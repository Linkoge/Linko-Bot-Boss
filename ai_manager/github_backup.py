"""Application-owned, non-forcing backup: deploy receipts pin targets for deployable projects."""
import asyncio
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager, nullcontext
from urllib.parse import urlsplit

from .git_commands import GitError, git_bytes

RETRY_SECONDS = 300
PREFIX = 'github_backup:'


@contextmanager
def token_auth(token):
    """Credential stays in the network child's environment, never a URL, argument or Git config file."""
    with tempfile.TemporaryDirectory(prefix='linko-git-auth-') as directory:
        helper = os.path.join(directory, 'askpass')
        with open(helper, 'w') as stream:
            stream.write('#!/bin/sh\ncase "$1" in\n*Username*) printf "%s\\n" x-access-token;;\n'
                         '*) printf "%s\\n" "$LINKO_GITHUB_CREDENTIAL";;\nesac\n')
        os.chmod(helper, 0o700)
        yield {'GIT_ASKPASS': helper, 'LINKO_GITHUB_CREDENTIAL': token,
               'LC_ALL': 'C', 'GIT_TRACE': '0', 'GIT_TRACE_CURL': '0', 'GIT_CURL_VERBOSE': '0',
               'GIT_TRACE_PACKET': '0', 'GIT_TRACE_REDACT': '1',
               'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'credential.helper', 'GIT_CONFIG_VALUE_0': '',
               'GIT_CONFIG_KEY_1': 'credential.useHttpPath', 'GIT_CONFIG_VALUE_1': 'true'}


def github_remote(url):
    if url.startswith('git@github.com:'):
        return True
    parsed = urlsplit(url)
    return parsed.scheme in {'https', 'ssh'} and parsed.hostname == 'github.com'


class GitHubBackup:
    def __init__(self, db, registry, has_setup=None):
        self.db, self.registry = db, registry
        self.job, self.started = None, False
        self.credentials = None
        self.has_setup = has_setup or (lambda project_id: self.db.setting('deploy_setup:' + project_id) is not None)

    def offline(self, project_id):
        return self.db.setting('github_tier:' + project_id) == 'offline'

    def request(self, project_id, commit=None, conn=None):
        if self.offline(project_id):
            return  # offline projects are never backed up: no state, no retry
        # setup() may migrate the installed runtime and write settings itself. Resolve it
        # before taking the backup write transaction to avoid a nested SQLite writer.
        waiting = commit is None and self.has_setup(project_id)
        with (nullcontext(conn) if conn is not None else self.db.transaction()) as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project_id,)).fetchone()
            previous = json.loads(row['value']) if row else {}
            value = {**previous, 'state': 'waiting-for-deploy' if waiting else 'pending',
                     'target_commit': commit, 'retry_at': 0,
                     'generation': previous.get('generation', 0) + 1}
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (PREFIX + project_id, json.dumps(value)))

    def tick(self):
        # Existing backlog uses the same deploy gate as new merges.
        if not self.started:
            self.started = True
            for project in self.registry.list():
                if project.get('github_tier') != 'offline' and not self.db.setting(PREFIX + project['id']):
                    self.request(project['id'])
        if self.job and not self.job.done():
            return
        for project in self.registry.list():
            if project.get('github_tier') == 'offline':
                continue
            state = self.db.setting(PREFIX + project['id'], {})
            # Old pending receipts predate deploy gating and have no verified target.
            if state.get('state') in {'pending', 'needs-attention'} and not state.get('target_commit') and self.has_setup(project['id']):
                self.request(project['id'])
                continue
            if state.get('state') in {'pending', 'needs-attention'} and state.get('retry_at', 0) <= time.time():
                self.job = asyncio.create_task(self.attempt(project, state))
                return

    async def attempt(self, project, state):
        if project.get('github_tier') == 'offline' or self.offline(project['id']):
            return
        if not state.get('target_commit') and self.has_setup(project['id']):
            self.request(project['id'])
            return
        try:
            if project.get('github_tier'):
                try:
                    if self.credentials is None:
                        raise ValueError('GitHub settings unavailable')
                    token = self.credentials(project)
                except ValueError:
                    result = {'state': 'needs-attention', 'reason': 'open-github-settings-for-tier-token'}
                else:
                    result = await asyncio.to_thread(self.push_with_token, project['path'], token,
                                                     **({'commit': state['target_commit']} if state.get('target_commit') else {}))
            else:
                result = await asyncio.to_thread(self.push, project['path'],
                                                 **({'commit': state['target_commit']} if state.get('target_commit') else {}))
        except Exception:
            # Git/SSH and optional hooks can echo credentials. Never store their error text.
            result = {'state': 'needs-attention', 'reason': 'backup-operation-failed'}
        with self.db.transaction() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project['id'],)).fetchone()
            current = json.loads(row['value']) if row else {}
            if current.get('generation') != state.get('generation'):
                # A merge arrived while this immutable snapshot was being checked/pushed.
                result = {**current, **({'commit': result['commit']} if result.get('state') == 'synced' else {})}
            else:
                result = {**{k: current[k] for k in ('commit', 'target_commit') if k in current}, **result}
                result['retry_at'] = time.time() + RETRY_SECONDS if result['state'] == 'needs-attention' else 0
            result |= {'generation': current.get('generation', 0), 'attempted_at': time.time()}
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (PREFIX + project['id'], json.dumps(result)))
            self.db.event(conn, 'github_backup.result', result, project['id'])

    @staticmethod
    def push_with_token(root, token, commit=None):
        with token_auth(token) as env:
            return GitHubBackup.push(root, env, commit=commit)

    @staticmethod
    def push(root, auth_env=None, commit=None):
        def git(*args):
            network = args[0] in {'ls-remote', 'fetch', 'push'}
            return git_bytes(root, *args, **({'env': auth_env} if network and auth_env else {})).decode().strip()

        # Non-Git projects keep their existing in-place behavior.
        if git_bytes(root, 'rev-parse', '--git-dir', check=False).returncode:
            return {'state': 'unavailable', 'reason': 'not-a-git-repository'}
        try:
            if git('rev-parse', '--is-shallow-repository') == 'true':
                return {'state': 'needs-attention', 'reason': 'repository-history-incomplete'}
            remote = git('remote', 'get-url', '--push', 'origin')
            if not github_remote(remote):
                return {'state': 'needs-attention', 'reason': 'configure-github-origin'}
            # Push exactly one destination: refuse ambiguous multi-pushurl configurations.
            if len(git('remote', 'get-url', '--push', '--all', 'origin').splitlines()) != 1:
                return {'state': 'needs-attention', 'reason': 'configure-one-github-origin'}
            if auth_env:
                match = re.fullmatch(r'(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)'
                                     r'([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?', remote)
                if not match:
                    return {'state': 'needs-attention', 'reason': 'configure-github-origin'}
                remote = 'https://github.com/' + match[1] + '.git'
            branch = 'main'
            if git_bytes(root, 'show-ref', '--verify', '--quiet', 'refs/heads/main', check=False).returncode:
                branch = 'master'
            local_head = git_bytes(root, 'rev-parse', '--verify', 'refs/heads/' + branch, check=False)
            if local_head.returncode:
                # An untouched, unborn repository has nothing to publish. Never manufacture a commit,
                # or call it synced if another local ref, uncommitted work or remote history exists.
                unborn = git('symbolic-ref', '--short', 'HEAD')
                if git('for-each-ref', '--format=%(refname)') or unborn not in {'main', 'master'}:
                    return {'state': 'needs-attention', 'reason': 'missing-main-branch'}
                if git('ls-remote', '--refs', remote):
                    return {'state': 'needs-attention', 'reason': 'local-history-missing'}
                if git('status', '--porcelain'):
                    return {'state': 'needs-attention', 'reason': 'uncommitted-files'}
                return {'state': 'synced', 'commit': None, 'reason': 'empty-repository'}
            # Pin the receipt object even if main has advanced or rolled back.
            head = git('rev-parse', '--verify', commit + '^{commit}') if commit else local_head.stdout.decode().strip()
            # Query the actual push URL, not an occasionally stale origin tracking ref or fetch URL.
            remote_head = git('ls-remote', '--refs', remote, 'refs/heads/' + branch)
            base = remote_head.split()[0] if remote_head else None
            if base:
                git('fetch', '--quiet', '--no-tags', '--recurse-submodules=no', remote, 'refs/heads/' + branch)
                if git_bytes(root, 'merge-base', '--is-ancestor', base, head, check=False).returncode:
                    return {'state': 'needs-attention', 'reason': 'remote-history-diverged'}
            if base != head:
                # Explicit object/ref pair includes the entire backlog. Never force/reset/rebase.
                git('push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
                    remote, head + ':refs/heads/' + branch)
            return {'state': 'synced', 'commit': head, 'reason': 'backlog-verified'}
        except GitError:
            return {'state': 'needs-attention', 'reason': 'git-push-or-verification-failed'}

    async def close(self):
        if self.job:
            await asyncio.gather(self.job, return_exceptions=True)

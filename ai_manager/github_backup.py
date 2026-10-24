"""Application-owned, non-forcing backup: deploy receipts pin targets for deployable projects."""
import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager, nullcontext
from urllib.parse import urlsplit

from .db import new_id
from .git_commands import GitError, git_bytes
from .push_diagnosis import diagnose, precheck

RETRY_SECONDS = 300
PREFIX = 'github_backup:'
BYPASS_PREFIX = 'github_bypass:'  # per project: blocked-commit keys already bypassed or reported (never secret values)
TEST_PATH = re.compile(r'(?:^|/)(?:tests?|__tests__|spec|specs|fixtures?|testdata)/|(?:^|/)test_[^/]*$|'
                       r'_test\.[A-Za-z]+$|\.(?:test|spec)\.[A-Za-z]+$')
UNBLOCK_ID = re.compile(r'https://github\.com/([\w.-]+/[\w.-]+)/security/secret-scanning/unblock-secret/(\w+)')
BYPASS_KEEP = 50


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
        self.manual = set()  # projects with an owner-requested push running; the background job stays out
        self.has_setup = has_setup or (lambda project_id: self.db.setting('deploy_setup:' + project_id) is not None)

    def offline(self, project_id):
        return self.db.setting('github_tier:' + project_id) == 'offline'

    def request(self, project_id, commit=None, conn=None):
        if self.offline(project_id):
            return  # offline projects are never backed up: no state, no retry
        # Setup discovery can persist the installed runtime migration. Do it before taking a SQLite write lock.
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
        if (self.job and not self.job.done()) or self.manual:
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
        target = state.get('target_commit')
        if state.get('unverified') and state.get('commit') and target and target != state['commit'] \
                and await asyncio.to_thread(self.contains, project['path'], target, state['commit']):
            # The owner already pushed a newer main; an older deploy must not be pushed over it.
            result = {'state': 'synced', 'commit': state['commit'], 'reason': 'pushed-before-verification',
                      'unverified': True, 'unverified_at': state.get('unverified_at')}
        else:
            result = await self.run_push(project, target)
        with self.db.transaction() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project['id'],)).fetchone()
            current = json.loads(row['value']) if row else {}
            if current.get('generation') != state.get('generation'):
                # A merge arrived while this immutable snapshot was being checked/pushed.
                synced = result.get('state') == 'synced'
                result = {**{k: v for k, v in current.items() if not (synced and k in ('unverified', 'unverified_at')
                                                                      and not result.get('unverified'))},
                          **({'commit': result['commit']} if synced else {})}
            else:
                keep = ('commit', 'target_commit') + (('unverified', 'unverified_at') if result['state'] != 'synced' else ())
                result = {**{k: current[k] for k in keep if k in current}, **result}
                result['retry_at'] = time.time() + RETRY_SECONDS if result['state'] == 'needs-attention' else 0
            result |= {'generation': current.get('generation', 0), 'attempted_at': time.time()}
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (PREFIX + project['id'], json.dumps(result)))
            self.db.event(conn, 'github_backup.result', result, project['id'])

    async def run_push(self, project, commit=None, unverified=False):
        """One push path for the background job and the owner's manual override."""
        extra = {**({'commit': commit} if commit else {}), **({'unverified': True} if unverified else {})}
        try:
            if project.get('github_tier'):
                try:
                    if self.credentials is None:
                        raise ValueError('GitHub settings unavailable')
                    token = self.credentials(project)
                except ValueError:
                    return {'state': 'needs-attention', 'reason': 'open-github-settings-for-tier-token'}
                result = await asyncio.to_thread(self.push_with_token, project['path'], token, **extra)
                if result.get('reason') == 'github-push-protection':
                    result = await self.recover_push_protection(project, token, result, extra)
                return result
            result = await asyncio.to_thread(self.push, project['path'], **extra)
            if result.get('reason') == 'github-push-protection':
                result = await self.recover_push_protection(project, None, result, extra)
            return result
        except Exception:
            # Unexpected failure: Git/SSH and hooks can echo credentials, so store no error text at all.
            return {'state': 'needs-attention', 'reason': 'backup-operation-failed'}

    @staticmethod
    def blocked(result):
        """(hits, urls, key) of a push-protection result; key identifies the blocked commit set, never a value."""
        detail = result.get('detail') or {}
        hits = [{'type': item['type'], 'path': loc['path'], 'line': loc['line'], 'commit': loc['commit']}
                for item in detail.get('secrets', []) for loc in item['locations']]
        commits = sorted({str(h['commit']) for h in hits}) or sorted(detail.get('unblock_urls', []))
        return hits, detail.get('unblock_urls', []), hashlib.sha256('|'.join(commits).encode()).hexdigest()[:16]

    def bypass_api(self, token, repo, placeholder):
        from .github_projects import API, urllib_fetch
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'linko-bot-boss',
                   'Content-Type': 'application/json'}
        body = json.dumps({'reason': 'used_in_tests', 'placeholder_id': placeholder}).encode()
        fetch = getattr(self, 'fetch', None) or urllib_fetch
        return fetch('POST', f'{API}/repos/{repo}/secret-scanning/push-protection-bypasses', headers, body, 30)[0]

    async def recover_push_protection(self, project, token, result, extra):
        """Bypass push protection once per blocked commit set when every hit is in test paths; else tell the owner."""
        hits, urls, key = self.blocked(result)
        project_id = project['id']
        state = self.db.setting(BYPASS_PREFIX + project_id, {})
        done, noticed = state.get('attempted', []), state.get('noticed', [])
        if key in done:
            return self.notify_blocked(project_id, key, hits, result, noticed)
        found = [UNBLOCK_ID.fullmatch(url) for url in urls]
        eligible = bool(token and hits and urls and all(found) and all(TEST_PATH.search(h['path']) for h in hits))
        if not eligible:
            return self.notify_blocked(project_id, key, hits, result, noticed)
        self.db.set_setting(BYPASS_PREFIX + project_id, {**state, 'attempted': (done + [key])[-BYPASS_KEEP:]})
        try:
            statuses = [await asyncio.to_thread(self.bypass_api, token, m[1], m[2]) for m in found]
        except Exception:
            statuses = [0]
        if not all(code in (200, 201) for code in statuses):
            result = {**result, 'bypass': {'attempted': True, 'ok': False, 'reason': 'used_in_tests', 'status': max(statuses)}}
            return self.notify_blocked(project_id, key, hits, result, noticed, failed=True)
        record = {'attempted': True, 'ok': True, 'reason': 'used_in_tests', 'hits': hits[:20], 'at': time.time()}
        retried = await asyncio.to_thread(self.push_with_token, project['path'], token, **extra)
        return {**retried, 'bypass': record}

    def notify_blocked(self, project_id, key, hits, result, noticed, failed=False):
        """One short owner-visible chat notice per blocked commit set; the needs-attention receipt is unchanged."""
        if key not in noticed:
            places = ', '.join(f"{h['path']}:{h['line']}" if h['line'] else h['path'] for h in hits[:5]) or 'unknown files'
            text = ('GitHub push protection blocked the backup push (' + places + (f' and {len(hits) - 5} more' if len(hits) > 5 else '')
                    + '). ' + ('The automatic test-fixture bypass failed or was not permitted. ' if failed else
                               'Not every hit is in a test path, so nothing was bypassed. ')
                    + 'Replace the key-shaped literals with values built at runtime.')
            with self.db.transaction() as conn:
                conversation = conn.execute('SELECT id FROM conversations WHERE context=?', (project_id,)).fetchone()
                conversation_id = conversation['id'] if conversation else new_id()
                if not conversation:
                    conn.execute('INSERT INTO conversations(id,context,project_id,created_at) VALUES(?,?,?,?)',
                                 (conversation_id, project_id, project_id, time.time()))
                conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                             "VALUES(?,?,'assistant',?,'completed',?)",
                             ('pushblock-' + hashlib.sha256((project_id + key).encode()).hexdigest()[:24],
                              conversation_id, text[:600], time.time()))
                current = conn.execute('SELECT value FROM settings WHERE key=?', (BYPASS_PREFIX + project_id,)).fetchone()
                current = json.loads(current['value']) if current else {}
                current['noticed'] = (current.get('noticed', []) + [key])[-BYPASS_KEEP:]
                conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (BYPASS_PREFIX + project_id, json.dumps(current)))
        return result

    @staticmethod
    def contains(root, ancestor, descendant):
        return git_bytes(root, 'merge-base', '--is-ancestor', ancestor, descendant, check=False).returncode == 0

    async def push_now(self, project_id):
        """Owner override: push the current main HEAD without waiting for the deploy gate. Never forces."""
        project = self.registry.get(project_id)
        if project.get('github_tier') == 'offline' or self.offline(project_id):
            raise ValueError('Offline projects are never pushed to GitHub')
        if project_id in self.manual:
            raise ValueError('A GitHub push is already running for this project')
        self.manual.add(project_id)
        try:
            if self.job and not self.job.done():
                await asyncio.gather(self.job, return_exceptions=True)
            result = await self.run_push(project, unverified=True)
            with self.db.transaction() as conn:
                row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project_id,)).fetchone()
                current = json.loads(row['value']) if row else {}
                if result.get('state') == 'synced' and result.get('commit'):
                    now = time.time()
                    receipt = {'state': 'synced', 'commit': result['commit'], 'reason': result['reason'],
                               'retry_at': 0, 'attempted_at': now, 'generation': current.get('generation', 0) + 1}
                    # A deploy already verified exactly this commit: nothing is unverified.
                    if not (current.get('state') in {'pending', 'needs-attention'}
                            and current.get('target_commit') == result['commit']):
                        receipt |= {'unverified': True, 'unverified_at': now}
                    conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                 (PREFIX + project_id, json.dumps(receipt)))
                    result = {**{k: v for k, v in result.items() if k != 'unverified'},
                              **{k: receipt[k] for k in ('unverified', 'unverified_at') if k in receipt}}
                self.db.event(conn, 'github_backup.manual', result, project_id)
            return result
        finally:
            self.manual.discard(project_id)

    @staticmethod
    def push_with_token(root, token, commit=None, unverified=False):
        with token_auth(token) as env:
            return GitHubBackup.push(root, env, commit=commit, unverified=unverified)

    @staticmethod
    def push(root, auth_env=None, commit=None, unverified=False):
        def git(*args):
            network = args[0] in {'ls-remote', 'fetch', 'push'}
            return git_bytes(root, *args, **({'env': auth_env} if network and auth_env else {})).decode().strip()

        warnings = []

        def failure(error):
            token = (auth_env or {}).get('LINKO_GITHUB_CREDENTIAL')
            return {'state': 'needs-attention', **diagnose(error.output, [token]),
                    **({'warnings': warnings} if warnings else {})}

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
                # Advisory only: GitHub's verdict decides, and this never blocks the push.
                try:
                    diff = git_bytes(root, 'log', '-p', '-U0', '--no-color', '--no-ext-diff', '--format=@@commit %H',
                                     (base + '..' if base else '') + head)[:5_000_000]
                    warnings.extend(precheck(diff))
                except GitError:
                    pass
                # Explicit object/ref pair includes the entire backlog. Never force/reset/rebase.
                git('push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
                    remote, head + ':refs/heads/' + branch)
            if unverified:
                return {'state': 'synced', 'commit': head, 'reason': 'pushed-before-verification', 'unverified': True}
            return {'state': 'synced', 'commit': head, 'reason': 'backlog-verified'}
        except GitError as error:
            return failure(error)

    async def close(self):
        if self.job:
            await asyncio.gather(self.job, return_exceptions=True)

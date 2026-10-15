"""Application-owned, non-forcing backup of main. Network work never holds the queue/checkout fence."""
import asyncio
import json
import time
from urllib.parse import urlsplit

from .secret_check import ScanError, git_bytes, scan

RETRY_SECONDS = 300
PREFIX = 'github_backup:'


def github_remote(url):
    if url.startswith('git@github.com:'):
        return True
    parsed = urlsplit(url)
    return parsed.scheme in {'https', 'ssh'} and parsed.hostname == 'github.com'


class GitHubBackup:
    def __init__(self, db, registry):
        self.db, self.registry = db, registry
        self.job, self.started = None, False

    def request(self, project_id):
        with self.db.transaction() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project_id,)).fetchone()
            previous = json.loads(row['value']) if row else {}
            value = {**previous, 'state': 'pending', 'retry_at': 0,
                     'generation': previous.get('generation', 0) + 1}
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (PREFIX + project_id, json.dumps(value)))

    def tick(self):
        # A fresh installation also verifies and backs up the existing local backlog, without a new merge.
        if not self.started:
            self.started = True
            for project in self.registry.list():
                if not self.db.setting(PREFIX + project['id']):
                    self.request(project['id'])
        if self.job and not self.job.done():
            return
        for project in self.registry.list():
            state = self.db.setting(PREFIX + project['id'], {})
            if state.get('state') in {'pending', 'needs-attention'} and state.get('retry_at', 0) <= time.time():
                self.job = asyncio.create_task(self.attempt(project, state))
                return

    async def attempt(self, project, state):
        try:
            result = await asyncio.to_thread(self.push, project['path'])
        except Exception:
            # Git/SSH and optional hooks can echo credentials. Never store their error text.
            result = {'state': 'needs-attention', 'reason': 'backup-operation-failed'}
        with self.db.transaction() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + project['id'],)).fetchone()
            current = json.loads(row['value']) if row else {}
            if current.get('generation') != state.get('generation'):
                # A merge arrived while this immutable snapshot was being checked/pushed.
                result = {**result, 'state': 'pending', 'retry_at': 0}
            else:
                result['retry_at'] = time.time() + RETRY_SECONDS if result['state'] == 'needs-attention' else 0
            result |= {'generation': current.get('generation', 0), 'attempted_at': time.time()}
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (PREFIX + project['id'], json.dumps(result)))
            self.db.event(conn, 'github_backup.result', result, project['id'])

    @staticmethod
    def push(root):
        def git(*args):
            return git_bytes(root, *args).decode().strip()

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
            branch = 'main'
            if git_bytes(root, 'show-ref', '--verify', '--quiet', 'refs/heads/main', check=False).returncode:
                branch = 'master'
            head = git('rev-parse', 'refs/heads/' + branch)
            # Query the actual push URL, not an occasionally stale origin tracking ref or fetch URL.
            remote_head = git('ls-remote', '--refs', remote, 'refs/heads/' + branch)
            base = remote_head.split()[0] if remote_head else None
            if base:
                git('fetch', '--quiet', '--no-tags', '--recurse-submodules=no', remote, 'refs/heads/' + branch)
                if git_bytes(root, 'merge-base', '--is-ancestor', base, head, check=False).returncode:
                    return {'state': 'needs-attention', 'reason': 'remote-history-diverged'}
            hits = scan(root, base, head)
            if hits:
                return {'state': 'needs-attention', 'reason': 'secret-check', 'findings': hits[:30]}
            if base != head:
                # Explicit object/ref pair includes the entire safe backlog. Never force/reset/rebase.
                git('push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no',
                    remote, head + ':refs/heads/' + branch)
            return {'state': 'synced', 'commit': head, 'reason': 'backlog-verified'}
        except ScanError:
            return {'state': 'needs-attention', 'reason': 'git-push-or-verification-failed'}

    async def close(self):
        if self.job:
            await asyncio.gather(self.job, return_exceptions=True)

"""Bot Boss's own version, the other machines' versions, and auto-update from GitHub for non-Main machines.

Version: derived from git at startup, never bumped by hand: `YYYY.MM.DD.N (hash)`, where the date is the HEAD
commit's date (UTC) and N is `git rev-list --count HEAD`. Machines with the same main history compare by N.

Auto-update (off on Main, where task merges already deploy; on by default elsewhere; never on a restricted instance):
every CHECK_SECONDS fetch origin's main into refs/remotes/origin/main; when main moved ahead, the checkout is on main
and clean, and nothing runs, fast-forward and queue the existing deploy (tests, health check, rollback). Never force,
reset or commit; local changes or a diverged main are skipped and reported.
"""
import asyncio
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import fence

ROOT = Path(__file__).resolve().parents[1]
ROLE_ENV = 'AI_MANAGER_MACHINE_ROLE'
KEY = 'self_update'  # settings row: {'enabled': bool} plus the last check's status
CHECK_SECONDS = 1800
FIRST_CHECK_SECONDS = 120
FETCH_SECONDS = 60
PEER_SECONDS = 3
UNKNOWN = {'version': 'unknown', 'commit': '', 'commit_count': 0}
BUSY = ("SELECT 1 FROM executions WHERE finished_at IS NULL UNION ALL "
        "SELECT 1 FROM tasks WHERE state='running' OR lease_owner IS NOT NULL UNION ALL "
        "SELECT 1 FROM messages WHERE status IN ('queued','streaming') LIMIT 1")


def git(root, *args, timeout=FETCH_SECONDS):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL,
                            env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC', 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed: ' + (result.stderr or result.stdout).strip()[-200:])
    return result.stdout.strip()


def format_version(date, count, commit):
    """'2026.11.28.1873' from a YYYY-MM-DD date and the commit count; the display adds ' (c4fe69b)'."""
    return f"{date.replace('-', '.')}.{int(count)}", commit[:7]


def version_info(root=ROOT, ref='HEAD'):
    """{'version', 'commit', 'commit_count'} of ref; UNKNOWN outside a git checkout."""
    try:
        commit = git(root, 'rev-parse', ref, timeout=10)
        date = git(root, 'log', '-1', '--format=%cd', '--date=format-local:%Y-%m-%d', ref, timeout=10)
        count = int(git(root, 'rev-list', '--count', ref, timeout=10))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return dict(UNKNOWN)
    version, short = format_version(date, count, commit)
    return {'version': version, 'commit': short, 'commit_count': count}


def label(version):
    return f"{version['version']} ({version['commit']})" if version.get('commit') else version.get('version', 'unknown')


def is_main(registry=None, root=ROOT):
    """Main: AI_MANAGER_MACHINE_ROLE=main, or (role unset) this checkout is a registered project here, i.e. this
    machine merges and deploys Bot Boss itself. Any other role value marks a non-Main machine."""
    role = os.getenv(ROLE_ENV, '').strip().lower()
    if role:
        return role == 'main'
    if registry is None:
        return False
    try:
        return any(Path(p['path']).resolve() == Path(root).resolve() for p in registry.list())
    except (OSError, KeyError, TypeError):
        return False


def compare(mine, other):
    """A peer's label against this machine: latest / behind (by N) / offline."""
    if not isinstance(other, dict) or not isinstance(other.get('commit_count'), int):
        return {'state': 'offline'}
    out = {'state': 'latest', 'version': str(other.get('version', ''))[:40], 'commit': str(other.get('commit', ''))[:12],
           'commit_count': other['commit_count']}
    behind = mine.get('commit_count', 0) - other['commit_count']
    if behind > 0:
        out.update(state='behind', behind=behind)
    return out


def fetch_peer(url, timeout=PEER_SECONDS):
    """GET <url>/api/version; None when unreachable or not a Bot Boss answer."""
    try:
        request = urllib.request.Request(url.rstrip('/') + '/api/version', headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read(4096))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def peers(mine, machines, fetch=fetch_peer):
    """Each saved machine's version label, fetched in parallel with a short timeout."""
    if not machines:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(machines))) as pool:
        answers = list(pool.map(lambda m: fetch(m['url']), machines))
    return [{'id': m['id'], **compare(mine, answer)} for m, answer in zip(machines, answers)]


def decide(*, enabled, branch, dirty, local, remote, fast_forward, busy):
    """The auto-update decision after a fetch: (state, reason); state 'update' means pull and deploy now."""
    if not enabled:
        return 'off', 'Auto-update is off.'
    if branch != 'main':
        return 'skipped', f"Checkout is on '{branch or 'detached HEAD'}', not main; update skipped."
    if dirty:
        return 'skipped', 'Checkout has local changes; update skipped (nothing was changed).'
    if local == remote:
        return 'latest', 'Up to date with GitHub.'
    if not fast_forward:
        return 'skipped', 'Local main has commits GitHub does not have; update skipped.'
    if busy:
        return 'busy', 'Work is running; update deferred to the next check.'
    return 'update', 'Pulling GitHub updates.'


class SelfUpdate:
    def __init__(self, db, registry, pipeline, restricted=False, root=ROOT, version=None, clock=time.time):
        self.db, self.registry, self.pipeline, self.restricted = db, registry, pipeline, restricted
        self.root, self.clock = Path(root), clock
        self.version = version or version_info(root)
        self.due = clock() + FIRST_CHECK_SECONDS
        self.job = None

    def main(self):
        return is_main(self.registry, self.root)

    def enabled(self):
        if self.restricted:
            return False
        saved = self.db.setting(KEY, {})
        return saved['enabled'] if isinstance(saved.get('enabled'), bool) else not self.main()

    def status(self):
        saved = self.db.setting(KEY, {})
        return {**self.version, 'label': label(self.version), 'main': self.main(), 'enabled': self.enabled(),
                'available': not self.restricted, 'checking': bool(self.job and not self.job.done()),
                **{k: saved.get(k) for k in ('checked_at', 'updated_at', 'state', 'reason', 'remote_count')}}

    def set_enabled(self, value):
        if self.restricted:
            raise ValueError('Not available on a restricted instance')
        if not isinstance(value, bool):
            raise ValueError('enabled must be true or false')
        self.db.set_setting(KEY, {**self.db.setting(KEY, {}), 'enabled': value})
        return self.status()

    def busy(self):
        return self.pipeline.pending() or bool(self.db.one(BUSY))

    def checkout(self):
        """The deployed checkout: the installed runtime's repo, which must be this running code's checkout."""
        installed = (self.pipeline.root / 'installed.json')
        try:
            repo = json.loads(installed.read_text()).get('repo')
        except (OSError, ValueError, AttributeError):
            repo = None
        if not repo:
            return None, 'Deploy is not installed on this machine; auto-update needs it.'
        if Path(repo).resolve() != self.root.resolve():
            return None, 'The installed deploy runs another checkout; update skipped.'
        return Path(repo), None

    def check(self, manual=False):
        """One fetch-and-decide round; records and returns the status. manual=True works even while auto is off."""
        enabled = self.enabled() or (manual and not self.restricted)
        if not enabled:
            return self.record('off', 'Auto-update is off.' if not self.restricted else 'Not available here.')
        root, problem = self.checkout()
        if problem:
            return self.record('skipped', problem)
        try:
            git(root, 'fetch', '--quiet', '--no-tags', '--recurse-submodules=no', 'origin',
                '+refs/heads/main:refs/remotes/origin/main')
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            return self.record('failed', 'GitHub fetch failed: ' + str(error)[:200])
        try:
            with fence.hold(self.db.path.parent, exclusive=True, wait=5):
                return self.apply(root, enabled)
        except fence.Busy:
            return self.record('busy', 'Checkout is busy; update deferred to the next check.')
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            return self.record('failed', 'Update failed: ' + str(error)[:200])

    def apply(self, root, enabled):
        branch = subprocess.run(['git', '-C', str(root), 'symbolic-ref', '-q', '--short', 'HEAD'],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        local, remote = git(root, 'rev-parse', 'HEAD'), git(root, 'rev-parse', 'refs/remotes/origin/main')
        forward = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', local, remote],
                                 capture_output=True, timeout=10).returncode == 0
        remote_count = version_info(root, 'refs/remotes/origin/main')['commit_count']
        state, reason = decide(enabled=enabled, branch=branch, dirty=bool(git(root, 'status', '--porcelain', '--untracked-files=no')),
                               local=local, remote=remote, fast_forward=forward, busy=self.busy())
        if state != 'update':
            return self.record(state, reason, remote_count=remote_count)
        git(root, 'merge', '--ff-only', '--quiet', remote)
        self.pipeline.request_runtime('self-update', 'Auto-update from GitHub')
        return self.record('updated', f'Pulled {remote[:7]} from GitHub; deploy queued (tests, health check, rollback).',
                           remote_count=remote_count, updated_at=self.clock())

    def record(self, state, reason, **extra):
        saved = {**self.db.setting(KEY, {}), 'state': state, 'reason': reason, 'checked_at': self.clock(), **extra}
        self.db.set_setting(KEY, saved)
        return self.status()

    def tick(self):
        """From the runner loop: start a background check when due. Inert on restricted instances and when off."""
        if self.restricted or self.clock() < self.due or (self.job and not self.job.done()):
            return
        self.due = self.clock() + CHECK_SECONDS
        if self.enabled():
            self.job = asyncio.create_task(asyncio.to_thread(self.check))

    async def run_now(self):
        if self.job and not self.job.done():
            await asyncio.shield(self.job)
            return self.status()
        self.due = self.clock() + CHECK_SECONDS
        self.job = asyncio.create_task(asyncio.to_thread(self.check, True))
        return await asyncio.shield(self.job)

    async def close(self):
        if self.job:
            await asyncio.gather(self.job, return_exceptions=True)

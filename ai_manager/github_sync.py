"""Bounded, fast-forward-only incoming GitHub updates through the existing deploy queue."""
import asyncio
import re
import time
from contextlib import nullcontext

from . import fence
from .git_commands import GitError, git_bytes
from .github_backup import github_remote, token_auth
from .push_diagnosis import OPERATIONS, diagnose_incoming, redact

PREFIX = 'github_sync:'
FETCH_ATTEMPTS = 2
MAX_PATHS = 5


def dirty_paths(root):
    """Bounded, redacted names of changed paths (never contents), for the dirty-checkout warning."""
    entries = git_bytes(root, 'status', '--porcelain', '-z').decode('utf-8', 'replace').split('\0')
    paths, skip = [], False
    for entry in entries:
        if skip:
            skip = False
        elif entry:
            skip = entry[0] in 'RC'  # the next entry is the rename/copy source
            paths.append(redact(re.sub(r'[\x00-\x1f\x7f]', '?', entry[3:]), generic=False)[:80])
    return paths


def dirty_warning(paths):
    shown = ', '.join(paths[:MAX_PATHS])
    more = f' and {len(paths) - MAX_PATHS} more' if len(paths) > MAX_PATHS else ''
    return {'state': 'warning', 'cause': 'uncommitted-changes', 'paths': paths[:MAX_PATHS], 'path_count': len(paths),
            'reason': f'Uncommitted changes in {shown}{more}; update skipped, local files left untouched.'
                      if paths else 'Uncommitted changes; update skipped.'}


def failure(operation, error, secrets=(), attempts=1):
    """One persisted shape for every failed incoming step: which operation failed and the redacted cause."""
    if isinstance(error, GitError):
        found = diagnose_incoming(operation, error.output, secrets, attempts)
    else:
        text = redact(str(error), secrets)[:200]
        found = {'cause': 'unexpected-error', 'excerpt': [],
                 'message': f'GitHub {OPERATIONS.get(operation, operation)} failed unexpectedly ({type(error).__name__})' + (f': {text}' if text else '.')}
    return {'state': 'warning', 'operation': operation, 'cause': found['cause'], 'reason': found['message'],
            'excerpt': found['excerpt']}


class GitHubSync:
    def __init__(self, pipeline, interval=300):
        self.pipeline = pipeline
        self.db, self.registry = pipeline.db, pipeline.registry
        self.interval = max(10, interval)
        self.jobs, self.prepared, self.due = {}, {}, {}

    def status(self, project_id):
        return self.db.setting(PREFIX + project_id, {})

    def ready(self, project_id, ticket):
        """Called before claiming work; the fetch runs off the scheduler thread."""
        if self.pipeline.pending():
            return False
        project = self.registry.get(project_id)
        if self.pipeline.restricted or self.pipeline.backups.offline(project_id):
            return True
        try:
            remote = git_bytes(project['path'], 'remote', 'get-url', 'origin').decode().strip()
        except GitError:
            return True
        if not github_remote(remote):
            return True
        job = self.jobs.get(project_id)
        if job and not job.done():
            return False
        if self.prepared.get(project_id) == ticket:
            return True
        self.start(project_id, preparing=ticket)
        return False

    def consumed(self, project_id):
        self.prepared.pop(project_id, None)

    def start(self, project_id, preparing=None):
        job = self.jobs.get(project_id)
        if job and not job.done():
            return job
        self.jobs[project_id] = asyncio.create_task(self.attempt(project_id, preparing))
        return self.jobs[project_id]

    def tick(self):
        if self.pipeline.restricted or self.pipeline.pending():
            return
        for project in self.registry.list():
            pid = project['id']
            if (project.get('github_tier') != 'offline' and time.time() >= self.due.get(pid, 0)
                    and not self.db.one("SELECT 1 FROM tasks WHERE project_id=? AND state='running'", (pid,))):
                self.start(pid)

    async def attempt(self, project_id, preparing=None):
        try:
            result = await asyncio.to_thread(self.pull, project_id)
        except Exception as error:
            result = failure('update', error)
        self.db.set_setting(PREFIX + project_id, {**result, 'attempted_at': time.time()})
        self.due[project_id] = time.time() + self.interval
        if preparing is not None and result.get('state') != 'busy':
            self.prepared[project_id] = preparing
        return result

    def pull(self, project_id):
        project = self.registry.get(project_id)
        if self.pipeline.restricted or self.pipeline.backups.offline(project_id):
            return {'state': 'excluded', 'reason': 'Offline or restricted project; no GitHub sync.'}
        root = self.registry.allowed_path(project['path'])
        def git(*args, **kwargs):
            return git_bytes(root, *args, **kwargs).decode().strip()
        try:
            remote = git('remote', 'get-url', 'origin')
        except GitError:
            return {'state': 'excluded', 'reason': 'No GitHub origin.'}
        if not github_remote(remote):
            return {'state': 'excluded', 'reason': 'No GitHub origin.'}
        step, token, attempts = 'inspect', None, 1
        try:
            state = self.status(project_id)
            # Preserve the divergence latch even while work is active or the checkout is dirty.
            if state.get('state') == 'diverged':
                local = git('rev-parse', 'main')
                remote_head = git('rev-parse', 'refs/remotes/origin/main')
                if (git_bytes(root, 'merge-base', '--is-ancestor', local, remote_head, check=False).returncode
                        and git_bytes(root, 'merge-base', '--is-ancestor', remote_head, local, check=False).returncode):
                    return state
            if self.pipeline.pending() or self.db.one(
                    "SELECT 1 FROM tasks WHERE project_id=? AND state='running'", (project_id,)):
                return {'state': 'busy', 'reason': 'Work or deploy is active; update deferred.'}
            if git('status', '--porcelain'):
                return dirty_warning(dirty_paths(root))
            token = self.pipeline.backups.credentials(project) if project.get('github_tier') else None
            # Fetch off the checkout fence: an unreachable host cannot hold other projects' queues.
            destination = remote
            if token:
                if remote.startswith('git@github.com:'):
                    destination = 'https://github.com/' + remote.split(':', 1)[1]
                elif remote.startswith('ssh://git@github.com/'):
                    destination = 'https://github.com/' + remote.split('github.com/', 1)[1]
            step = 'fetch'
            with (token_auth(token) if token else nullcontext(None)) as env:
                for attempt in range(FETCH_ATTEMPTS):
                    attempts = attempt + 1
                    try:
                        git('fetch', '--quiet', '--no-tags', '--recurse-submodules=no', destination,
                            'refs/heads/main:refs/remotes/origin/main', env=env)
                        break
                    except GitError:
                        if attempts == FETCH_ATTEMPTS:
                            raise
                step, attempts = 'refs', 1
            with fence.hold(self.db.path.parent, exclusive=True):
                if self.pipeline.pending() or self.db.one(
                        "SELECT 1 FROM tasks WHERE project_id=? AND state='running'", (project_id,)):
                    return {'state': 'busy', 'reason': 'Work or deploy is active; update deferred.'}
                if git('status', '--porcelain'):
                    return dirty_warning(dirty_paths(root))
                branch = git_bytes(root, 'symbolic-ref', '--short', 'HEAD', check=False)
                if branch.stdout.decode().strip() != 'main':
                    current = redact(branch.stdout.decode().strip(), generic=False)[:80]
                    return {'state': 'warning', 'cause': 'not-on-main',
                            'reason': (f"Checkout is on '{current}', not main; update skipped." if current
                                       else 'Checkout has a detached HEAD, not main; update skipped.')}
                local, target = git('rev-parse', 'main'), git('rev-parse', 'refs/remotes/origin/main')
                if local == target:
                    return {'state': 'synced', 'reason': 'Up to date with GitHub.'}
                if git_bytes(root, 'merge-base', '--is-ancestor', target, local, check=False).returncode == 0:
                    return {'state': 'ahead', 'reason': 'Local main is ahead; waiting for verified GitHub push.'}
                if git_bytes(root, 'merge-base', '--is-ancestor', local, target, check=False).returncode:
                    return {'state': 'diverged', 'local': local, 'remote': target,
                            'reason': 'Out of sync with GitHub, needs attention: both main branches have moved.'}
                step = 'merge'
                git('merge', '--ff-only', '--no-overwrite-ignore', target)
                step = 'deploy'
                deployment = self.pipeline._request(project_id, 'github-sync', None, None, 'Pulled GitHub updates')
                return {'state': 'updated', 'commit': target, 'reason': 'Pulled GitHub updates; ' +
                        ('deploy queued.' if deployment.get('id') else 'no deploy setup.')}
        except fence.Busy:
            return {'state': 'busy', 'reason': 'Checkout is busy; update deferred.'}
        except Exception as error:
            return failure(step, error, [token], attempts)

    async def close(self):
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

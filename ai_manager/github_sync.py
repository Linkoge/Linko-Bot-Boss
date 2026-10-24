"""Bounded, fast-forward-only incoming GitHub updates through the existing deploy queue."""
import asyncio
import time
from contextlib import nullcontext

from . import fence
from .git_commands import GitError, git_bytes
from .github_backup import github_remote, token_auth

PREFIX = 'github_sync:'


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
        except Exception:
            result = {'state': 'warning', 'reason': 'GitHub update failed; work can continue. Retry later.'}
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
                return {'state': 'warning', 'reason': 'Uncommitted changes; update skipped.'}
            token = self.pipeline.backups.credentials(project) if project.get('github_tier') else None
            # Fetch off the checkout fence: an unreachable host cannot hold other projects' queues.
            destination = remote
            if token:
                if remote.startswith('git@github.com:'):
                    destination = 'https://github.com/' + remote.split(':', 1)[1]
                elif remote.startswith('ssh://git@github.com/'):
                    destination = 'https://github.com/' + remote.split('github.com/', 1)[1]
            with (token_auth(token) if token else nullcontext(None)) as env:
                for attempt in range(2):
                    try:
                        git('fetch', '--quiet', '--no-tags', '--recurse-submodules=no', destination,
                            'refs/heads/main:refs/remotes/origin/main', env=env)
                        break
                    except GitError:
                        if attempt:
                            raise
            with fence.hold(self.db.path.parent, exclusive=True):
                if self.pipeline.pending() or self.db.one(
                        "SELECT 1 FROM tasks WHERE project_id=? AND state='running'", (project_id,)):
                    return {'state': 'busy', 'reason': 'Work or deploy is active; update deferred.'}
                if git('status', '--porcelain'):
                    return {'state': 'warning', 'reason': 'Uncommitted changes; update skipped.'}
                if git('symbolic-ref', '--short', 'HEAD') != 'main':
                    return {'state': 'warning', 'reason': 'Checkout is not on main; update skipped.'}
                local, target = git('rev-parse', 'main'), git('rev-parse', 'refs/remotes/origin/main')
                if local == target:
                    return {'state': 'synced', 'reason': 'Up to date with GitHub.'}
                if git_bytes(root, 'merge-base', '--is-ancestor', target, local, check=False).returncode == 0:
                    return {'state': 'ahead', 'reason': 'Local main is ahead; waiting for verified GitHub push.'}
                if git_bytes(root, 'merge-base', '--is-ancestor', local, target, check=False).returncode:
                    return {'state': 'diverged', 'local': local, 'remote': target,
                            'reason': 'Out of sync with GitHub, needs attention: both main branches have moved.'}
                git('merge', '--ff-only', '--no-overwrite-ignore', target)
                deployment = self.pipeline._request(project_id, 'github-sync', None, None, 'Pulled GitHub updates')
                return {'state': 'updated', 'commit': target, 'reason': 'Pulled GitHub updates; ' +
                        ('deploy queued.' if deployment.get('id') else 'no deploy setup.')}
        except fence.Busy:
            return {'state': 'busy', 'reason': 'Checkout is busy; update deferred.'}
        except (GitError, ValueError):
            return {'state': 'warning', 'reason': 'GitHub fetch failed after bounded retries; work can continue.'}

    async def close(self):
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

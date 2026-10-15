"""Merge into main and the deploy request/result protocol. Contract: README.md.

The worker (core.py) merges a passed branch into main one at a time, then asks the single deploy script
(scripts/deploy.py) to deploy main. Per-project "Auto-deploy" ON means merge and deploy; OFF means neither.
The deploy script itself waits for idle, backs up, restarts, health-checks and rolls back; this module only
writes its request file and reports its short result. No AI call decides anything here.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import fence
from .fence import Busy, main_branch

SETTING = 'auto_deploy:'
SEED = 'auto_deploy_seed'
ROOT = Path(os.getenv('AI_MANAGER_REPO') or Path(__file__).resolve().parents[1]).resolve()  # live code checkout
IDENTITY = ['-c', 'user.name=Linko Bot Boss', '-c', 'user.email=bot-boss@linko.invalid']


class Conflict(ValueError):
    """The branch does not merge cleanly into main."""


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2))
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def enabled(conn, project_id):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (SETTING + project_id,)).fetchone()
    return bool(row and json.loads(row[0]).get('enabled'))


def put(conn, project_id, value):
    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (SETTING + project_id, json.dumps(value)))


def git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise ValueError('git ' + args[0] + ' failed: ' + (result.stderr or result.stdout).strip()[:300])
    return result.stdout.strip() if check else result


def merge_into_main(root, commit):
    """Merge one verified commit into the project's main branch; returns (head, changed). Never forces."""
    branch = main_branch(root)
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('project checkout has uncommitted changes')
    if git(root, 'symbolic-ref', '-q', '--short', 'HEAD', check=False).stdout.strip() != branch:
        git(root, 'checkout', '--quiet', branch)
    head = git(root, 'rev-parse', 'HEAD')
    if git(root, 'merge-base', '--is-ancestor', commit, head, check=False).returncode == 0:
        return head, False
    result = git(root, *IDENTITY, 'merge', '--no-edit', commit, check=False)
    if result.returncode:
        git(root, 'merge', '--abort', check=False)
        raise Conflict('merge conflict with main: ' + (result.stdout + result.stderr).strip()[-300:])
    return git(root, 'rev-parse', 'HEAD'), True


class Pipeline:
    def __init__(self, db, registry):
        self.db, self.registry, self.job = db, registry, None
        self.root = db.path.parent / 'deploy'

    # ---- settings -------------------------------------------------------------------------------------------
    def deployable(self, path):
        # Only the manager's own repository has the deploy script wiring.
        return Path(path).resolve() == ROOT

    def settings(self):
        with self.db.connect() as conn:
            return [{'project_id': p['id'], 'name': p['display_name'] or p['name'], 'enabled': enabled(conn, p['id']),
                     'deployable': self.deployable(p['path'])} for p in self.registry.list()]

    def set(self, project_id, value, source='Settings'):
        if not isinstance(project_id, str):
            raise ValueError('Choose a project')
        if not isinstance(value, bool):
            raise ValueError('Auto-deploy setting must be a boolean')
        with self.db.transaction() as conn:
            project = conn.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project or project['status'] == 'removed':
                raise ValueError('Unknown or removed project')
            setting = {'enabled': value, 'updated_at': time.time(), 'source': source}
            put(conn, project_id, setting)
            self.db.event(conn, 'auto_deploy.setting', setting, project_id)
        if not value:
            self.cancel(project_id)
        return setting

    def seed(self):
        """Owner request of 2026-10-14: ON for the manager's own project, once; later toggles always persist."""
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM settings WHERE key=?', (SEED,)).fetchone():
                return
            seeded = []
            for project in conn.execute("SELECT id,path FROM projects WHERE status!='removed'").fetchall():
                if self.deployable(project['path']) and not conn.execute(
                        'SELECT 1 FROM settings WHERE key=?', (SETTING + project['id'],)).fetchone():
                    setting = {'enabled': True, 'updated_at': time.time(), 'source': 'Owner request 2026-10-14'}
                    put(conn, project['id'], setting)
                    self.db.event(conn, 'auto_deploy.setting', setting, project['id'])
                    seeded.append(project['id'])
            if seeded:
                conn.execute('INSERT INTO settings VALUES(?,?)', (SEED, json.dumps({'at': time.time(), 'projects': seeded})))

    # ---- deploy protocol (files shared with scripts/deploy.py) ---------------------------------------------
    def installed(self):
        return (self.root / 'installed.json').is_file()

    def pending(self):
        return (self.root / 'request.json').is_file()

    def status(self):
        return {'installed': self.installed(), 'request': read(self.root / 'request.json'),
                'state': read(self.root / 'state.json') if self.pending() else None,
                'last_result': read(self.root / 'result.json')}

    def summary(self):
        """Short fact for the manager prompt."""
        value = self.status()
        if value['request']:
            return 'deploy requested; ' + ((value['state'] or {}).get('phase') or 'queued')
        last = value['last_result']
        return (last['status'] + ': ' + last['reason'])[:200] if last else 'no deploy yet'

    def request(self, project_id, source='auto', task_id=None, conversation_id=None, reason=''):
        if not self.deployable(self.registry.get(project_id)['path']):
            raise ValueError('Deploy is available only for the manager project')
        if self.pending():
            return read(self.root / 'request.json')
        value = {'id': hashlib.sha256(os.urandom(16)).hexdigest()[:16], 'project_id': project_id, 'source': source,
                 'task_id': task_id, 'conversation_id': conversation_id, 'reason': reason[:300],
                 'requested_at': time.time()}
        atomic(self.root / 'request.json', value)
        return value

    def cancel(self, project_id=None):
        """Withdraw a request that has not yet reached backup/restart; the script re-checks before it stops anything."""
        request = read(self.root / 'request.json')
        if not request or (project_id and request.get('project_id') != project_id):
            return False
        phase = (read(self.root / 'state.json') or {}).get('phase')
        if phase not in {None, 'testing', 'waiting_idle'}:
            raise ValueError('Deploy already started; it finishes or rolls back automatically')
        (self.root / 'request.json').unlink(missing_ok=True)
        return True

    def post(self, conn, conversation_id, key, text):
        if conversation_id and conn.execute('SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone():
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                         "VALUES(?,?,'assistant',?,'completed',?)",
                         ('pipeline-' + hashlib.sha256(key.encode()).hexdigest()[:24], conversation_id, text, time.time()))

    def report(self):
        """Tell the originating conversation about a finished deploy exactly once."""
        result = read(self.root / 'result.json')
        if not result or self.pending():
            return
        with self.db.transaction() as conn:
            key = 'deploy_reported:' + result['id']
            if conn.execute('SELECT 1 FROM settings WHERE key=?', (key,)).fetchone():
                return
            payload = {k: result.get(k) for k in ('id', 'status', 'reason', 'commit', 'previous')}
            self.db.event(conn, 'deployment.result', payload, result.get('project_id'), result.get('task_id'))
            text = {'success': 'Deploy succeeded', 'failed': 'Deploy failed; nothing changed',
                    'rolled_back': 'Deploy failed its health check and was rolled back (code and database)',
                    'rollback_failed': 'Deploy failed and could not be rolled back; the service needs attention',
                    'cancelled': 'Deploy cancelled'}.get(result['status'], 'Deploy ' + result['status'])
            self.post(conn, result.get('conversation_id'), key, f"{text}: {result['reason']}"[:600])
            conn.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(payload)))

    # ---- merge to main ----------------------------------------------------------------------------------------
    def merge(self, project_path, commit):
        """The only path into main: exclusive checkout fence, re-checked pending deploy, then the merge."""
        with fence.hold(self.db.path.parent, exclusive=True):
            if self.pending():
                raise Busy('a deploy is pending')
            return merge_into_main(project_path, commit)

    def expire(self, max_age=7 * 3600):
        """A deploy request nobody consumed (script not installed, stuck unit) must not hold the queue forever."""
        request = read(self.root / 'request.json')
        if request and time.time() - request.get('requested_at', time.time()) > max_age:
            (self.root / 'request.json').unlink(missing_ok=True)
            (self.root / 'state.json').unlink(missing_ok=True)
            with self.db.transaction() as conn:
                self.db.event(conn, 'deployment.expired', {'id': request.get('id')}, request.get('project_id'))
                self.post(conn, request.get('conversation_id'), 'expired:' + str(request.get('id')),
                          'Deploy request expired without being processed (is the deploy unit installed?). Nothing changed.')
            return True
        return False

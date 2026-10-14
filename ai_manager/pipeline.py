"""Minimal delivery pipeline. Contract: docs/AUTO_DEPLOY.md.

Worker success -> merge the assignment branch into main (one at a time) -> ask the single deploy script
(scripts/deploy.py) to deploy main. Per-project "Auto-deploy" ON means merge and deploy; OFF means neither.
The deploy script itself waits for idle, backs up, restarts, health-checks and rolls back; this module only
writes its request file, honors holds and reports its short result. No AI call decides anything here.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

SETTING = 'auto_deploy:'
SEED = 'auto_deploy_seed'
ROOT = Path(os.getenv('AI_MANAGER_REPO') or Path(__file__).resolve().parents[1]).resolve()  # live code checkout
FAILED_MARK = re.compile(r'^\W*RESULT:\s*(PASSED|FAILED)\b', re.I | re.M)
IDENTITY = ['-c', 'user.name=Linko Bot Boss', '-c', 'user.email=bot-boss@linko.invalid']


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


def main_branch(root):
    for name in ('main', 'master'):
        if git(root, 'show-ref', '--verify', '--quiet', 'refs/heads/' + name, check=False).returncode == 0:
            return name
    raise ValueError('No main branch in the project checkout')


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
        raise ValueError('merge conflict with main; rebase or merge main into the assignment branch and retry: '
                         + (result.stdout + result.stderr).strip()[-300:])
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
    def busy(self):
        return bool(self.job and not self.job.done())

    def candidate(self):
        """Oldest finished assignment whose verified branch is ready to merge, else None."""
        with self.db.connect() as conn:
            for task in conn.execute("SELECT t.*,p.path AS project_path FROM tasks t JOIN projects p ON p.id=t.project_id "
                                     "WHERE t.lifecycle='completed' AND t.lease_owner IS NULL AND t.workspace IS NOT NULL "
                                     "AND p.status='active' ORDER BY t.updated_at,t.id").fetchall():
                if not enabled(conn, task['project_id']):
                    continue
                execution = conn.execute('SELECT * FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1',
                                         (task['id'],)).fetchone()
                if (not execution or execution['status'] != 'completed' or execution['stop_requested']
                        or execution['delivery'] not in {'delivered', 'discarded'}):
                    continue
                flow = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_workflow:' + task['id'],)).fetchone()
                if flow and json.loads(flow[0]).get('state') != 'complete':
                    continue  # Owner hold, wait, deferral or unfinished repair.
                row = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_source:' + task['id'],)).fetchone()
                source = json.loads(row[0]) if row else {}
                if not source.get('commit') or source.get('dirty'):
                    continue
                row = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_merge:' + task['id'],)).fetchone()
                if row and json.loads(row[0]).get('commit') == source['commit']:
                    continue  # Merged or already reported failed; a new commit is a new attempt.
                return {'task': dict(task), 'commit': source['commit'], 'execution': dict(execution)}
        return None

    def mark(self, conn, candidate, state, reason, **extra):
        task = candidate['task']
        value = {'state': state, 'commit': candidate['commit'], 'reason': reason[:600], 'at': time.time(), **extra}
        conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     ('assignment_merge:' + task['id'], json.dumps(value)))
        self.db.event(conn, 'pipeline.' + state, value, task['project_id'], task['id'])
        return value

    def step(self):
        """One scheduler tick: at most one merge, never while a deploy is pending or another merge runs."""
        if self.busy() or self.pending():
            return
        candidate = self.candidate()
        if candidate:
            self.job = asyncio.create_task(self.merge(candidate))

    async def merge(self, candidate):
        task, commit = candidate['task'], candidate['commit']
        short = task['id'][:8]
        try:
            tail = candidate['execution']['output'][-4000:]
            marks = FAILED_MARK.findall(tail)
            if marks and marks[-1].upper() == 'FAILED':
                with self.db.transaction() as conn:
                    self.mark(conn, candidate, 'blocked', 'worker reported failing checks after its repair rounds')
                    self.post(conn, task['origin_conversation'], 'merge:' + task['id'] + commit,
                              f'Assignment {short} was not merged: the worker reported failing checks after its repair '
                              'rounds. Nothing was merged or deployed.')
                return
            head, changed = await asyncio.to_thread(merge_into_main, task['project_path'], commit)
            requested = None
            if changed and self.deployable(task['project_path']):
                requested = self.request(task['project_id'], 'auto', task['id'], task['origin_conversation'],
                                         'merged assignment ' + short)
            with self.db.transaction() as conn:
                self.mark(conn, candidate, 'merged', 'merged into main' if changed else 'already in main', main=head)
                if changed:
                    self.post(conn, task['origin_conversation'], 'merge:' + task['id'] + commit,
                              f'Assignment {short} merged into main ({head[:10]})'
                              + ('; deploy requested.' if requested else '.'))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.getLogger(__name__).warning('merge of %s failed: %s', short, error)
            with self.db.transaction() as conn:
                self.mark(conn, candidate, 'merge_failed', str(error))
                self.post(conn, task['origin_conversation'], 'merge:' + task['id'] + commit,
                          f'Assignment {short} was not merged: {str(error)[:400]}. Nothing was deployed; '
                          'repair the existing assignment.')

    async def merge_assignment(self, task_id):
        """Owner-requested merge (manager `deploy` action) even when Auto-deploy is OFF."""
        with self.db.connect() as conn:
            task = conn.execute("SELECT t.*,p.path AS project_path FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?",
                                (task_id,)).fetchone()
            row = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_source:' + task_id,)).fetchone()
        source = json.loads(row[0]) if row else {}
        if (not task or task['lifecycle'] != 'completed' or task['lease_owner'] or not source.get('commit')
                or source.get('dirty')):
            raise ValueError('Assignment must be completed, stopped and committed to merge')
        execution = self.db.one('SELECT * FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1', (task_id,))
        head, changed = await asyncio.to_thread(merge_into_main, task['project_path'], source['commit'])
        with self.db.transaction() as conn:
            self.mark(conn, {'task': dict(task), 'commit': source['commit'], 'execution': execution}, 'merged',
                      'merged into main by owner request', main=head)
        return {'main': head, 'changed': changed}

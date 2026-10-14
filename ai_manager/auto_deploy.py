"""Owner-enabled automatic deployment of verified, owner-requested work. Contract: docs/AUTO_DEPLOY.md.

Per-project setting "Auto-deploy verified work". When on, the owner's Auto/Execute work request is the recorded
deployment intent for that assignment's exact release only. No AI call decides deployment: the Option B
verification record, the fixed preparation gate (full backend+browser suite, migration rehearsal for
schema/dependency releases) and the existing helper protocol (idle wait, backup, health check, automatic
rollback) do. Failed verification or preparation never deploys; nothing is retried automatically.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .deployment import read
from .deployment_intent import Held, Superseded, assignment_evidence, revoke, rows, save

SETTING = 'auto_deploy:'
SEED = 'auto_deploy_seed'
ROOT = Path(__file__).resolve().parents[1]
PREPARE_SECONDS = 3600


def enabled(conn, project_id):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (SETTING + project_id,)).fetchone()
    return bool(row and json.loads(row[0]).get('enabled'))


def put(conn, project_id, value):
    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (SETTING + project_id, json.dumps(value)))


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('git ' + args[0] + ' failed: ' + result.stderr.strip()[:300])
    return result.stdout.strip()


def verified(conn, task, execution_id, commit=None):
    """'' when the completed attempt passed its required Option B tier checks, else the reason it did not."""
    row = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_policy:' + task['id'],)).fetchone()
    policy = json.loads(row[0]) if row else {}
    if not policy.get('verification'):
        return 'assignment predates risk-tiered verification'
    row = conn.execute("SELECT payload FROM events WHERE execution_id=? AND kind='task.verification' ORDER BY seq DESC LIMIT 1",
                       (execution_id,)).fetchone()
    if not row:
        return 'no verification record for the completed attempt'
    record = json.loads(row[0])
    from .verification import plan
    required = plan(policy['verification'], policy.get('integration'))['tier']
    if record.get('assigned') != required or record.get('effective') != required:
        return 'verification does not cover the required current tier'
    if record.get('error') or record.get('actual_error'):
        return 'verification post-check failed: ' + (record.get('error') or record['actual_error'])[:300]
    if record.get('escalated'):
        return 'actual changes need the higher ' + record['effective'] + ' tier checks'
    if not record.get('changed_count'):
        return 'no file changes to deploy'
    if record.get('scope') != assignment_evidence(conn, task):
        return 'assignment scope changed after verification'
    if commit and record.get('commit') != commit:
        return 'verification is not bound to the exact clean commit'
    review = record.get('review') or {}
    if record['effective'] == 'risky' and (review.get('status') != 'completed'
                                           or not review.get('findings', '').strip().startswith('No blocking findings')):
        return 'risky diff review did not pass: ' + (review.get('reason') or review.get('findings') or 'unavailable')[:300]
    return ''


class AutoDeploy:
    def __init__(self, db, registry, handoff, intents, prepare=None):
        self.db, self.registry, self.handoff, self.intents = db, registry, handoff, intents
        self.prepare = prepare or self.prepare_release
        self.job = None

    def busy(self):
        return bool(self.job and not self.job.done())

    def deployable(self, path):
        # Only the manager's own repository has the controlled release/helper pipeline.
        config = read(self.handoff.root / 'config.json')
        return bool(config and path == config['repo'])

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
                self.withdraw(conn, project_id, 'Owner turned off auto-deploy')
        return setting

    def withdraw(self, conn, project_id, reason):
        """Turning the setting off revokes unused auto intents and cancels an unacknowledged auto request."""
        for value in rows(conn):
            if value.get('kind') != 'auto' or value['project_id'] != project_id:
                continue
            if value['status'] == 'recorded':
                revoke(self.db, conn, reason, intent_id=value['id'])
            active = self.handoff.status()
            if (value['status'] == 'dispatched' and active.get('nonce') == value.get('nonce')
                    and active['phase'] == 'waiting_idle'):
                self.handoff.cancel(active['nonce'])

    def seed(self):
        """Owner request of 2026-10-14: ON for the manager's own deployable project (Linko-Bot-Boss), once.

        The marker makes this a one-time initial value; later owner toggles always persist."""
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM settings WHERE key=?', (SEED,)).fetchone():
                return
            seeded = []
            for project in conn.execute("SELECT id,path FROM projects WHERE status!='removed'").fetchall():
                if self.deployable(project['path']) and not conn.execute(
                        'SELECT 1 FROM settings WHERE key=?', (SETTING + project['id'],)).fetchone():
                    setting = {'enabled': True, 'updated_at': time.time(),
                               'source': 'Owner request 2026-10-14 (assignment 44097fd1a52c4f9096fcd901bd04d344)'}
                    put(conn, project['id'], setting)
                    self.db.event(conn, 'auto_deploy.setting', setting, project['id'])
                    seeded.append(project['id'])
            if seeded:
                conn.execute('INSERT INTO settings VALUES(?,?)', (SEED, json.dumps({'at': time.time(), 'projects': seeded})))

    def owner_request(self, project_id, params, task_id):
        """Record the owner's Auto/Execute request as this assignment's auto intent when the setting is on."""
        if params.get('internal') or params.get('mode') == 'conversation':
            return None
        if not self.deployable(self.registry.get(project_id)['path']):
            return None
        with self.db.transaction() as conn:
            if not enabled(conn, project_id):
                return None
            return self.intents.record_auto(conn, project_id, params, task_id)

    def recover(self):
        """A preparation interrupted by a restart is failed, never resumed or retried."""
        if self.busy():
            return
        with self.db.transaction() as conn:
            active = self.handoff.status()
            for value in rows(conn):
                if value.get('kind') != 'auto' or value['status'] != 'recorded':
                    continue
                if (active.get('origin') or {}).get('intent_id') == value['id']:
                    # Request bytes can survive a crash before SQL commits consumption. Never redispatch them.
                    value.update(status='dispatched', nonce=active['nonce'], candidate=active['origin']['candidate'])
                    self.mark(conn, value, 'dispatched', reason='Recovered durable helper request')
                elif value['auto']['state'] == 'preparing':
                    self.fail(conn, value, 'Preparation was interrupted by a restart')

    def mark(self, conn, value, state, notice=None, **extra):
        value['auto'] = {**value['auto'], 'state': state, 'updated_at': time.time(), **extra}
        save(self.db, conn, value, 'auto_' + state)
        if notice and conn.execute('SELECT 1 FROM conversations WHERE id=?', (value['conversation_id'],)).fetchone():
            key = value['id'] + ':' + state + ':' + str(extra.get('execution_id', ''))
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                         "VALUES(?,?,'assistant',?,'completed',?)",
                         ('auto-deploy-' + hashlib.sha256(key.encode()).hexdigest()[:24], value['conversation_id'],
                          notice, time.time()))
        return value

    def fail(self, conn, value, reason):
        value.update(status='failed', reason=reason[:2000])
        self.mark(conn, value, 'failed', 'Auto-deploy stopped: ' + reason[:600] +
                  '. Nothing was deployed and it will not be retried automatically.')

    def evaluate(self, conn, value):
        """Next pipeline step for one unused auto intent: None to wait, or the exact verified candidate."""
        task_id, auto = value['task_ids'][0], value['auto']
        project = conn.execute('SELECT * FROM projects WHERE id=?', (value['project_id'],)).fetchone()
        if not project or not enabled(conn, project['id']) or not self.deployable(project['path']):
            revoke(self.db, conn, 'Auto-deploy is off or unavailable for this project', intent_id=value['id'])
            return None
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['lifecycle'] != 'completed' or task['lease_owner']:
            return None
        execution = conn.execute('SELECT * FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1',
                                 (task_id,)).fetchone()
        # Wait for the manager to review the delivered result and finish follow-up (continue/hold/complete).
        if not execution or execution['status'] != 'completed' or execution['stop_requested'] or execution['delivery'] != 'delivered':
            return None
        workflow = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_workflow:' + task_id,)).fetchone()
        if workflow and json.loads(workflow[0]).get('state') != 'complete':
            return None
        if auto.get('state') == 'prepared' and (auto.get('commit') != self.db.setting('assignment_source:' + task_id, {}).get('commit')
                or auto.get('execution_id') != execution['id'] or auto.get('scope') != assignment_evidence(conn, task)):
            self.mark(conn, value, 'waiting_result', reason='Scope/candidate changed; old prepared release withdrawn')
            auto = value['auto']
        if auto.get('execution_id') == execution['id'] and auto['state'] == 'not_verified':
            return None  # Already reported; a later completed attempt is evaluated afresh.
        source = json.loads((conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_source:' + task_id,)).fetchone()
                             or ['{}'])[0])
        commit = source.get('commit')
        reason = verified(conn, task, execution['id'], commit)
        if not reason and (not task['workspace'] or not commit or source.get('dirty') or 'dirty' in source):
            reason = 'assignment source is not one clean committed workspace branch'
        if not reason:
            try:
                live = git(project['path'], 'rev-parse', 'HEAD')
                if git(task['workspace'], 'status', '--porcelain'):
                    reason = 'assignment workspace has uncommitted changes'
                elif git(task['workspace'], 'rev-parse', 'HEAD') != commit:
                    reason = 'assignment branch moved after completion'
                else:
                    git(task['workspace'], 'merge-base', '--is-ancestor', live, commit)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                reason = 'candidate needs serialized reconciliation with current live source: ' + str(error)[:200]
        if reason:
            self.mark(conn, value, 'not_verified', f'Auto-deploy skipped for assignment {task_id[:8]}: {reason}. '
                      'Nothing was deployed; this assignment needs corrected verification.',
                      execution_id=execution['id'], reason=reason)
            return None
        try:
            self.intents.current(conn, value)
        except ValueError as error:
            if auto['state'] != 'held' or auto.get('reason') != str(error):
                self.mark(conn, value, 'held', reason=str(error))
            return None
        if conn.execute("SELECT 1 FROM settings WHERE key IN ('release_preparation','assignment_integration')").fetchone():
            return None
        return {'task': dict(task), 'scope': assignment_evidence(conn, task), 'execution_id': execution['id'], 'commit': commit,
                'release_id': f'auto-{task_id[:8]}-{commit[:12]}-{execution["id"][:8]}', 'project_path': project['path']}

    def step(self):
        """One scheduler tick: start at most one preparation. Waiting is never a retry."""
        if self.busy() or self.handoff.gated():
            return
        with self.db.transaction() as conn:
            pending = sorted((v for v in rows(conn) if v.get('kind') == 'auto' and v['status'] == 'recorded'),
                             key=lambda v: v['created_at'])
            for value in pending:
                if value['auto']['state'] == 'preparing':
                    self.fail(conn, value, 'Preparation was interrupted')
                    continue
                candidate = self.evaluate(conn, value)
                if candidate:
                    if value['auto'].get('state') == 'prepared':
                        self.dispatch(conn, value, candidate)
                        return
                    self.mark(conn, value, 'preparing',
                              f"Auto-deploy: assignment {candidate['task']['id'][:8]} passed its tier checks; preparing "
                              f"release {candidate['release_id']} (full backend+browser suite"
                              + '; migration rehearsal when needed).',
                              execution_id=candidate['execution_id'], release_id=candidate['release_id'],
                              commit=candidate['commit'], scope=candidate['scope'])
                    self.job = asyncio.create_task(self.run(value['id'], candidate))
                    return

    async def run(self, intent_id, candidate):
        release_id, task = candidate['release_id'], candidate['task']
        checkout = self.db.path.parent / 'auto-deploy' / release_id
        try:
            # Prepare a pristine clone of the exact verified commit; the workspace may be continued meanwhile.
            checkout.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            await asyncio.to_thread(git, candidate['project_path'], 'clone', '--quiet', '--no-checkout',
                                    candidate['project_path'], str(checkout))
            await asyncio.to_thread(git, checkout, 'fetch', '--quiet', task['workspace'], candidate['commit'])
            await asyncio.to_thread(git, checkout, 'checkout', '--quiet', '--detach', candidate['commit'])
            await self.prepare(checkout, Path(candidate['project_path']), self.db.path.parent, release_id, task['id'])
            with self.db.transaction() as conn:
                value = next(v for v in rows(conn) if v['id'] == intent_id)
                if value['status'] == 'recorded':
                    self.mark(conn, value, 'prepared')
            # Dispatch on the next tick; explicit holds or a scope change during preparation are honored.
        except asyncio.CancelledError:
            raise  # Service stop: recover() fails the interrupted preparation on restart.
        except Exception as error:
            logging.getLogger(__name__).warning('auto-deploy %s stopped: %s', release_id, error)
            with self.db.transaction() as conn:
                value = next((v for v in rows(conn) if v['id'] == intent_id), None)
                if value and value['status'] == 'recorded':
                    self.fail(conn, value, 'Release preparation/verification failed (' + (str(error) or type(error).__name__)[:400] + ')')
        finally:
            shutil.rmtree(checkout, ignore_errors=True)

    def dispatch(self, conn, value, candidate):
        manifest = read(self.handoff.root / 'releases' / value['auto']['release_id'] / 'manifest.json') or {}
        helper = self.handoff.root / 'helper-v3.py'
        upgrade = read(self.handoff.root / 'helper-v3-upgrade.json') or {}
        from .deployment import sha
        if (upgrade.get('phase') != 'succeeded' or not helper.is_file()
                or sha(helper) != manifest.get('required_helper_sha256')):
            if value['auto'].get('reason') != 'helper v3 bootstrap required':
                self.mark(conn, value, 'prepared', 'Auto-deploy is prepared and waiting for the external helper v3 upgrade. '
                          'No additional owner deployment approval is needed.', reason='helper v3 bootstrap required')
            return
        # consume_auto owns a separate transaction; release the caller transaction before consuming on a job.
        self.job = asyncio.create_task(self.consume(value['id'], candidate))

    async def consume(self, intent_id, candidate):
        try:
            self.intents.consume_auto(intent_id, candidate['release_id'], candidate['commit'], candidate['execution_id'])
        except Held:
            pass  # Explicit hold: keep the immutable prepared release, without rerunning validation.
        except Superseded:
            with self.db.transaction() as conn:
                value = next(v for v in rows(conn) if v['id'] == intent_id)
                self.mark(conn, value, 'waiting_result', reason='Assignment scope changed; release withdrawn')
        except Exception as error:
            with self.db.transaction() as conn:
                value = next(v for v in rows(conn) if v['id'] == intent_id)
                if value['status'] == 'recorded':
                    self.fail(conn, value, 'Deployment request failed: ' + str(error)[:400])

    async def prepare_release(self, checkout, live, data, release_id, task_id):
        """Run the RUNNING manager's fixed preparation gate against the candidate clone."""
        log = data / 'auto-deploy' / (release_id + '.log')
        argv = [sys.executable, str(ROOT / 'scripts/prepare_deployment.py'), release_id, '--live', str(live),
                '--data', str(data), '--task-id', task_id, '--auto-execution', self.db.one('SELECT id FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1', (task_id,))['id'], '--candidate', str(checkout)]
        with log.open('w') as stream:
            os.chmod(log, 0o600)
            process = await asyncio.create_subprocess_exec(*argv, cwd=ROOT, stdout=stream, stderr=stream,
                                                           start_new_session=True, env={**os.environ, 'PYTHONPATH': str(ROOT)})
            try:
                code = await asyncio.wait_for(process.wait(), PREPARE_SECONDS)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise
        if code:
            raise ValueError('preparation gate failed; private log ' + str(log))

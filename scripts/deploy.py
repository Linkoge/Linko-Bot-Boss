#!/usr/bin/env python3
"""The one deploy script. Contract: README.md. Standard library only.

Triggered by systemd when the manager writes DATA/deploy/request.json (or run by hand: `python3 scripts/deploy.py`).
Every saved project setup uses the same wait for idle -> backup -> deploy main -> health check -> automatic rollback
sequence. Command/service setups supply backup, health and rollback hooks; the installed runtime adapter preserves
its full backend suite, SQLite snapshots and scheduler/authentication checks. Results are durable and project-scoped.
"""
import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager import fence  # noqa: E402
from ai_manager.deploy_setup import validate_setup  # noqa: E402

IDLE = {
    'unfinished_executions': 'SELECT count(*) FROM executions WHERE finished_at IS NULL',
    'leases': 'SELECT count(*) FROM tasks WHERE lease_owner IS NOT NULL',
    'running_tasks': "SELECT count(*) FROM tasks WHERE state='running'",
    'streaming_messages': "SELECT count(*) FROM messages WHERE status IN ('queued','streaming')",
    'deliveries': "SELECT count(*) FROM executions WHERE delivery='running'",
}
KEEP_BACKUPS = 10
IDLE_SECONDS = 1800  # the wait for idle is bounded; the app withdraws a request this old (pipeline.DEPLOY_WAIT)


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


def run(argv, cwd=None, timeout=300, check=True, env=None):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(map(str, argv[:3]))} failed: {(result.stderr or result.stdout).strip()[-300:]}")
    return result


class Stop(Exception):
    """Ends the deploy with a status before anything was changed."""

    def __init__(self, status, reason, repairable=False):
        super().__init__(reason)
        self.status = status
        self.repairable = repairable  # a code change can fix it (failing tests or health), unlike busy/lock/setup problems


class HealthFailed(RuntimeError):
    """The health check still failed at its deadline."""


class Deploy:
    def __init__(self, config, sleep=time.sleep):
        self.config, self.sleep = config, sleep
        self.repo, self.data = Path(config['repo']), Path(config['data'])
        self.root = self.data / 'deploy'
        self.db = self.data / 'manager.sqlite3'
        self.url = config.get('url', 'http://127.0.0.1:8787')
        systemctl = config.get('systemctl', ['systemctl', '--user'])
        service = config.get('service', 'linko-bot-manager.service')
        self.commands = {'stop': [*systemctl, 'stop', service], 'start': [*systemctl, 'start', service],
                         **config.get('service_cmds', {})}
        # Other instances of this one code copy (README "Multiple instances"): restarted and health-checked with it,
        # each with its own data directory, database snapshot, checkout fence and port.
        self.instances = [{'name': i['name'], 'data': Path(i['data']), 'url': i['url'], 'cmds': i.get('service_cmds') or {
            action: [*systemctl, action, i['service']] for action in ('stop', 'start')}}
            for i in config.get('instances', [])]
        self.id = None
        self.setup = None  # installed runtime adapter until a project request selects its saved setup

    def select_setup(self, request):
        setup = request.get('setup')
        if setup is None:
            # Pre-upgrade/host CLI requests resolve the same saved setup, including explicit removal.
            if not request.get('project_id') and not self.db.is_file():
                return
            project = self.query('SELECT id,path FROM projects WHERE id=?', (request['project_id'],)) if (
                request.get('project_id')) else self.query('SELECT id,path FROM projects WHERE path=?', (str(self.repo),))
            if project:
                request.update(project_id=project[0], repo=project[1])
                saved = self.query('SELECT value FROM settings WHERE key=?', ('deploy_setup:' + project[0],))
                setup = json.loads(saved[0]) if saved else (
                    {'runtime': self.config} if Path(project[1]).resolve() == self.repo.resolve() else None)
                if setup is None:
                    raise ValueError('no deploy setup')
                request['setup'] = setup
            elif request.get('project_id'):
                raise ValueError('no deploy setup')
            else:
                return  # Initial operator bootstrap before this runtime has a registered project.
        if 'runtime' in setup:
            runtime = setup['runtime']
            if Path(runtime['repo']).resolve() != Path(request['repo']).resolve():
                raise ValueError('Deploy setup repository mismatch')
            # Keep host timing overrides; all runtime operations use the saved installation.
            self.__init__({**runtime, **{k: v for k, v in self.config.items()
                                      if k in {'health_seconds', 'poll_seconds', 'idle_seconds', 'test_seconds', 'instances'}}}, self.sleep)
            self.id = request['id']
            return
        self.setup = validate_setup(setup)
        self.instances = []  # an external project's deploy never restarts this application's instances
        if request.get('emergency'):
            raise ValueError('Normal project deploys cannot use host emergency mode')
        self.repo = Path(request['repo'])
        self.receipt = self.root / 'projects' / (hashlib.sha256(str(self.repo.resolve()).encode()).hexdigest() + '.json')
        service = self.setup.get('service')
        self.commands = {'stop': ['systemctl', '--user', 'stop', service] if service else ['true'],
                         'start': self.setup.get('deploy_command') or ['systemctl', '--user', 'start', service]}

    @property
    def last_good(self):
        return self.receipt if self.setup else self.root / 'last_good.json'

    def hook(self, name, backup=None, commit=None):
        env = {**os.environ, 'DEPLOY_BACKUP_DIR': str(backup or self.backup_dir),
               'DEPLOY_REPO': str(self.repo), 'DEPLOY_COMMIT': commit or self.target or '',
               'DEPLOY_PREVIOUS': self.previous or ''}
        return run(self.setup[name], cwd=self.repo, timeout=120, env=env)

    def service(self, action):
        if self.setup and action == 'start' and self.setup.get('deploy_command'):
            return self.hook('deploy_command')
        result = run(self.commands[action], cwd=self.repo, timeout=120)
        for instance in self.instances:
            run(instance['cmds'][action], cwd=self.repo, timeout=120)
        return result

    def databases(self):
        """(label, path) of every database this deploy must keep idle, back up and restore: main first."""
        return [('', self.db)] + [(i['name'] + ':', i['data'] / 'manager.sqlite3') for i in self.instances
                                  if (i['data'] / 'manager.sqlite3').is_file()]

    def fences(self, wait):
        """The checkout fence of this application and of every instance, so no work starts in any of them."""
        stack = ExitStack()
        try:
            for data in [self.data, *(i['data'] for i in self.instances)]:
                stack.enter_context(fence.hold(data, True, wait=wait))
        except BaseException:
            stack.close()
            raise
        return stack

    @property
    def branch(self):
        return fence.main_branch(self.repo, None if self.setup else self.config.get('branch'))

    # ---- bookkeeping --------------------------------------------------------------------------------------------
    def log(self, text):
        line = time.strftime('%Y-%m-%d %H:%M:%S ') + text
        print(line, flush=True)
        with (self.root / 'deploy.log').open('a') as stream:
            stream.write(line + '\n')

    def state(self, phase, **extra):
        atomic(self.root / 'state.json', {'id': self.id, 'phase': phase, 'at': time.time(), 'pid': os.getpid(), **extra})
        self.log('phase ' + phase)

    def git(self, *args, check=True):
        return run(['git', '-C', str(self.repo), *args], check=check).stdout.strip()

    def request(self):
        value = read(self.root / 'request.json')
        return value if value and value.get('id') == self.id else None

    def finish(self, request, status, reason, **extra):
        result = {'id': self.id, 'status': status, 'reason': reason[:500], 'finished_at': time.time(),
                  'requested_at': request.get('requested_at'), 'project_id': request.get('project_id'),
                  'task_id': request.get('task_id'), 'source': request.get('source'), 'conversation_id': request.get('conversation_id'), **extra}
        atomic(self.root / 'results' / (self.id + '.json'), result)
        atomic(self.root / 'result.json', result)
        if request.get('project_id'):
            key = hashlib.sha256(request['project_id'].encode()).hexdigest()
            atomic(self.root / 'project-results' / (key + '.json'), result)
        stamp = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(result['finished_at']))
        (self.root / 'last-deploy.txt').write_text(f"{stamp} - {status.replace('_', ' ')}: {result['reason']}\n")
        self.log(f"result {status}: {result['reason']}")
        (self.root / 'state.json').unlink(missing_ok=True)
        if self.request():
            (self.root / 'request.json').unlink(missing_ok=True)
        if (read(self.root / 'emergency.json') or {}).get('id') == self.id:
            (self.root / 'emergency.json').unlink(missing_ok=True)
        return result

    # ---- idle, holds, health -------------------------------------------------------------------------------------
    def query(self, sql, args=()):
        with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10) as conn:
            return conn.execute(sql, args).fetchone()

    def busy(self):
        # One SQLite snapshot; queued/held/retry-waiting tasks are durable, not active writers.
        busy = {}
        for label, db in self.databases():
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10) as conn:
                conn.execute('BEGIN')
                busy.update({label + name: n for name, sql in IDLE.items() if (n := conn.execute(sql).fetchone()[0])})
        return busy

    def blockers(self):
        """Bounded identities only: never log task input, output or credentials."""
        checks = {
            'executions': "SELECT id,task_id,agent_id,status,pid,started_at,retry_at,delivery FROM executions "
                          "WHERE finished_at IS NULL OR delivery='running' LIMIT 20",
            'tasks': "SELECT id,state,lease_owner,updated_at FROM tasks "
                     "WHERE lease_owner IS NOT NULL OR state='running' LIMIT 20",
            'messages': "SELECT id,execution_id,status FROM messages WHERE status IN ('queued','streaming') LIMIT 20",
        }
        found = {}
        for label, db in self.databases():
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('BEGIN')
                found.update({label + name: [dict(row) for row in conn.execute(sql)] for name, sql in checks.items()})
        return found

    def check_request(self, request):
        """A withdrawn request or a paused project stops the deploy before anything changes."""
        if not self.request():
            raise Stop('cancelled', 'request withdrawn before the restart')
        emergency = request.get('emergency') or (read(self.root / 'emergency.json') or {}).get('id') == self.id
        if not emergency and (read(self.root / 'state.json') or {}).get('phase') in {'testing', 'waiting_idle'} and (
                time.time() - request.get('requested_at', time.time()) >= IDLE_SECONDS):
            raise Stop('cancelled', 'deploy request expired after 30 minutes; merged work stays on main')
        if request.get('source') == 'auto' and request.get('project_id') and not emergency:
            status = self.query('SELECT status FROM projects WHERE id=?', (request['project_id'],))
            if not status or status[0] != 'active':
                raise Stop('cancelled', 'project is paused or removed')

    def http(self, path, cookie=None, url=None):
        req = urllib.request.Request((url or self.url) + path)
        if cookie:
            req.add_header('Cookie', 'ai_manager_session=' + cookie)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def running_commit(self):
        if self.setup:
            return (read(self.last_good) or {}).get('commit')
        try:
            status, body = self.http('/api/auth-status')
            return json.loads(body).get('release_commit') if status == 200 else None
        except (OSError, ValueError):
            return None

    def health(self, commit):
        """App responds with the expected code, scheduler runs, auth boundary holds and a session works."""
        if self.setup:
            if self.setup.get('health_command'):
                self.hook('health_command', commit=commit)
            else:
                with urllib.request.urlopen(self.setup['health_url'], timeout=5) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError('unhealthy HTTP status: ' + str(response.status))
            return
        self.check(self.url, self.db, commit)
        for instance in self.instances:  # each instance has its own health check; any failure rolls the deploy back
            try:
                self.check(instance['url'], instance['data'] / 'manager.sqlite3', commit)
            except Exception as error:
                raise RuntimeError(f"instance {instance['name']}: {error}") from error

    def check(self, url, db, commit):
        status, body = self.http('/api/auth-status', url=url)
        info = json.loads(body)
        if status != 200 or not info.get('scheduler_running'):
            raise RuntimeError('auth-status unhealthy or scheduler not running')
        if commit and info.get('release_commit') != commit:
            raise RuntimeError(f"service runs {str(info.get('release_commit'))[:10]}, expected {commit[:10]}")
        if self.http('/api/portfolio', url=url)[0] != 401:
            raise RuntimeError('authentication boundary failed')
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with sqlite3.connect(db, timeout=10) as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?)', (digest, secrets.token_hex(16), time.time() + 60))
        try:
            if self.http('/api/portfolio', token, url=url)[0] != 200:
                raise RuntimeError('authenticated request failed')
        finally:
            with sqlite3.connect(db, timeout=10) as conn:
                conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))

    def wait_health(self, commit):
        deadline = time.monotonic() + self.config.get('health_seconds', 60)
        while True:
            try:
                return self.health(commit)
            except Exception as error:
                if time.monotonic() >= deadline:
                    raise HealthFailed('health check failed: ' + str(error)) from error
                self.sleep(1)

    # ---- database ------------------------------------------------------------------------------------------------
    def backup(self, destination):
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.setup:
            if destination.name == 'before.sqlite3':
                self.git('bundle', 'create', str(destination.parent / 'code.bundle'), '--all')
                atomic(destination.parent / 'release.json', {'previous': self.previous, 'commit': self.target,
                                                            'repo': str(self.repo), 'setup': self.setup})
                self.hook('backup_command', backup=destination.parent)
            return
        for (_, db), target in zip(self.databases(), self.copies(destination)):
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=30) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup failed the integrity check')
            os.chmod(target, 0o600)

    def copies(self, path):
        """Where each database's snapshot lives: main at `path`, instances in a folder named after them beside it."""
        return [path if not label else path.parent / label[:-1] / path.name for label, _ in self.databases()]

    def restore(self, source):
        if self.setup:
            self.hook('rollback_command', backup=source.parent, commit=self.previous)
            return
        for (_, db), snapshot in zip(self.databases(), self.copies(source)):
            temp = db.with_name(db.name + '.restore')
            temp.unlink(missing_ok=True)
            with sqlite3.connect(snapshot) as src, sqlite3.connect(temp) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup to restore is corrupt')
            for suffix in ('-wal', '-shm'):
                Path(str(db) + suffix).unlink(missing_ok=True)
            os.replace(temp, db)

    def prune(self):
        directory = self.root / 'backups'
        for old in sorted(directory.glob('*'), key=lambda p: p.stat().st_mtime)[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)

    # ---- code ----------------------------------------------------------------------------------------------------
    def sync(self):
        if self.setup:
            return
        if self.config.get('uv'):
            run([self.config['uv'], 'sync', '--locked'], cwd=self.repo, timeout=900)

    def deps_changed(self, old, new):
        return bool(old and self.git('diff', '--name-only', old, new, '--', 'pyproject.toml', 'uv.lock', check=False))

    def rollback(self, previous, target, synced):
        if self.setup and not previous:
            return  # First release: rollback_command restores the live release from the mandatory backup.
        if not previous:
            raise RuntimeError('previous version unknown; code could not be restored')
        self.git('tag', '-f', 'linko-failed-' + self.id, target)  # keeps the failed commits reachable
        self.git('checkout', '--quiet', self.branch)
        ancestor = run(['git', '-C', str(self.repo), 'merge-base', '--is-ancestor', previous, target], check=False)
        if ancestor.returncode == 0:
            self.git('reset', '--hard', '--quiet', previous)
        else:
            self.git('checkout', '--detach', '--quiet', previous)
        if synced:
            self.sync()

    # ---- the deploy ----------------------------------------------------------------------------------------------
    def execute(self, request):
        stopped = changed = synced = False
        previous = target = None
        backups = self.root / 'backups' / self.id
        self.backup_dir, self.previous, self.target = backups, None, None
        fenced = ExitStack()  # holds the checkout fence from the final idle check to the end
        def emergency_stop():
            nonlocal stopped
            # Explicit host-only escape hatch: stop the app under the existing checkout fence,
            # then ignore stale queue records. Never race a still-live native worker.
            fenced.enter_context(self.fences(5))
            self.state('emergency_backing_up')
            self.backup(backups / 'before.sqlite3')
            stopped = True
            self.service('stop')
            pids = []
            for _, db in self.databases():
                with sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10) as conn:
                    pids += conn.execute('SELECT pid,process_start FROM executions WHERE pid IS NOT NULL').fetchall()
            def process_start(pid):
                try:
                    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    return fields[19] if fields[0] != 'Z' else None
                except (OSError, IndexError):
                    return None
            if any((current := process_start(pid)) and (not start or current == start) for pid, start in pids):
                raise RuntimeError('Emergency deploy refused: a recorded worker is still alive')

        try:
            self.state('testing')
            try:
                with fence.hold(self.data, True, wait=60):  # no merge may change the checkout under us
                    if self.git('status', '--porcelain', '--untracked-files=no'):
                        raise Stop('failed', 'live checkout has uncommitted changes')
                    previous = self.running_commit() or (read(self.last_good) or {}).get('commit')
                    if self.git('symbolic-ref', '-q', '--short', 'HEAD', check=False) != self.branch:
                        self.git('checkout', '--quiet', self.branch)
                    target = self.git('rev-parse', 'HEAD')
                    self.previous, self.target = previous, target
            except fence.Busy as error:
                raise Stop('failed', 'live checkout is busy: ' + str(error)) from error
            if not self.setup and not previous:
                raise Stop('failed', 'previous release unknown; cannot guarantee code and database rollback')
            if previous == target and not request.get('emergency'):
                self.wait_health(target)
                raise Stop('success', f'{target[:10]} is already live; nothing to deploy')
            tests = None if self.setup else self.config.get('test_cmd')
            if tests and not request.get('skip_tests'):
                result = run(tests, cwd=self.repo, timeout=self.config.get('test_seconds', 3600), check=False)
                if result.returncode:
                    raise Stop('failed', 'full backend suite failed: ' + (result.stdout + result.stderr).strip()[-300:], repairable=True)
            if request.get('emergency'):
                emergency_stop()
            self.state('waiting_idle', commit=target)
            remaining = max(0, request.get('requested_at', time.time()) + IDLE_SECONDS - time.time())
            deadline = time.monotonic() + min(self.config.get('idle_seconds', IDLE_SECONDS), remaining)
            last_busy, reported_at = None, 0.0
            while True:
                self.check_request(request)
                if not request.get('emergency') and (read(self.root / 'emergency.json') or {}).get('id') == self.id:
                    if self.setup:
                        raise Stop('failed', 'Normal project deploys cannot use host emergency mode')
                    request['emergency'] = True
                    emergency_stop()
                busy = {} if request.get('emergency') else self.busy()
                if not busy:
                    # Fence: from here until the end no merge and no new task/chat can start, so the idle
                    # check below cannot be invalidated before the stop. Work that slipped in releases the fence.
                    if request.get('emergency'):
                        break
                    try:
                        fenced.enter_context(self.fences(5))
                        if not (busy := self.busy()):
                            break
                        fenced.close()
                    except fence.Busy:
                        busy = {'checkout': 1}
                if busy != last_busy or time.monotonic() - reported_at >= 30:
                    self.state('waiting_idle', commit=target, busy=busy, blockers=self.blockers())
                    self.log('waiting for idle: ' + json.dumps(busy, sort_keys=True)
                             + '; blockers: ' + json.dumps(self.blockers(), sort_keys=True))
                    last_busy, reported_at = busy, time.monotonic()
                if time.monotonic() >= deadline:
                    raise Stop('failed', 'not idle after waiting: ' + json.dumps(busy))
                self.sleep(self.config.get('poll_seconds', 5))
            self.check_request(request)
            self.state('backing_up')
            if not request.get('emergency'):
                self.backup(backups / 'before.sqlite3')
            self.check_request(request)
            if not request.get('emergency') and self.busy():
                raise Stop('failed', 'work appeared during backup; will not interrupt it')
            self.state('restarting')
            stopped = True
            if not request.get('emergency'):
                self.service('stop')
            self.backup(backups / 'stopped.sqlite3')  # consistent snapshot; nothing writes while stopped
            self.state('updating')
            changed = True
            self.git('checkout', '--quiet', self.branch)
            if previous and self.deps_changed(previous, target):
                self.sync()
                synced = True
            self.service('start')
            self.state('verifying')
            self.wait_health(target)
            atomic(self.last_good, {'commit': target, 'at': time.time()})
            self.prune()
            return self.finish(request, 'success', f'deployed {target[:10]}; health check passed',
                               commit=target, previous=previous, backup=str(backups))
        except Stop as stop:
            if stopped:
                try:
                    self.service('start')
                    self.wait_health(previous)
                except Exception as restart_error:
                    return self.finish(request, 'rollback_failed', str(stop) + '; restart failed: ' + str(restart_error))
            return self.finish(request, stop.status, str(stop), commit=target, previous=previous,
                               repairable=stop.repairable)
        except Exception as error:
            if not stopped:
                return self.finish(request, 'failed', str(error), commit=target, previous=previous)
            self.state('rolling_back')
            problems = []
            for step, action in (('stop', lambda: self.service('stop')),
                                 ('code', lambda: (changed or self.setup) and self.rollback(previous, target, synced)),
                                 ('data', lambda: (changed or self.setup) and self.restore(backups / 'stopped.sqlite3'))):
                try:
                    action()
                except Exception as rollback_error:  # keep going: the service must still be started
                    problems.append(f'{step} rollback failed: {str(rollback_error)[:200]}')
            try:
                if not self.setup:  # The saved rollback command restores/restarts the external release itself.
                    self.service('start')
                if not problems:
                    self.wait_health(previous)
            except Exception as start_error:
                problems.append(f'restart/health failed: {str(start_error)[:200]}')
            if problems:
                return self.finish(request, 'rollback_failed', f'{str(error)[:200]}; ' + '; '.join(problems),
                                   commit=target, previous=previous, backup=str(backups))
            if not changed:
                return self.finish(request, 'failed', str(error)[:300] + '; service restarted',
                                   commit=target, previous=previous, repairable=isinstance(error, HealthFailed))
            return self.finish(request, 'rolled_back', f'{str(error)[:300]}; code and data restored to '
                               f'{(previous or "backed-up release")[:20]}', commit=target, previous=previous, backup=str(backups),
                               repairable=isinstance(error, HealthFailed))
        finally:
            fenced.close()

    def main(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / 'deploy.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                # A PathExists unit immediately retriggers while request.json remains.
                # Exiting here hot-loops it into start-limit-hit, losing the durable
                # consumer if the manual owner subsequently dies. Wait, then re-read
                # the request: either the owner finished or we safely take over.
                print('another deploy is running; waiting for its lock', flush=True)
                fcntl.flock(lock, fcntl.LOCK_EX)
            request = read(self.root / 'request.json')
            if not request:
                print('no deploy requested')
                return None
            if getattr(self, 'emergency', False) or (read(self.root / 'emergency.json') or {}).get('id') == request['id']:
                request['emergency'] = True
                atomic(self.root / 'request.json', request)
            self.id = request['id']
            try:
                self.select_setup(request)
            except Exception as error:
                return self.finish(request, 'failed', 'Invalid deploy setup: ' + str(error))
            result = self.execute(request)
            return result


def load_config(path, data):
    config = read(path or Path(data) / 'deploy' / 'installed.json')
    if not config:
        raise SystemExit('Deploy mechanism is not installed: run scripts/install_deploy.py once (see README)')
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data', default=os.getenv('AI_MANAGER_DATA', '~/.local/share/linko-bot-manager'))
    parser.add_argument('--now', action='store_true', help='create a deploy request first (manual deploy)')
    parser.add_argument('--emergency', action='store_true', help='Host repair only: stop app and bypass stale queue records')
    args = parser.parse_args(argv)
    os.umask(0o077)
    data = Path(args.data).expanduser()
    config = load_config(args.config, data)
    deploy = Deploy(config)
    if args.now and not (deploy.root / 'request.json').exists():
        atomic(deploy.root / 'request.json', {'id': secrets.token_hex(8), 'source': 'owner', 'reason': 'manual run',
                                              'requested_at': time.time(), 'emergency': args.emergency})
    if args.emergency and (pending := read(deploy.root / 'request.json')):
        atomic(deploy.root / 'emergency.json', {'id': pending['id']})
    deploy.emergency = args.emergency
    result = deploy.main()
    return 0 if not result or result['status'] in {'success', 'cancelled'} else 1


if __name__ == '__main__':
    sys.exit(main())

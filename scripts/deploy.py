#!/usr/bin/env python3
"""The one deploy script. Contract: README.md. Standard library only.

Triggered by systemd when the manager writes DATA/deploy/request.json (or run by hand: `python3 scripts/deploy.py`).
Steps: run the full backend suite once on main -> wait until nothing is running -> back up the SQLite database ->
stop the service -> update the live checkout to main -> start -> health check -> on any failure after the stop,
roll back code and database automatically. The outcome is persisted as one short human-readable result.
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

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


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
        self.id = None

    @property
    def branch(self):
        return fence.main_branch(self.repo, self.config.get('branch'))  # same resolution as the merge step

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
                  'task_id': request.get('task_id'), 'conversation_id': request.get('conversation_id'), **extra}
        atomic(self.root / 'results' / (self.id + '.json'), result)
        atomic(self.root / 'result.json', result)
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
        # One SQLite snapshot; queued/held/backoff tasks are durable, not active writers.
        with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10) as conn:
            conn.execute('BEGIN')
            return {name: n for name, sql in IDLE.items() if (n := conn.execute(sql).fetchone()[0])}

    def blockers(self):
        """Bounded identities only: never log task input, output or credentials."""
        checks = {
            'executions': "SELECT id,task_id,agent_id,status,pid,started_at,retry_at,delivery FROM executions "
                          "WHERE finished_at IS NULL OR delivery='running' LIMIT 20",
            'tasks': "SELECT id,state,lease_owner,updated_at FROM tasks "
                     "WHERE lease_owner IS NOT NULL OR state='running' LIMIT 20",
            'messages': "SELECT id,execution_id,status FROM messages WHERE status IN ('queued','streaming') LIMIT 20",
        }
        with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            return {name: [dict(row) for row in conn.execute(sql)] for name, sql in checks.items()}

    def check_request(self, request):
        """A withdrawn request or a paused project stops the deploy before anything changes."""
        if not self.request():
            raise Stop('cancelled', 'request withdrawn before the restart')
        if (request.get('source') == 'auto' and request.get('project_id') and not request.get('emergency')
                and (read(self.root / 'emergency.json') or {}).get('id') != self.id):
            status = self.query('SELECT status FROM projects WHERE id=?', (request['project_id'],))
            if not status or status[0] != 'active':
                raise Stop('cancelled', 'project is paused or removed')

    def http(self, path, cookie=None):
        req = urllib.request.Request(self.url + path)
        if cookie:
            req.add_header('Cookie', 'ai_manager_session=' + cookie)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def running_commit(self):
        try:
            status, body = self.http('/api/auth-status')
            return json.loads(body).get('release_commit') if status == 200 else None
        except (OSError, ValueError):
            return None

    def health(self, commit):
        """App responds with the expected code, scheduler runs, auth boundary holds and a session works."""
        status, body = self.http('/api/auth-status')
        info = json.loads(body)
        if status != 200 or not info.get('scheduler_running'):
            raise RuntimeError('auth-status unhealthy or scheduler not running')
        if commit and info.get('release_commit') != commit:
            raise RuntimeError(f"service runs {str(info.get('release_commit'))[:10]}, expected {commit[:10]}")
        if self.http('/api/portfolio')[0] != 401:
            raise RuntimeError('authentication boundary failed')
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with sqlite3.connect(self.db, timeout=10) as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?)', (digest, secrets.token_hex(16), time.time() + 60))
        try:
            if self.http('/api/portfolio', token)[0] != 200:
                raise RuntimeError('authenticated request failed')
        finally:
            with sqlite3.connect(self.db, timeout=10) as conn:
                conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))

    def wait_health(self, commit):
        deadline = time.monotonic() + self.config.get('health_seconds', 60)
        while True:
            try:
                return self.health(commit)
            except Exception as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError('health check failed: ' + str(error)) from error
                self.sleep(1)

    # ---- database ------------------------------------------------------------------------------------------------
    def backup(self, destination):
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=30) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('backup failed the integrity check')
        os.chmod(destination, 0o600)

    def restore(self, source):
        temp = self.db.with_name(self.db.name + '.restore')
        temp.unlink(missing_ok=True)
        with sqlite3.connect(source) as src, sqlite3.connect(temp) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('backup to restore is corrupt')
        for suffix in ('-wal', '-shm'):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        os.replace(temp, self.db)

    def prune(self):
        directory = self.root / 'backups'
        for old in sorted(directory.glob('*'), key=lambda p: p.stat().st_mtime)[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)

    # ---- code ----------------------------------------------------------------------------------------------------
    def sync(self):
        if self.config.get('uv'):
            run([self.config['uv'], 'sync', '--locked'], cwd=self.repo, timeout=900)

    def deps_changed(self, old, new):
        return bool(old and self.git('diff', '--name-only', old, new, '--', 'pyproject.toml', 'uv.lock', check=False))

    def rollback(self, previous, target, synced):
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
        fenced = ExitStack()  # holds the checkout fence from the final idle check to the end
        def emergency_stop():
            nonlocal stopped
            # Explicit host-only escape hatch: stop the app under the existing checkout fence,
            # then ignore stale queue records. Never race a still-live native worker.
            fenced.enter_context(fence.hold(self.data, True, wait=5))
            self.state('emergency_backing_up')
            self.backup(backups / 'before.sqlite3')
            stopped = True
            run(self.commands['stop'], timeout=120)
            with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10) as conn:
                pids = conn.execute('SELECT pid,process_start FROM executions WHERE pid IS NOT NULL').fetchall()
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
                    previous = self.running_commit() or (read(self.root / 'last_good.json') or {}).get('commit')
                    if self.git('symbolic-ref', '-q', '--short', 'HEAD', check=False) != self.branch:
                        self.git('checkout', '--quiet', self.branch)
                    target = self.git('rev-parse', 'HEAD')
            except fence.Busy as error:
                raise Stop('failed', 'live checkout is busy: ' + str(error)) from error
            if previous == target and not request.get('emergency'):
                raise Stop('success', f'{target[:10]} is already live; nothing to deploy')
            tests = self.config.get('test_cmd')
            if tests and not request.get('skip_tests'):
                result = run(tests, cwd=self.repo, timeout=self.config.get('test_seconds', 3600), check=False)
                if result.returncode:
                    raise Stop('failed', 'full backend suite failed: ' + (result.stdout + result.stderr).strip()[-300:])
            if request.get('emergency'):
                emergency_stop()
            self.state('waiting_idle', commit=target)
            deadline = time.monotonic() + self.config.get('idle_seconds', IDLE_SECONDS)
            last_busy, reported_at = None, 0.0
            while True:
                self.check_request(request)
                if not request.get('emergency') and (read(self.root / 'emergency.json') or {}).get('id') == self.id:
                    request['emergency'] = True
                    emergency_stop()
                busy = {} if request.get('emergency') else self.busy()
                if not busy:
                    # Fence: from here until the end no merge and no new task/chat can start, so the idle
                    # check below cannot be invalidated before the stop. Work that slipped in releases the fence.
                    if request.get('emergency'):
                        break
                    try:
                        fenced.enter_context(fence.hold(self.data, True, wait=5))
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
                run(self.commands['stop'], timeout=120)
            self.backup(backups / 'stopped.sqlite3')  # consistent snapshot; nothing writes while stopped
            self.state('updating')
            changed = True
            self.git('checkout', '--quiet', self.branch)
            if previous and self.deps_changed(previous, target):
                self.sync()
                synced = True
            run(self.commands['start'], timeout=120)
            self.state('verifying')
            self.wait_health(target)
            (self.root / 'last_good.json').write_text(json.dumps({'commit': target, 'at': time.time()}))
            self.prune()
            return self.finish(request, 'success', f'deployed {target[:10]}; health check passed',
                               commit=target, previous=previous, backup=str(backups))
        except Stop as stop:
            if stopped:
                try:
                    run(self.commands['start'], timeout=120)
                    self.wait_health(previous)
                except Exception as restart_error:
                    return self.finish(request, 'rollback_failed', str(stop) + '; restart failed: ' + str(restart_error))
            return self.finish(request, stop.status, str(stop), commit=target, previous=previous)
        except Exception as error:
            if not stopped:
                return self.finish(request, 'failed', str(error), commit=target, previous=previous)
            self.state('rolling_back')
            problems = []
            for step, action in (('stop', lambda: run(self.commands['stop'], timeout=120, check=False)),
                                 ('code', lambda: changed and self.rollback(previous, target, synced)),
                                 ('database', lambda: changed and self.restore(backups / 'stopped.sqlite3'))):
                try:
                    action()
                except Exception as rollback_error:  # keep going: the service must still be started
                    problems.append(f'{step} rollback failed: {str(rollback_error)[:200]}')
            try:
                run(self.commands['start'], timeout=120)
                if not problems:
                    self.wait_health(previous)
            except Exception as start_error:
                problems.append(f'restart/health failed: {str(start_error)[:200]}')
            if problems:
                return self.finish(request, 'rollback_failed', f'{str(error)[:200]}; ' + '; '.join(problems),
                                   commit=target, previous=previous, backup=str(backups))
            if not changed:
                return self.finish(request, 'failed', str(error)[:300] + '; service restarted',
                                   commit=target, previous=previous)
            return self.finish(request, 'rolled_back', f'{str(error)[:300]}; code and database restored to '
                               f'{previous[:10]}', commit=target, previous=previous, backup=str(backups))
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

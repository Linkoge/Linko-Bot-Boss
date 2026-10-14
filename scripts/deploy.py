#!/usr/bin/env python3
"""The one deploy script. Contract: docs/AUTO_DEPLOY.md. Standard library only.

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
from pathlib import Path

IDLE = {
    'unfinished_executions': 'SELECT count(*) FROM executions WHERE finished_at IS NULL',
    'leases': 'SELECT count(*) FROM tasks WHERE lease_owner IS NOT NULL',
    'streaming_messages': "SELECT count(*) FROM messages WHERE status IN ('queued','streaming')",
    'deliveries': "SELECT count(*) FROM executions WHERE delivery='running'",
}
KEEP_BACKUPS = 10


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
        self.branch = config.get('branch', 'main')
        self.url = config.get('url', 'http://127.0.0.1:8787')
        systemctl = config.get('systemctl', ['systemctl', '--user'])
        service = config.get('service', 'linko-bot-manager.service')
        self.commands = {'stop': [*systemctl, 'stop', service], 'start': [*systemctl, 'start', service],
                         **config.get('service_cmds', {})}
        self.id = None

    # ---- bookkeeping --------------------------------------------------------------------------------------------
    def log(self, text):
        line = time.strftime('%Y-%m-%d %H:%M:%S ') + text
        print(line, flush=True)
        with (self.root / 'deploy.log').open('a') as stream:
            stream.write(line + '\n')

    def state(self, phase, **extra):
        atomic(self.root / 'state.json', {'id': self.id, 'phase': phase, 'at': time.time(), **extra})
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
        return result

    # ---- idle, holds, health -------------------------------------------------------------------------------------
    def query(self, sql, args=()):
        with sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10) as conn:
            return conn.execute(sql, args).fetchone()

    def busy(self):
        return {name: n for name, sql in IDLE.items() if (n := self.query(sql)[0])}

    def check_request(self, request):
        """Withdrawn request, owner OFF or paused project all stop the deploy before anything changes."""
        if not self.request():
            raise Stop('cancelled', 'request withdrawn before the restart')
        if request.get('source') == 'auto' and request.get('project_id'):
            row = self.query('SELECT value FROM settings WHERE key=?', ('auto_deploy:' + request['project_id'],))
            if not row or not json.loads(row[0]).get('enabled'):
                raise Stop('cancelled', 'owner turned Auto-deploy off')
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
        try:
            self.state('testing')
            if self.git('status', '--porcelain', '--untracked-files=no'):
                raise Stop('failed', 'live checkout has uncommitted changes')
            previous = self.running_commit() or (read(self.root / 'last_good.json') or {}).get('commit')
            if self.git('symbolic-ref', '-q', '--short', 'HEAD', check=False) != self.branch:
                self.git('checkout', '--quiet', self.branch)
            target = self.git('rev-parse', 'HEAD')
            if previous == target:
                raise Stop('success', f'{target[:10]} is already live; nothing to deploy')
            tests = self.config.get('test_cmd')
            if tests and not request.get('skip_tests'):
                result = run(tests, cwd=self.repo, timeout=self.config.get('test_seconds', 3600), check=False)
                if result.returncode:
                    raise Stop('failed', 'full backend suite failed: ' + (result.stdout + result.stderr).strip()[-300:])
            self.state('waiting_idle', commit=target)
            deadline = time.monotonic() + self.config.get('idle_seconds', 6 * 3600)
            while True:
                self.check_request(request)
                busy = self.busy()
                if not busy:
                    break
                if time.monotonic() >= deadline:
                    raise Stop('failed', 'not idle after waiting: ' + json.dumps(busy))
                self.sleep(self.config.get('poll_seconds', 5))
            self.state('backing_up')
            self.backup(backups / 'before.sqlite3')
            self.check_request(request)
            if self.busy():
                raise Stop('failed', 'work appeared during backup; will not interrupt it')
            self.state('restarting')
            stopped = True
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
            return self.finish(request, stop.status, str(stop), commit=target, previous=previous)
        except Exception as error:
            if not stopped:
                return self.finish(request, 'failed', str(error), commit=target, previous=previous)
            self.state('rolling_back')
            try:
                run(self.commands['stop'], timeout=120, check=False)
                if changed:
                    self.rollback(previous, target, synced)
                    self.restore(backups / 'stopped.sqlite3')
                run(self.commands['start'], timeout=120)
                self.wait_health(previous)
                return self.finish(request, 'rolled_back', f'{str(error)[:300]}; code and database restored to '
                                   f'{previous[:10]}', commit=target, previous=previous, backup=str(backups))
            except Exception as rollback_error:
                return self.finish(request, 'rollback_failed', f'{str(error)[:200]}; rollback failed: '
                                   f'{str(rollback_error)[:250]}', commit=target, previous=previous, backup=str(backups))

    def main(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / 'deploy.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                print('another deploy is running')
                return None
            request = read(self.root / 'request.json')
            if not request:
                print('no deploy requested')
                return None
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
    args = parser.parse_args(argv)
    os.umask(0o077)
    data = Path(args.data).expanduser()
    config = load_config(args.config, data)
    deploy = Deploy(config)
    if args.now and not (deploy.root / 'request.json').exists():
        atomic(deploy.root / 'request.json', {'id': secrets.token_hex(8), 'source': 'owner', 'reason': 'manual run',
                                              'requested_at': time.time()})
    result = deploy.main()
    return 0 if not result or result['status'] in {'success', 'cancelled'} else 1


if __name__ == '__main__':
    sys.exit(main())

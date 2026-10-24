#!/usr/bin/env python3
"""Host emergency repair. Standard library only; no application imports or queue writes."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

FINAL = {'succeeded', 'failed'}
DEFAULT_TURN_SECONDS = 7200
INSTRUCTIONS = """You are the owner's host agent, outside the normal task queue. You carry out whatever host work the
owner's request describes: installs and packages, permissions/ACLs, users, services, Syncthing-style setup, reboots,
config, and repairing the application. Normal queue worker rules do not apply to you.
Read the current README and implementation before acting. If the work is reasonable, just do it, with no extra caution
or confirmation. You may restart the application's user service and invoke scripts/deploy.py --now --emergency, which
preserves backup, health checks and rollback. Do not wait on or enqueue normal tasks. Preserve all data,
workspaces and recovery protections.
STOP and return a failed result asking for specific owner confirmation only in two cases:
(1) irreversible loss: deleting data or files that cannot be restored (repository, volume, database, unbacked-up user
files, force-push); (2) weakening security: opening the machine to attack, e.g. exposing services publicly, disabling
the firewall or authentication, opening up credentials or SSH keys, broad world-readable or world-writable permissions,
or adding unauthenticated remote access. Reversible changes such as installs, moving files with a kept copy,
permissions scoped to the needed user, services and users need no confirmation. No unattended approval of those two cases.
You may be resuming an interrupted attempt: inspect retained logs, native history, git, service and
deploy receipts FIRST; reconcile effects before repeating any operation. Do not blindly replay.
Report what changed, verification, remaining problems and whether owner action is needed.
End an unsuccessful or blocked request with RESULT: FAILED and its reason.
This role is authorized to restart/deploy; normal queue worker restrictions do not apply.
"""


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.repair-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def locked(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield stream


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def drop_privileges():
    """Started as root through the sudoers rule of scripts/install_worker_user.py: keep working with the service user's
    effective ids, so the spool, logs and database stay its files. Returns (uid, gid), or None when not root."""
    uid, gid = os.environ.get('SUDO_UID', ''), os.environ.get('SUDO_GID', '')
    if os.geteuid() != 0 or not (uid.isdigit() and gid.isdigit()) or uid == '0':
        return None
    os.setgroups(os.getgrouplist(pwd.getpwuid(int(uid)).pw_name, int(gid)))
    os.setegid(int(gid))
    os.seteuid(int(uid))
    return int(uid), int(gid)


@contextlib.contextmanager
def elevated(invoker):
    """Become root again only around starting the host agent (the real uid stayed root)."""
    if not invoker:
        yield
        return
    os.seteuid(0)
    os.setegid(0)
    try:
        yield
    finally:
        os.setegid(invoker[1])
        os.seteuid(invoker[0])


LIMIT_WORDS = ('usage limit', 'rate limit', 'usage_limit', 'rate_limit', 'usagelimit', 'ratelimit', 'quota', '429')


class Repair:
    def __init__(self, data, config=None, invoker=None):
        self.data = Path(data)
        self.root = self.data / 'repair'
        self.config = config if config is not None else read(self.root / 'installed.json', {})
        self.invoker = invoker  # (uid, gid) of the service user when this launcher runs as root

    def agent(self):
        """Popen options for the host agent: root (with root's own provider login) when started through sudo."""
        if not self.invoker:
            return {}
        home = pwd.getpwuid(0).pw_dir
        env = {**os.environ, 'HOME': home, 'USER': 'root', 'LOGNAME': 'root',
               'PATH': self.config.get('path') or os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}
        return {'user': 0, 'group': 0, 'extra_groups': [], 'env': env}

    def records(self):
        return [read(p) for p in sorted((self.root / 'requests').glob('*.json'))]

    def save(self, row):
        row['updated_at'] = time.time()
        atomic(self.root / 'requests' / (row['id'] + '.json'), row)

    def notice(self, row, text):
        row.setdefault('notices', []).append(text)
        self.save(row)

    def submit(self, key, instructions, conversation, provider=None, model=None, effort=None):
        if (not isinstance(key, str) or not key or not isinstance(instructions, str) or not instructions.strip()
                or not isinstance(conversation, str) or not conversation):
            raise ValueError('request key, instructions and originating conversation are required')
        if not self.config.get('repo'):
            raise ValueError('Repair not installed: run scripts/install_repair.py on the host first')
        with locked(self.root / 'state.lock'):
            rows = self.records()
            for row in rows:
                if row['key'] == key:
                    return row
            for row in rows:
                if row['status'] not in FINAL:
                    # Do not silently lose another chat's request or its instructions.
                    raise ValueError('Repair already active: ' + row['id'])
            # The app records its saved worker selection; the installed provider is only the fallback without one.
            fallback = not provider
            row = {'id': hashlib.sha256(key.encode()).hexdigest()[:32], 'key': key,
                   'instructions': instructions, 'conversation_id': conversation,
                   'provider': provider or self.config.get('provider') or 'codex',
                   'model': model or (self.config.get('model') if fallback else None), 'effort': effort,
                   'fallback_provider': fallback, 'status': 'pending', 'attempts': 0,
                   'retry_at': 0, 'created_at': time.time(), 'delivered': 0, 'delivery_attempts': 0}
            if row['provider'] not in {'codex', 'claude'}:
                raise ValueError('provider must be codex or claude')
            self.notice(row, 'Emergency repair accepted: ' + row['id'] + ' on ' + self.runs_on(row))
            return row

    @staticmethod
    def runs_on(row):
        text = row['provider'].title() + (' ' + row['model'] if row.get('model') else '')
        if row.get('effort'):
            text += ' (' + row['effort'] + ')'
        return text + (' [fallback provider: the app gave no selection]' if row.get('fallback_provider') else '')

    def healthy(self):
        try:
            with urllib.request.urlopen(self.config.get('url', 'http://127.0.0.1:8787') + '/api/auth-status',
                                        timeout=5) as response:
                return response.status == 200 and json.load(response).get('scheduler_running', False)
        except (OSError, ValueError):
            return False

    def detect(self):
        """Opt-in host watchdog. Only bounded read-only inspection, never task mutation."""
        if not self.config.get('auto_detect'):
            return
        now = time.time()
        monitor = read(self.root / 'monitor.json', {})
        healthy = self.healthy()
        if healthy:
            monitor.pop('down_since', None)
        else:
            monitor.setdefault('down_since', now)
        atomic(self.root / 'monitor.json', monitor)
        incident = None
        if now - monitor.get('down_since', now) >= self.config.get('stuck_seconds', 1800):
            incident = ('outage:' + str(monitor['down_since']), 'Application health unavailable for 30 minutes.')
        try:
            with sqlite3.connect(f'file:{self.data / "manager.sqlite3"}?mode=ro', uri=True, timeout=2) as db:
                row = db.execute("""SELECT t.id, MAX(t.updated_at, COALESCE((SELECT MAX(e.created_at)
                    FROM events e WHERE e.task_id=t.id),0)) AS activity FROM tasks t
                    WHERE t.state='running' AND NOT EXISTS(SELECT 1 FROM questions q
                    WHERE q.execution_id=t.lease_owner AND q.status='pending') ORDER BY activity LIMIT 1""").fetchone()
                if row and now - row[1] >= self.config.get('stuck_seconds', 1800):
                    incident = ('stuck:' + row[0] + ':' + str(row[1]), 'Task has no activity: ' + row[0])
        except sqlite3.Error:
            pass  # Outage detection still works with no database/schema.
        if incident:
            try:
                self.submit('watchdog:' + incident[0], incident[1] + '\nDiagnose and repair; preserve queued work.',
                            self.config['conversation_id'])
            except ValueError:
                pass  # An active repair owns the incident; never start a second one.

    def deliver(self, row):
        if row['delivered'] >= len(row.get('notices', [])) or time.time() < row.get('delivery_retry_at', 0):
            return
        if row.get('delivery_attempts', 0) >= 10 or not self.healthy():
            return  # Offline health probes do not consume delivery attempts.
        try:
            with sqlite3.connect(f'file:{self.data / "manager.sqlite3"}?mode=rw', uri=True, timeout=2) as db:
                if not db.execute('SELECT 1 FROM conversations WHERE id=?', (row['conversation_id'],)).fetchone():
                    raise ValueError('Original conversation missing; result retained on host')
                for index, content in enumerate(row['notices']):
                    # Stable message IDs make crash/retry and database rollback redelivery idempotent.
                    db.execute('INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) '
                               'VALUES(?,?,?,?,?,?)', ('repair-' + row['id'] + '-' + str(index),
                               row['conversation_id'], 'assistant', content, 'completed', time.time()))
            row['delivered'] = len(row['notices'])
            row['delivery_error'] = ''
            row['delivery_attempts'] = 0
        except (sqlite3.Error, ValueError) as error:
            row['delivery_attempts'] = row.get('delivery_attempts', 0) + 1
            row['delivery_error'] = str(error)
            row['delivery_retry_at'] = time.time() + min(3600, 30 * 2 ** row['delivery_attempts'])
        self.save(row)

    def command(self, row, output):
        binary = self.config.get(row['provider'] + '_bin', row['provider'])
        model = ['--model', row['model']] if row.get('model') else []
        if row['provider'] == 'codex':
            args = [binary, 'exec']
            if row.get('session'):
                args += ['resume', row['session']]
            if row.get('effort'):
                model += ['-c', 'model_reasoning_effort="' + row['effort'] + '"']
            return args + ['--json', '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check', *model,
                           '--output-last-message', str(output), '-']
        args = [binary, '-p', '--output-format', 'stream-json', '--verbose', '--strict-mcp-config',
                '--permission-mode', 'bypassPermissions', *model]
        if row.get('session'):
            args += ['--resume', row['session']]
        return args

    def alternative(self, row):
        """The other provider when its CLI is configured or on PATH, else None."""
        other = {'codex': 'claude', 'claude': 'codex'}.get(row['provider'])
        return other if other and shutil.which(self.config.get(other + '_bin', other)) else None

    def provider(self, row, lock):
        """One bounded CLI turn. Child inherits exclusivity even if this launcher dies."""
        directory = self.root / 'runs' / row['id']
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = directory / f"{row['attempts']}.txt"
        log = directory / f"{row['attempts']}.jsonl"
        prompt = INSTRUCTIONS + '\nHost repository: ' + self.config['repo'] + '\nRequest: ' + row['instructions'] + '\nDurable receipt: ' + str(
            self.root / 'requests' / (row['id'] + '.json')) + '\nRetained logs: ' + str(directory)
        if self.invoker:
            name = pwd.getpwuid(self.invoker[0]).pw_name
            prompt += (f'\nYou run as root. The application belongs to user {name}: run its commands, Git, deploy script '
                       f'and provider CLIs as that user (runuser -u {name} -- ...), manage its user services with '
                       f'systemctl --user -M {name}@ ..., and leave files under its home owned by it.')
        if row['attempts'] > 1:
            prompt += '\nRECOVERY: prior attempt interrupted/failed; reconcile its effects before proceeding.'
        if row.get('switched_from'):
            prompt += (f"\nThe earlier attempt(s) ran on {row['switched_from'].title()}; "
                       'its session cannot be resumed here, so read the retained logs.')
        with log.open('w') as stream, tempfile.TemporaryFile() as stdin:
            stdin.write(prompt.encode())
            stdin.seek(0)
            cwd = self.config['repo'] if Path(self.config['repo']).is_dir() else self.data
            with elevated(self.invoker):
                process = subprocess.Popen(self.command(row, output), cwd=cwd, stdin=stdin,
                                           stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                           pass_fds=(lock.fileno(),), **self.agent())
            row['pid'], row['process_start'] = process.pid, identity(process.pid)
            row['started_at'] = time.time()
            row['deadline'] = row['started_at'] + self.config.get('turn_seconds', DEFAULT_TURN_SECONDS)
            self.save(row)
            try:
                process.wait(timeout=self.config.get('turn_seconds', DEFAULT_TURN_SECONDS))
            except subprocess.TimeoutExpired:
                raise TimeoutError('Repair turn timed out')
            finally:
                # Also reap command descendants after the CLI exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        return self.outcome(row, log, output, process.returncode)

    def outcome(self, row, log, output, returncode):
        text, error, completed = '', '', False
        for line in log.read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'thread.started':
                row['session'] = event['thread_id']
            if event.get('session_id'):
                row['session'] = event['session_id']
            if event.get('type') in {'result', 'turn.completed'}:
                completed = True
            if event.get('type') == 'result':
                text = event.get('result', '')
                if event.get('is_error'):
                    error = str(event.get('result') or event.get('errors') or event)
            if event.get('type') in {'error', 'turn.failed'}:
                error = str(event.get('message') or event.get('error') or event)
        self.save(row)
        if output.exists():
            text = output.read_text()
        diagnostics = (error or log.read_text(errors='replace')[-4000:]).lower()
        limit = any(word in diagnostics for word in LIMIT_WORDS)
        retryable = limit or any(word in diagnostics for word in (
            'connection', 'network', 'overloaded', '503', '502', 'timed out'))
        if returncode or error:
            raise ProviderFailure(error or diagnostics, retryable, limit)
        if not completed or not text.strip():
            raise ProviderFailure('Provider returned no final result', False)
        if 'RESULT: FAILED' in text:
            raise ProviderFailure(text, False)
        return text

    def run(self, provider=None):
        try:
            with locked(self.root / 'run.lock', False) as lock:
                self.detect()
                for row in self.records():
                    self.deliver(row)
                    if row['status'] in FINAL or row.get('retry_at', 0) > time.time():
                        continue
                    # Reconcile a CLI that finished before its parent could save the result.
                    directory = self.root / 'runs' / row['id']
                    previous_log = directory / f"{row['attempts']}.jsonl"
                    # A log from before a provider switch was already handled; replaying it would restore its session.
                    if previous_log.exists() and row.get('switched_after') != row['attempts']:
                        try:
                            result = self.outcome(row, previous_log, directory / f"{row['attempts']}.txt", 0)
                        except ProviderFailure:
                            pass  # Session identity was retained; next turn explicitly reconciles effects.
                        else:
                            row['status'], row['result'] = 'succeeded', result
                            self.notice(row, 'Emergency repair succeeded (recovered):\n' + result)
                            self.deliver(row)
                            return
                    if row['attempts'] >= self.config.get('max_attempts', 5):
                        row['status'] = 'failed'
                        row['result'] = 'Retry budget exhausted: ' + row.get('error', 'interrupted')
                        self.notice(row, 'Emergency repair failed: retry budget exhausted. ' + row.get('error', ''))
                        self.deliver(row)
                        continue
                    row['attempts'] += 1
                    row['status'] = 'running'
                    self.notice(row, f"Emergency repair {row['id']} running on {self.runs_on(row)}, attempt {row['attempts']}.")
                    self.deliver(row)
                    try:
                        result = (provider or self.provider)(row, lock)
                        row['status'], row['result'] = 'succeeded', result
                        after = (f" on {row['provider'].title()} after switching from {row['switched_from'].title()}"
                                 if row.get('switched_from') else '')
                        self.notice(row, 'Emergency repair succeeded' + after + ':\n' + result)
                    except Exception as error:
                        row['error'] = str(error)[-8000:]
                        retryable = isinstance(error, (TimeoutError, ConnectionError)) or (
                            isinstance(error, ProviderFailure) and error.retryable)
                        if retryable and row['attempts'] < self.config.get('max_attempts', 5):
                            other = self.alternative(row) if getattr(error, 'limit', False) else None
                            row['status'] = 'waiting'
                            if other:
                                # A usage limit will not clear soon: retry now on the other provider.
                                old = row['provider']
                                row['provider'], row['switched_from'], row['switched_after'] = other, old, row['attempts']
                                row.pop('session', None)
                                row['model'], row['effort'] = self.config.get(other + '_model'), None
                                row['fallback_provider'] = False
                                row['retry_at'] = 0
                                self.notice(row, f'Emergency repair: {old.title()} usage limit; switching to '
                                                 f'{other.title()}. ' + row['error'])
                            else:
                                row['retry_at'] = time.time() + min(3600, 60 * 2 ** (row['attempts'] - 1))
                                self.notice(row, 'Emergency repair waiting for bounded retry: ' + row['error'])
                        else:
                            row['status'] = 'failed'
                            row['result'] = row['error']
                            self.notice(row, 'Emergency repair failed:\n' + row['error'])
                    self.deliver(row)
                    return
        except BlockingIOError:
            # An orphan CLI retains the inherited lock. Only kill our verified group after its deadline.
            for row in self.records():
                pid = row.get('pid')
                if (row['status'] == 'running' and pid and row.get('process_start')
                        and identity(pid) == row['process_start'] and time.time() > row.get('deadline', float('inf'))):
                    if os.getpgid(pid) == pid:
                        os.killpg(pid, signal.SIGKILL)
            return


class ProviderFailure(RuntimeError):
    def __init__(self, message, retryable, limit=False):
        super().__init__(message)
        self.retryable = retryable
        self.limit = limit  # usage/rate/quota exhaustion, which another provider may not share


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(os.getenv(
        'AI_MANAGER_DATA', '~/.local/share/linko-bot-manager')).expanduser())
    commands = parser.add_subparsers(dest='command', required=True)
    submit = commands.add_parser('request')
    submit.add_argument('--key', required=True)
    submit.add_argument('--instructions-file', type=Path, required=True)
    submit.add_argument('--conversation', required=True)
    submit.add_argument('--provider', choices=['codex', 'claude'])
    submit.add_argument('--model')
    commands.add_parser('tick')
    commands.add_parser('status')
    commands.add_parser('retry-delivery')
    args = parser.parse_args(argv)
    repair = Repair(args.data, invoker=drop_privileges())
    if args.command == 'request':
        print(json.dumps(repair.submit(args.key, args.instructions_file.read_text(), args.conversation,
                                       args.provider, args.model), indent=2))
    elif args.command == 'tick':
        repair.run()
    elif args.command == 'retry-delivery':
        with locked(repair.root / 'run.lock', False):
            for row in repair.records():
                row['delivered'], row['delivery_attempts'], row['delivery_retry_at'] = 0, 0, 0
                repair.save(row)
                repair.deliver(row)
    else:
        print(json.dumps(repair.records(), indent=2))


if __name__ == '__main__':
    main()

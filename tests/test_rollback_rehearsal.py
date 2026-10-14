"""Throwaway-copy rehearsal of post-deploy health failure and automatic rollback.

Real Git checkout, release bundle, snapshot, HTTP health/auth/asset probes and SQLite preservation checks.
Only systemctl is simulated by starting/stopping a local server process from the checkout, so the live
service is never touched. LINKO_REAL_HEALTH_CLOCK=1 keeps the helper's real 45s health deadline.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from types import SimpleNamespace

import pytest

import ai_manager.deployment as deployment
from ai_manager.db import Database
from ai_manager.deployment import Handoff, Helper, atomic, sha

SERVER = '''import hashlib, http.server, json, os, sqlite3, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parent
HEALTHY = True
COMMIT = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code, body = 404, b''
        if self.path == '/api/auth-status':
            code, body = 200, json.dumps({'release_commit': COMMIT, 'scheduler_running': HEALTHY}).encode()
        elif self.path == '/api/portfolio':
            token = self.headers.get('Cookie', '').partition('ai_manager_session=')[2]
            with sqlite3.connect(os.environ['DB']) as conn:
                found = token and conn.execute('SELECT 1 FROM sessions WHERE token_hash=?',
                                               (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            code, body = (200, b'{}') if found else (401, b'{}')
        elif self.path.startswith('/static/') and (ROOT / 'ai_manager' / self.path[1:]).is_file():
            code, body = 200, (ROOT / 'ai_manager' / self.path[1:]).read_bytes()
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), Handler).serve_forever()
'''


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def assets(repo, ref):
    names = git(repo, 'ls-tree', '-r', '--name-only', ref, '--', 'ai_manager/static').splitlines()
    return {n.removeprefix('ai_manager/static/'): hashlib.sha256(subprocess.run(
        ['git', '-C', str(repo), 'show', ref + ':' + n], capture_output=True, check=True).stdout).hexdigest() for n in names}


class FakeSystemd:
    """Stands in for `systemctl --user` on the throwaway checkout only."""

    def __init__(self, repo, port, database):
        self.repo, self.port, self.database = repo, port, database
        self.process, self.operations = None, []

    def __call__(self, argv, cwd=None):
        assert argv[:2] == ['systemctl', '--user'] and argv[3] == 'linko-bot-manager.service'
        operation = argv[2]
        self.operations.append(operation)
        if operation == 'stop':
            self.stop()
        elif operation == 'start':
            self.process = subprocess.Popen([sys.executable, '-I', str(self.repo / 'server.py')], cwd=self.repo,
                                            env={**os.environ, 'PORT': str(self.port), 'DB': str(self.database)})
            deadline = time.monotonic() + 10
            while True:  # Like Type=notify: return once the listener accepts connections.
                try:
                    socket.create_connection(('127.0.0.1', self.port), 0.2).close()
                    break
                except OSError:
                    assert time.monotonic() < deadline and self.process.poll() is None, 'server did not start'
                    time.sleep(0.05)
        elif operation == 'is-active':
            return 'active' if self.process and self.process.poll() is None else 'inactive'
        elif operation == 'show':
            return f'MainPID={self.process.pid}'
        return ''

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(10)


@pytest.fixture
def rehearsal(tmp_path, monkeypatch):
    live, data = tmp_path / 'live', tmp_path / 'data'
    (live / 'ai_manager/static').mkdir(parents=True)
    git(live, 'init', '-b', 'main')
    git(live, 'config', 'user.email', 'test@example.invalid')
    git(live, 'config', 'user.name', 'Test')
    (live / 'server.py').write_text(SERVER)
    (live / 'ai_manager/static/app.js').write_text('console.log("base")\n')
    git(live, 'add', '.')
    git(live, 'commit', '-m', 'base')
    db = Database(data / 'manager.sqlite3')
    with db.transaction() as conn:
        conn.execute("INSERT INTO conversations(id,context,created_at) VALUES('origin','global',0)")
        conn.execute("INSERT INTO settings VALUES('global_instructions',?)", (json.dumps('keep'),))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    systemd = FakeSystemd(live, port, db.path)
    real_run = deployment.run
    monkeypatch.setattr(deployment, 'run', lambda argv, cwd=None: systemd(argv) if argv[0] == 'systemctl' else real_run(argv, cwd))
    # This suite may run inside the live service cgroup; the throwaway helper is external by construction.
    monkeypatch.setattr(Helper, 'check_external', lambda self: None)
    if not os.environ.get('LINKO_REAL_HEALTH_CLOCK'):
        clock = SimpleNamespace(offset=0.0)
        monkeypatch.setattr(deployment, 'time', SimpleNamespace(
            time=time.time, monotonic=lambda: time.monotonic() + clock.offset,
            sleep=lambda seconds: (time.sleep(0.05), setattr(clock, 'offset', clock.offset + seconds * 10))))
    systemd('systemctl --user start linko-bot-manager.service'.split())
    yield live, data, db, systemd, port
    systemd.stop()


def prepare(live, data, healthy):
    """Candidate built in a separate clone and shipped as a pinned bundle, like prepare_deployment."""
    base = git(live, 'rev-parse', 'HEAD')
    candidate = live.parent / 'candidate'
    subprocess.run(['git', 'clone', '-q', str(live), str(candidate)], check=True)
    git(candidate, 'config', 'user.email', 'test@example.invalid')
    git(candidate, 'config', 'user.name', 'Test')
    (candidate / 'ai_manager/static/app.js').write_text('console.log("candidate")\n')
    if not healthy:
        (candidate / 'server.py').write_text(SERVER.replace('HEALTHY = True', 'HEALTHY = False'))
    git(candidate, 'commit', '-qam', 'candidate')
    commit = git(candidate, 'rev-parse', 'HEAD')
    release = Handoff(data).root / 'releases' / 'release-1'
    release.mkdir(parents=True)
    (release / 'validation.log').write_text('full suite passed once\n')
    git(candidate, 'bundle', 'create', str(release / 'release.bundle'), 'HEAD')
    atomic(release / 'manifest.json', {
        'id': 'release-1', 'base': base, 'commit': commit, 'assets': assets(candidate, commit),
        'base_assets': assets(live, base), 'bundle_sha256': sha(release / 'release.bundle'),
        'validation_sha256': sha(release / 'validation.log')})
    return base, commit


def served(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/auth-status', timeout=5) as response:
        return json.loads(response.read())


@pytest.mark.parametrize('healthy', [False, True], ids=['unhealthy-rolls-back', 'healthy-control'])
def test_post_deploy_health_failure_rolls_back_throwaway_copy(rehearsal, healthy):
    live, data, db, systemd, port = rehearsal
    base, commit = prepare(live, data, healthy)
    handoff = Handoff(data)
    handoff.request('release-1', origin={'conversation_id': 'origin'})
    handoff.acknowledge(db)
    before = db.all('SELECT * FROM conversations')
    helper = Helper({'repo': str(live), 'data': str(data), 'native': str(data / 'native'), 'preserve_files': [],
                     'loopback': f'http://127.0.0.1:{port}', 'https': f'http://localhost:{port}'})
    result = helper.execute()
    receipt = json.loads((handoff.root / 'results' / (result['nonce'] + '.json')).read_text())
    assert receipt['phase'] == result['phase']
    backup = handoff.root / 'backups' / result['nonce']
    assert {p.name for p in backup.iterdir()} >= {'manager.sqlite3', 'final.sqlite3', 'source.bundle', 'receipt.json'}
    assert db.all('SELECT * FROM conversations') == before  # Rollback never restores/rewrites the database.
    assert not db.all('SELECT * FROM sessions')  # Authenticated probe sessions are removed.
    if healthy:
        assert result['phase'] == 'succeeded' and result['health']['commit'] == commit
        assert git(live, 'rev-parse', 'HEAD') == commit and served(port)['scheduler_running']
        assert [o for o in systemd.operations if o in {'start', 'stop'}] == ['start', 'stop', 'start']
        return
    assert result['phase'] == 'rolled_back' and 'Release/scheduler health mismatch' in result['error']
    assert git(live, 'rev-parse', 'HEAD') == base and git(live, 'rev-parse', 'main') == commit
    assert served(port) == {'release_commit': base, 'scheduler_running': True}
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/app.js', timeout=5) as response:
        assert response.read() == b'console.log("base")\n'
    assert [o for o in systemd.operations if o in {'start', 'stop'}] == ['start', 'stop', 'start', 'stop', 'start']
    handoff.report(db)
    handoff.report(db)
    assert [m['content'] for m in db.all("SELECT content FROM messages WHERE conversation_id='origin'")] == [
        'Deployment rolled_back. Activation needs attention.']

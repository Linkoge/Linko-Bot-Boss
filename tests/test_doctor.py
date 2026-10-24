"""Installation checks use disposable config/binaries and never touch user services."""
import os
import sys
from pathlib import Path

import pytest

from ai_manager import doctor


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    config = home / '.config'
    config.mkdir(parents=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('node', 'claude', 'codex', 'ffmpeg', 'ffprobe'):
        binary = binaries / name
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o700)
    data = home / 'data'
    (data / 'repair').mkdir(parents=True)
    for name in ('installed.json', 'launcher.py'):
        (data / 'repair' / name).write_text('{}')
    (data / 'deploy').mkdir()
    (data / 'deploy/installed.json').write_text('{"instances": []}')
    secret = 'xai-' + os.urandom(24).hex()
    env = config / 'linko-bot-manager.env'
    env.write_text(f'PATH={binaries}\nAI_MANAGER_DATA={data}\nXAI_API_KEY={secret}\n'
                   'AI_MANAGER_ALLOWED_HOSTS=localhost,example.test\nAI_MANAGER_PORT=9999\n')
    env.chmod(0o600)
    head = 'a' * 40
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'git':
            return 0, head
        if argv[0] == 'loginctl':
            return 0, 'yes'
        if 'show' in argv:
            service = argv[3]
            name = service.split('@')[1].split('.')[0] if '@' in service else ''
            env_name = 'linko-bot-manager' + (f'-{name}' if name else '') + '.env'
            return 0, ('LoadState=loaded\nUnitFileState=enabled\nActiveState=active\nMainPID=0\n'
                       f'EnvironmentFiles={config / env_name} (ignore_errors=yes)\nEnvironment=')
        return 0, ''

    monkeypatch.setattr(doctor, 'command', command)
    monkeypatch.setattr(doctor, 'health', lambda *a: {'release_commit': head, 'scheduler_running': True})
    output = []
    check = doctor.Doctor(home=home, repo=tmp_path, output=output.append)
    return check, env, binaries, secret, calls


def test_all_good(installation):
    check, _, _, secret, calls = installation
    assert check.run() == 0
    assert all(line.startswith(('PASS ', 'WARN ')) for line in check.output.__self__)
    assert any(argv[-1] == '--version' for argv in calls)
    assert secret not in '\n'.join(check.output.__self__)


def test_missing_env_fails(installation):
    check, env, *_ = installation
    env.unlink()
    assert check.run() == 1
    assert any('FAIL' in line and 'env file' in line for line in check.output.__self__)


def test_codex_present_node_missing(installation):
    check, _, binaries, *_ = installation
    (binaries / 'codex').write_text('#!/usr/bin/env node\n')
    (binaries / 'node').unlink()
    assert check.run() == 1
    assert any('FAIL' in line and 'node runtime' in line for line in check.output.__self__)


def test_release_mismatch_and_no_secrets(installation, monkeypatch):
    check, _, _, secret, _ = installation
    monkeypatch.setattr(doctor, 'health', lambda *a: {'release_commit': 'b' * 40, 'scheduler_running': True})
    assert check.run() == 1
    output = '\n'.join(check.output.__self__)
    assert 'FAIL linko-bot-manager.service release matches' in output
    assert secret not in output


def test_permissions_and_malformed_env(installation):
    check, env, _, secret, _ = installation
    env.chmod(0o644)
    env.write_text(env.read_text() + f'BAD="{secret}\n')
    assert check.run() == 1
    output = '\n'.join(check.output.__self__)
    assert 'FAIL linko-bot-manager.service env permissions' in output
    assert secret not in output


def test_every_instance_release_checked(installation, monkeypatch):
    check, env, *_ = installation
    instance = env.with_name('linko-bot-manager-kaxa.env')
    instance.write_text(env.read_text().replace('9999', '9998'))
    instance.chmod(0o600)
    def health(url, *args):
        return {'scheduler_running': True, 'release_commit': ('b' if ':9998' in url else 'a') * 40}
    monkeypatch.setattr(doctor, 'health', health)
    assert check.run() == 1
    assert any('FAIL linko-bot-manager@kaxa.service release matches' in line for line in check.output.__self__)


def test_probe_timeout_and_discard_output():
    assert doctor.command(['/does/not/exist']) == (-1, '')
    # A real subprocess validates the short timeout, independent of the fake services.
    result = doctor.command([sys.executable, '-c', 'import time; time.sleep(10)'])
    assert result == (-1, '')


def test_cli_does_not_prepare_data(monkeypatch, installation):
    from ai_manager import cli
    check, *_ = installation
    monkeypatch.setattr(doctor, 'main', check.run)
    monkeypatch.setattr(cli.Settings, 'prepare', lambda self: pytest.fail('doctor wrote data'))
    monkeypatch.setattr(sys, 'argv', ['cli', 'doctor'])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0


def test_docs_link():
    root = Path(__file__).resolve().parents[1]
    assert (root / 'docs/INSTALL.md').is_file()
    assert 'docs/INSTALL.md' in (root / 'README.md').read_text()


@pytest.mark.parametrize('property,value,label', [
    ('UnitFileState', 'disabled', 'UnitFileState'),
    ('ActiveState', 'inactive', 'ActiveState'),
    ('EnvironmentFiles', '/wrong/file (ignore_errors=yes)', 'EnvironmentFile'),
])
def test_systemd_failures(installation, monkeypatch, property, value, label):
    check, *_ = installation
    original = doctor.command
    def command(argv, **kwargs):
        code, text = original(argv, **kwargs)
        if 'show' in argv:
            text = '\n'.join(f'{property}={value}' if line.startswith(property + '=') else line
                             for line in text.splitlines())
        return code, text
    monkeypatch.setattr(doctor, 'command', command)
    assert check.run() == 1
    assert any(line.startswith('FAIL ') and label in line for line in check.output.__self__)


def test_subprocess_output_never_echoed(installation, monkeypatch):
    check, _, _, secret, _ = installation
    original = doctor.command
    def command(argv, **kwargs):
        if argv[-1] == '--version':
            return 1, secret
        return original(argv, **kwargs)
    monkeypatch.setattr(doctor, 'command', command)
    def broken_health(*args):
        raise ValueError(secret)
    monkeypatch.setattr(doctor, 'health', broken_health)
    assert check.run() == 1
    assert secret not in '\n'.join(check.output.__self__)


def test_actual_codex_launcher_uses_service_path(installation):
    check, _, binaries, *_ = installation
    (binaries / 'codex').write_text('#!/usr/bin/env node\n')
    (binaries / 'node').unlink()
    # Use the real timeout-bounded executor for the fake npm-style launcher.
    import subprocess
    env = {'PATH': str(binaries)}
    result = subprocess.run([str(binaries / 'codex'), '--version'], env=env,
                            capture_output=True, timeout=doctor.TIMEOUT)
    assert result.returncode != 0
    check.providers(env, 'test')
    assert check.failed


def test_health_uses_expected_path_and_no_proxy(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers['Host'], self.headers.get('Cookie')))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"scheduler_running":true,"release_commit":"test"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    try:
        assert doctor.health(f'http://127.0.0.1:{server.server_port}')['scheduler_running'] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert seen == [('/api/auth-status', 'localhost', None)]

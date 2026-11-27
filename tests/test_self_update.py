"""Bot Boss version, machine version labels and auto-update from GitHub (ai_manager/self_update.py)."""
import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager import self_update
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.pipeline import Pipeline, read
from ai_manager.registry import Registry
from ai_manager.self_update import SelfUpdate, compare, decide, format_version, version_info
from ai_manager.web import create_app

VERSION = re.compile(r'^\d{4}\.\d{2}\.\d{2}\.\d+$')


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                          capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def commit(root, name, text='x'):
    (root / name).write_text(text)
    git(root, 'add', name)
    git(root, 'commit', '-q', '-m', 'add ' + name)


@pytest.fixture
def repos(tmp_path):
    """A bare GitHub stand-in, a second clone that pushes, and the deployed checkout."""
    origin, upstream, checkout = tmp_path / 'origin.git', tmp_path / 'upstream', tmp_path / 'checkout'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True, timeout=60)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(upstream)], check=True, timeout=60)
    commit(upstream, 'a.txt')
    git(upstream, 'remote', 'add', 'origin', str(origin))
    git(upstream, 'push', '-q', 'origin', 'main')
    subprocess.run(['git', 'clone', '-q', str(origin), str(checkout)], check=True, timeout=60)
    return origin, upstream, checkout


@pytest.fixture
def updater(tmp_path, repos, monkeypatch):
    monkeypatch.delenv(self_update.ROLE_ENV, raising=False)
    _, upstream, checkout = repos
    db = Database(tmp_path / 'data' / 'manager.sqlite3')
    registry = Registry(db, (tmp_path,))
    pipeline = Pipeline(db, registry)
    (pipeline.root).mkdir(parents=True, exist_ok=True)
    (pipeline.root / 'installed.json').write_text(json.dumps({'repo': str(checkout)}))
    return SelfUpdate(db, registry, pipeline, root=checkout), upstream, checkout


def test_version_format_is_date_count_and_short_hash(repos):
    _, upstream, _ = repos
    commit(upstream, 'b.txt')
    info = version_info(upstream)
    assert set(info) == {'version', 'commit', 'commit_count'}
    assert VERSION.match(info['version']) and info['commit_count'] == 2
    assert info['version'].endswith('.2') and git(upstream, 'rev-parse', 'HEAD').startswith(info['commit'])
    assert len(info['commit']) == 7
    assert format_version('2026-11-28', 1873, 'c4fe69b0123') == ('2026.11.28.1873', 'c4fe69b')
    assert self_update.label({'version': '2026.11.28.1873', 'commit': 'c4fe69b', 'commit_count': 1873}) == \
        '2026.11.28.1873 (c4fe69b)'
    assert version_info(repos[0].parent / 'not-a-repo')['version'] == 'unknown'


@pytest.fixture
def client(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as client:
        yield client, app, settings


def sign_in(client, settings):
    token = (settings.data_dir / 'setup-token').read_text()
    client.post('/api/setup', json={'username': 'owner', 'password': 'a-long-private-password', 'token': token})
    response = client.post('/api/login', json={'username': 'owner', 'password': 'a-long-private-password'})
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def test_api_version_is_public_and_exposes_only_three_fields(client):
    browser, app, settings = client
    response = browser.get('/api/version')
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'version', 'commit', 'commit_count'}
    assert VERSION.match(body['version']) and isinstance(body['commit_count'], int)
    assert browser.get('/api/auth-status').json()['version'] == f"{body['version']} ({body['commit']})"
    assert browser.get('/api/self-update').status_code == 401
    assert browser.get('/api/machines/versions').status_code == 401
    sign_in(browser, settings)
    status = browser.get('/api/self-update').json()
    assert status['label'] == f"{body['version']} ({body['commit']})" and status['available'] is True


class Peer(BaseHTTPRequestHandler):
    answer = None

    def do_GET(self):
        body = json.dumps(self.server.answer).encode()
        self.send_response(200 if self.path == '/api/version' else 404)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def peers():
    servers = []

    def start(answer):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
        server.answer = answer
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f'http://127.0.0.1:{server.server_port}'
    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_machines_list_labels_latest_behind_offline_with_fake_servers(client, peers):
    browser, app, settings = client
    sign_in(browser, settings)
    mine = app.state.core.pipeline.self_update.version
    latest = peers({'version': mine['version'], 'commit': mine['commit'], 'commit_count': mine['commit_count']})
    behind = peers({'version': '2026.01.01.1', 'commit': 'abcdef1', 'commit_count': mine['commit_count'] - 3})
    closed = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
    offline = f'http://127.0.0.1:{closed.server_port}'
    closed.server_close()
    garbage = peers(['not', 'a', 'version'])
    saved = browser.put('/api/machines', json={'machines': [
        {'name': 'Same', 'url': latest}, {'name': 'Old', 'url': behind}, {'name': 'Gone', 'url': offline},
        {'name': 'Odd', 'url': garbage}]}).json()['machines']
    started = time.monotonic()
    result = browser.get('/api/machines/versions').json()
    assert time.monotonic() - started < 10
    by_id = {m['id']: m for m in result['machines']}
    labels = [by_id[m['id']] for m in saved]
    assert labels[0]['state'] == 'latest' and labels[0]['version'] == mine['version']
    assert labels[1]['state'] == 'behind' and labels[1]['behind'] == 3 and labels[1]['version'] == '2026.01.01.1'
    assert labels[2] == {'id': saved[2]['id'], 'state': 'offline'}
    assert labels[3]['state'] == 'offline'
    assert compare({'commit_count': 5}, {'version': 'v', 'commit': 'c', 'commit_count': 9})['state'] == 'latest'


def test_menu_and_settings_markup_are_wired():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / 'ai_manager/static'
    js, app_js, html = (static / 'version.js').read_text(), (static / 'app.js').read_text(), (static / 'index.html').read_text()
    assert 'renderVersionHead(content)' in app_js and 'machineVersion(m)' in app_js
    assert "'behind by '" in js and "'offline'" in js and "'latest'" in js and '/api/machines/versions' in js
    assert 'id="version-panel"' in html and 'Auto-update from GitHub' in html and '/static/version.js' in html
    assert ':not(#version-panel)' in (static / 'style.css').read_text()


@pytest.mark.parametrize('kwargs,state', [
    ({}, 'update'),
    ({'busy': True}, 'busy'),
    ({'dirty': True}, 'skipped'),
    ({'remote': 'aaa'}, 'latest'),
    ({'fast_forward': False}, 'skipped'),
    ({'branch': 'feature'}, 'skipped'),
    ({'enabled': False}, 'off'),
])
def test_auto_update_decision(kwargs, state):
    args = {'enabled': True, 'branch': 'main', 'dirty': False, 'local': 'aaa', 'remote': 'bbb', 'fast_forward': True,
            'busy': False, **kwargs}
    assert decide(**args)[0] == state


def test_main_defaults_off_and_other_machines_on(updater, monkeypatch):
    up, _, checkout = updater
    assert up.enabled() is True and up.status()['main'] is False
    monkeypatch.setenv(self_update.ROLE_ENV, 'main')
    assert up.enabled() is False and up.status()['main'] is True
    monkeypatch.setenv(self_update.ROLE_ENV, 'vm')
    assert up.enabled() is True
    monkeypatch.delenv(self_update.ROLE_ENV)
    up.registry.add(checkout)  # this machine merges and deploys Bot Boss itself: Main
    assert up.enabled() is False
    assert up.set_enabled(True)['enabled'] is True  # an explicit owner choice wins
    restricted = SelfUpdate(up.db, up.registry, up.pipeline, restricted=True, root=checkout)
    assert restricted.enabled() is False and restricted.status()['available'] is False
    with pytest.raises(ValueError):
        restricted.set_enabled(True)


def test_check_pulls_fast_forward_and_queues_the_deploy_when_idle(updater):
    up, upstream, checkout = updater
    before = git(checkout, 'rev-parse', 'HEAD')
    assert up.check()['state'] == 'latest'
    commit(upstream, 'new.txt')
    git(upstream, 'push', '-q', 'origin', 'main')
    status = up.check()
    assert status['state'] == 'updated' and status['updated_at'] and status['checked_at']
    assert git(checkout, 'rev-parse', 'HEAD') == git(upstream, 'rev-parse', 'HEAD') != before
    request = read(up.pipeline.root / 'request.json')
    assert request['source'] == 'self-update' and request['project_id'] is None and request['setup'] is None
    assert request['repo'] == str(checkout.resolve())


def test_check_skips_local_changes_busy_work_and_when_off(updater):
    up, upstream, checkout = updater
    commit(upstream, 'new.txt')
    git(upstream, 'push', '-q', 'origin', 'main')
    before = git(checkout, 'rev-parse', 'HEAD')
    (checkout / 'a.txt').write_text('local edit')
    status = up.check()
    assert status['state'] == 'skipped' and 'local changes' in status['reason']
    assert git(checkout, 'rev-parse', 'HEAD') == before and (checkout / 'a.txt').read_text() == 'local edit'
    git(checkout, 'checkout', '-q', '--', 'a.txt')
    assert up.busy() is False
    up.busy = lambda: True
    assert up.check()['state'] == 'busy' and git(checkout, 'rev-parse', 'HEAD') == before
    assert not (up.pipeline.root / 'request.json').exists()
    up.set_enabled(False)
    assert up.check()['state'] == 'off'
    del up.busy
    assert up.check(manual=True)['state'] == 'updated'  # Update now works while auto is off


def test_check_without_installed_deploy_changes_nothing(updater):
    up, upstream, checkout = updater
    (up.pipeline.root / 'installed.json').unlink()
    commit(upstream, 'new.txt')
    git(upstream, 'push', '-q', 'origin', 'main')
    before = git(checkout, 'rev-parse', 'HEAD')
    assert up.check()['state'] == 'skipped' and git(checkout, 'rev-parse', 'HEAD') == before

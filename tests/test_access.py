"""Main's access switch for another instance: Disable makes every request answer only "Error 69" and signs everyone
out, running chat replies stop, Enable restores the same login at once; Main ignores the flag."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from test_instance_models import pair, registry  # noqa: F401
from test_model_settings import CatalogServer

from ai_manager import access
from ai_manager.config import Settings
from ai_manager.web import create_app


def wait(check, seconds=15):  # generous under the parallel full suite; returns as soon as it holds
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    raise AssertionError('condition not met in time')


def blocked(response):
    return response.status_code == 503 and response.text == 'Error 69'


def login_owner(client, settings):
    """sign_in with a known password, so the same login can be repeated after Enable."""
    password = secrets.token_urlsafe(16)
    token = (settings.data_dir / 'setup-token').read_text()
    assert client.post('/api/setup', json={'username': 'owner', 'password': password, 'token': token}).status_code == 200
    response = client.post('/api/login', json={'username': 'owner', 'password': password})
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    return password


@pytest.fixture
def instance(tmp_path):
    """Main signed in, a restricted instance whose owner password is known."""
    project = tmp_path / 'projects' / 'docs'
    project.mkdir(parents=True)
    main = Settings(data_dir=tmp_path / 'main', project_roots=(tmp_path / 'main-projects',), secure_cookie=False)
    user = Settings(data_dir=tmp_path / 'user', project_roots=(project,), project_path=project,
                    restricted=True, secure_cookie=False)
    main_app = create_app(main, CatalogServer(main), CatalogServer)
    user_app = create_app(user, CatalogServer(user), CatalogServer)
    registry(main, [{'name': 'Writer', 'data': str(user.data_dir), 'url': 'http://127.0.0.1:1'}])
    with TestClient(main_app, base_url='http://localhost') as owner, \
            TestClient(user_app, base_url='http://localhost') as client:
        login_owner(owner, main)
        password = login_owner(client, user)
        yield owner, client, main_app, user_app, password


@pytest.mark.smoke
def test_disable_blocks_everything_with_error_69_and_enable_restores_login(instance):
    owner, client, main_app, user_app, password = instance
    shown = owner.get('/api/instance-harness?name=Writer').json()
    assert shown['access'] == {'disabled': False}
    assert client.get('/api/auth-status').json()['authenticated']
    owner_hash = user_app.state.db.one('SELECT password_hash FROM owner')['password_hash']

    saved = owner.put('/api/instance-access?name=Writer', json={'disabled': True}).json()
    assert saved['access'] == {'disabled': True} and saved['name'] == 'Writer'
    db = user_app.state.db
    assert db.setting(access.KEY) is True
    assert db.one('SELECT count(*) AS n FROM sessions')['n'] == 0  # everyone signed out at once
    assert owner.get('/api/instance-harness?name=Writer').json()['access'] == {'disabled': True}

    # Within the cache window (~2 s) every request, signed in or not, gets only Error 69.
    wait(lambda: blocked(client.get('/api/auth-status')))
    anonymous = TestClient(user_app, base_url='http://localhost')
    for response in (client.get('/'), client.get('/static/app.js'), client.get('/api/portfolio'),
                     client.post('/api/chat', json={'content': 'hi'}), anonymous.get('/'),
                     anonymous.get('/manifest.webmanifest'), anonymous.get('/api/auth-status'),
                     anonymous.post('/api/login', json={'username': 'owner', 'password': password})):
        assert blocked(response), response.text
        assert response.headers['content-type'].startswith('text/plain')
    assert db.one('SELECT count(*) AS n FROM sessions')['n'] == 0  # the blocked login created no session
    assert db.one('SELECT password_hash FROM owner')['password_hash'] == owner_hash  # the password never changes

    # Main ignores the flag, even its own copy of it.
    main_app.state.db.set_setting(access.KEY, True)
    assert owner.get('/api/auth-status').json()['authenticated']
    assert owner.get('/').status_code == 200

    saved = owner.put('/api/instance-access?name=Writer', json={'disabled': False}).json()
    assert saved['access'] == {'disabled': False} and db.setting(access.KEY) is None
    wait(lambda: client.get('/api/auth-status').status_code == 200)
    status = client.get('/api/auth-status').json()
    assert not status['authenticated']  # the old session stays signed out
    assert client.get('/').status_code == 200
    response = client.post('/api/login', json={'username': 'owner', 'password': password})
    assert response.status_code == 200  # the same password works again at once
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    assert client.get('/api/auth-status').json()['authenticated']


@pytest.mark.smoke
def test_access_switch_is_main_only_validated_and_reports_unreachable(pair, tmp_path):
    owner, client, app, user_app, main, user, entry = pair
    assert client.put('/api/instance-access?name=Writer', json={'disabled': True}).status_code == 403
    assert TestClient(app, base_url='http://localhost').put(
        '/api/instance-access?name=Writer', json={'disabled': True}).status_code in {401, 403}
    for body in ({}, {'disabled': 'yes'}, {'disabled': True, 'other': 1}):
        assert owner.put('/api/instance-access?name=Writer', json=body).status_code == 409
    assert owner.put('/api/instance-access?name=Main', json={'disabled': True}).status_code == 409  # Main never
    assert user_app.state.db.setting(access.KEY) is None
    registry(main, [entry, {**entry, 'name': 'Offline', 'data': str(tmp_path / 'missing')}])
    for response in (owner.get('/api/instance-harness?name=Offline'),
                     owner.put('/api/instance-access?name=Offline', json={'disabled': True})):
        assert response.status_code == 409 and 'unreachable' in response.json()['detail']
    assert not (tmp_path / 'missing').exists()


def test_gate_caches_for_at_most_its_ttl_and_keeps_last_value_on_errors(tmp_path):
    from ai_manager.db import Database
    db = Database(tmp_path / 'manager.sqlite3')
    now = [0.0]
    gate = access.Gate(db, ttl=2, clock=lambda: now[0])
    assert gate.disabled() is False
    access.write(db, True)
    now[0] = 1.9
    assert gate.disabled() is False  # cached
    now[0] = 2.0
    assert gate.disabled() is True
    db.path.rename(tmp_path / 'gone')  # unreadable: keep the last known value
    db.path.parent.joinpath('manager.sqlite3').mkdir()
    now[0] = 5
    assert gate.disabled() is True


class HangingServer(CatalogServer):
    """A manager turn (on its own thread) that runs until it is interrupted; other turns (titles) complete."""
    interrupted = []

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        return 'hang-thread' if dynamic_tools else await super().thread(cwd, instructions, thread_id, model)

    async def request(self, method, params, timeout=30):
        if method == 'turn/start' and params['threadId'] == 'hang-thread':
            return {'turn': {'id': 'turn-hang'}}
        if method == 'turn/interrupt':
            self.interrupted.append(params['turnId'])
            queue = self.queues[params['threadId']]
            queue.put_nowait({'method': 'turn/completed', 'params': {
                'threadId': params['threadId'], 'turn': {'id': params['turnId'], 'status': 'interrupted'}}})
            return {}
        return await super().request(method, params, timeout)


def test_disable_stops_running_chat_replies_and_keeps_the_chat(tmp_path):
    project = tmp_path / 'docs'
    project.mkdir()
    user = Settings(data_dir=tmp_path / 'user', project_roots=(project,), project_path=project,
                    restricted=True, secure_cookie=False)
    app = create_app(user, HangingServer(user), CatalogServer)
    HangingServer.interrupted.clear()
    with TestClient(app, base_url='http://localhost') as client:
        login_owner(client, user)
        receipt = client.post('/api/chat', json={'content': 'Write a long story'}).json()
        db = app.state.db
        wait(lambda: (db.one('SELECT turn_id FROM executions WHERE id=?', (receipt['execution_id'],)) or {}).get('turn_id'))
        access.write(db, True)  # what Main writes
        execution = wait(lambda: (row := db.one('SELECT * FROM executions WHERE id=?', (receipt['execution_id'],)))
                         ['finished_at'] and row)
        assert HangingServer.interrupted == ['turn-hang'] and execution['status'] != 'completed'
        assert db.one("SELECT count(*) AS n FROM messages WHERE role='user' AND content='Write a long story'")['n'] == 1
        access.write(db, False)
        wait(lambda: client.get('/api/auth-status').status_code == 200)

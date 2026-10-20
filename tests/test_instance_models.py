"""The main owner edits the record the separate instance actually dispatches."""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from test_model_settings import MODELS, SOL, CatalogServer
from test_restricted import sign_in

from ai_manager.config import Settings
from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.instance_models import ExistingDatabase, listing
from ai_manager.model_settings import assignment_route, current, delegation, initial_pool, manager_route
from ai_manager.providers import ProviderServer
from ai_manager.web import create_app


def registry(settings, entries):
    path = settings.data_dir / 'deploy' / 'installed.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'data': str(settings.data_dir), 'instances': entries}))


@pytest.fixture
def pair(tmp_path):
    project = tmp_path / 'projects' / 'docs'
    project.mkdir(parents=True)
    main = Settings(data_dir=tmp_path / 'main', project_roots=(tmp_path / 'main-projects',), secure_cookie=False)
    user = Settings(data_dir=tmp_path / 'user', project_roots=(project,), project_path=project,
                    restricted=True, secure_cookie=False)
    main_app = create_app(main, CatalogServer(main), CatalogServer)
    user_app = create_app(user, CatalogServer(user), CatalogServer)
    entry = {'name': 'Writer', 'data': str(user.data_dir), 'service': 'writer.service', 'url': 'http://127.0.0.1:1'}
    registry(main, [entry])
    with TestClient(main_app, base_url='http://localhost') as owner, TestClient(user_app, base_url='http://localhost') as client:
        sign_in(owner, main)
        sign_in(client, user)
        yield owner, client, main_app, user_app, main, user, entry


@pytest.mark.smoke
def test_listing_defaults_hidden_restricted_and_authentication(pair, tmp_path):
    owner, client, app, user_app, main, user, entry = pair
    untouched = current(user_app.state.db)
    registry(main, [entry, {**entry, 'name': 'Unavailable', 'data': str(tmp_path / 'missing')}])
    panel = owner.get('/api/model-settings').json()
    assert [u['name'] for u in panel['users']] == ['Writer', 'Unavailable']
    assert panel['users'][0]['settings']['manager'] == {'model': SOL, 'effort': 'low'}
    assert panel['users'][0]['providers'] == panel['providers']
    assert 'cannot access' in panel['users'][1]['error']
    assert not (tmp_path / 'missing').exists()  # never create an uninitialized instance
    assert user_app.state.db.setting('model_choice') is None
    assert current(user_app.state.db) == untouched  # listing never adopts defaults or legacy records
    registry(user, [entry])  # even a copied registry must not expose Users on a restricted instance
    restricted_panel = client.get('/api/model-settings').json()
    assert 'users' not in restricted_panel and 'users_error' not in restricted_panel
    for method in ('get', 'put'):
        args = {'json': {}} if method == 'put' else {}
        response = getattr(client, method)('/api/instance-model-settings?name=Writer', **args)
        assert response.status_code == 403 and response.json()['detail'] == 'Not available on a restricted instance'
    anonymous = TestClient(app, base_url='http://localhost')
    assert anonymous.get('/api/instance-model-settings?name=Writer').status_code == 401
    assert anonymous.put('/api/instance-model-settings?name=Writer', json={}).status_code == 401
    settings = panel['users'][0]['settings']
    assert owner.put('/api/instance-model-settings?name=Writer', json=settings,
                     headers={'X-CSRF-Token': 'bad'}).status_code == 403


@pytest.mark.smoke
def test_saved_pairs_are_used_by_next_live_chat_and_worker_and_survive_restart(pair):
    owner, client, app, user_app, main, user, _ = pair
    before = current(app.state.db)
    shown = owner.get('/api/model-settings').json()['users'][0]
    choice = {**shown['settings'], 'manager': {'model': MODELS['Luna'], 'effort': 'high'},
              'worker': {'model': SOL, 'effort': 'high'}}
    response = owner.put('/api/instance-model-settings?name=Writer', json=choice)
    assert response.status_code == 200
    confirmed = response.json()['settings']
    assert current(app.state.db) == before
    assert user_app.state.db.setting('model_choice') == confirmed
    assert owner.get('/api/model-settings').json()['users'][0]['settings'] == confirmed
    assert client.get('/api/model-settings').json()['settings'] == confirmed
    assert owner.put('/api/instance-model-settings?name=Writer', json=choice).status_code == 409
    invalid = {**confirmed, 'worker': {'model': SOL, 'effort': 'unknown'}}
    assert owner.put('/api/instance-model-settings?name=Writer', json=invalid).status_code == 409
    assert user_app.state.db.setting('model_choice') == confirmed
    # Read through an independently opened database, as after a restart.
    reopened = Database(user.db_path)
    assert current(reopened, user_app.state.chat.server) == confirmed
    assert manager_route(reopened) == confirmed['manager']
    assert delegation(reopened, user_app.state.chat.server) == confirmed['worker']
    project = user_app.state.registry.list()[0]['id']
    CatalogServer.turns.clear()
    receipt = client.post('/api/chat', json={'content': 'Hello from the owner-selected manager'}).json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        execution = reopened.one('SELECT * FROM executions WHERE id=?', (receipt['execution_id'],))
        if execution['finished_at']:
            break
        time.sleep(0.01)
    assert execution['status'] == 'completed'
    assert (execution['model'], execution['effort']) == (MODELS['Luna'], 'high')
    assert any(t['model'] == MODELS['Luna'] and t['effort'] == 'high' for t in CatalogServer.turns)
    params = {'tool': 'manager_action', 'callId': 'dispatch', 'manager_execution_id': 'manager-test',
              'conversation_id': user_app.state.chat.conversation(project)['id'],
              'arguments': {'action': 'delegate', 'objective': 'Use the owner-selected worker'}}
    async def dispatch():
        task = await user_app.state.scheduler.manager_action(project, params)
        claimed = Tasks(reopened).claim()
        await user_app.state.core._run(claimed)
        return task, claimed

    task, claimed = client.portal.call(dispatch)
    assert task['worker_route'] == confirmed['worker']
    assert assignment_route(reopened, user_app.state.chat.server, task) == confirmed['worker']
    execution = reopened.one('SELECT * FROM executions WHERE id=?', (claimed['lease_owner'],))
    assert (execution['model'], execution['effort']) == (SOL, 'high')
    assert any(t['model'] == SOL and t['effort'] == 'high' for t in CatalogServer.turns)
    keys = reopened.all("SELECT key FROM settings WHERE key LIKE 'model%'")
    assert keys == [{'key': 'model_choice'}]  # no shadow source


def test_permission_and_lock_errors_are_visible_and_do_not_change_the_record(pair):
    owner, _, _, user_app, _, user, _ = pair
    shown = owner.get('/api/model-settings').json()['users'][0]['settings']
    requested = {**shown, 'worker': {'model': MODELS['Astra'], 'effort': 'high'}}
    before = current(user_app.state.db)
    user.db_path.chmod(0o400)
    try:
        result = owner.put('/api/instance-model-settings?name=Writer', json=requested)
        assert result.status_code == 409
        assert 'read/write access' in result.json()['detail']
    finally:
        user.db_path.chmod(0o600)
        for path in user.data_dir.glob('manager.sqlite3-*'):
            path.chmod(0o600)
    assert current(user_app.state.db) == before
    # An inaccessible directory must leave the instance name in the list with an actionable error.
    user.data_dir.chmod(0)
    try:
        row = owner.get('/api/model-settings').json()['users'][0]
        assert row['name'] == 'Writer' and 'permissions' in row['error'] and 'settings' not in row
    finally:
        user.data_dir.chmod(0o700)
    with sqlite3.connect(user.db_path) as locked:
        locked.execute('BEGIN IMMEDIATE')
        result = owner.put('/api/instance-model-settings?name=Writer', json=requested)
        assert result.status_code == 409 and 'locked' in result.json()['detail']
    assert current(user_app.state.db) == before


def test_missing_bad_registry_and_unknown_instance_fail_clearly(pair, tmp_path):
    owner, _, _, _, main, _, entry = pair
    assert owner.get('/api/instance-model-settings?name=unknown').status_code == 409
    choice = owner.get('/api/model-settings').json()['users'][0]['settings']
    registry(main, [{**entry, 'data': str(tmp_path / 'unreachable')}])
    result = owner.put('/api/instance-model-settings?name=Writer', json=choice)
    assert result.status_code == 409 and 'cannot access' in result.json()['detail']
    path = main.data_dir / 'deploy' / 'installed.json'
    path.write_text('{broken')
    panel = owner.get('/api/model-settings').json()
    assert 'invalid instance registry' in panel['users_error'] and 'settings' in panel
    assert not (tmp_path / 'unreachable').exists()


def test_stopped_instance_migrates_legacy_automatic_and_dispatches_the_same_pool(tmp_path):
    main = tmp_path / 'main'
    main.mkdir()
    user = tmp_path / 'stopped'
    db = Database(user / 'manager.sqlite3')
    server = CatalogServer(Settings(data_dir=main))
    db.set_setting('model_routes', {'interactive': {'model': MODELS['Astra'], 'effort': 'high'}, 'execution': None})
    path = main / 'deploy'
    path.mkdir()
    (path / 'installed.json').write_text(json.dumps({'instances': [{'name': 'Offline', 'data': str(user)}]}))
    shown = listing(main, server)[0]['settings']
    assert shown['manager'] == {'model': MODELS['Astra'], 'effort': 'high'} and shown['worker'] == initial_pool(server, 'codex')
    assert db.setting('model_choice')['worker'] == shown['worker']
    from ai_manager.instance_models import save
    confirmed = save(main, server, 'Offline', {**shown, 'manager': {'model': SOL, 'effort': 'low'}})['settings']
    assert confirmed['worker'] == shown['worker']
    assert db.setting('model_choice') == confirmed
    assert delegation(ExistingDatabase(user), server, MODELS['Luna'], 'high') == {'model': MODELS['Luna'], 'effort': 'high'}
    restarted = Settings(data_dir=user, project_roots=(tmp_path / 'empty',), secure_cookie=False)
    app = create_app(restarted, CatalogServer(restarted), CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        sign_in(client, restarted)
        panel = client.get('/api/model-settings').json()
        assert panel['settings'] == confirmed and 'users' not in panel


def test_implicit_defaults_follow_the_user_provider_even_when_main_uses_claude(tmp_path):
    data = tmp_path / 'user'
    db = Database(data / 'manager.sqlite3')
    settings = Settings(data_dir=tmp_path / 'main')
    codex, claude = CatalogServer(settings), CatalogServer(settings)
    claude.catalog = [m for m in claude.catalog if m['model'].startswith('claude-')]
    claude.catalog[0]['isDefault'] = True
    server = ProviderServer(settings, active=lambda: 'claude', backends={'codex': codex, 'claude': claude})
    from ai_manager.instance_models import panel
    shown = panel({'name': 'Default', 'data': str(data)}, server)['settings']
    assert shown['manager'] == {'model': SOL, 'effort': 'low'}
    assert shown['worker'] == {'model': SOL, 'effort': 'medium'}
    assert db.setting('model_choice') is None and server.current is None

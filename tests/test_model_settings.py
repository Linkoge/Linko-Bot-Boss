import asyncio
import copy
import json

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_web import sign_in

from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.model_settings import MODELS, assignment_route, current, delegation, permitted, save
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry
from ai_manager.web import create_app


class CatalogServer(FakeServer):
    turns = []
    threads = []

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.catalog = [{'model': model, 'displayName': name, 'isDefault': name == 'Sol',
                         'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('low', 'medium', 'high')],
                         'defaultReasoningEffort': 'low'} for name, model in MODELS.items()]

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.threads.append({'model': model, 'prior': thread_id})
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)

    async def request(self, method, params, timeout=30):
        if method == 'turn/start':
            self.turns.append(params)
        return await super().request(method, params, timeout)


@pytest.fixture
def system(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    (root / 'one').mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,), secure_cookie=False)
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    registry.add(root / 'one')
    server = CatalogServer(settings)
    chat = Chat(db, registry, server)
    workers = Core(db, registry, settings, Pipeline(db, registry), CatalogServer)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    return db, registry, settings, server, chat, workers, scheduler


def test_defaults_validation_and_atomic_persistence(system):
    db, _, _, server, *_ = system
    db.set_setting('model_routes', {'interactive': {'model': MODELS['Astra'], 'effort': 'high'},
                                   'execution': {'model': MODELS['Sol'], 'effort': 'medium'}})
    config = current(db, server)
    assert config['manager'] == {'model': MODELS['Astra'], 'effort': 'high'}
    assert config['bot']['mode'] == 'manual'
    assert all(p['model'] != MODELS['Astra'] for p in permitted(config, server))
    saved = save(db, server, config)
    assert Database(db.path).setting('model_settings') == saved
    with pytest.raises(ValueError, match='Settings changed'):
        save(db, server, config)
    invalid = copy.deepcopy(saved)
    for item in invalid['bot']['policy'].values():
        item['allowed'] = False
    with pytest.raises(ValueError, match='at least one'):
        save(db, server, invalid)
    invalid['manager']['effort'] = 'ultra'
    with pytest.raises(ValueError):
        save(db, server, invalid)
    assert current(db, server) == saved


def restricted(db, server):
    config = current(db, server)
    config['bot']['mode'] = 'automatic'
    for model, item in config['bot']['policy'].items():
        item['allowed'] = model != MODELS['Sol']
        item['levels'] = {e: (e == ('high' if model == MODELS['Luna'] else 'low')) for e in ('low', 'medium', 'high')}
    return save(db, server, config)


@pytest.mark.smoke
def test_every_pair_and_catalog_support_are_enforced(system):
    db, _, _, server, *_ = system
    config = restricted(db, server)
    assert permitted(config, server) == [{'model': MODELS['Luna'], 'effort': 'high'}, {'model': MODELS['Astra'], 'effort': 'low'}]
    for model in MODELS.values():
        for effort in ('low', 'medium', 'high'):
            pair = {'model': model, 'effort': effort}
            if pair in permitted(config, server):
                assert delegation(db, server, pair)['effort'] == effort
            else:
                with pytest.raises(ValueError, match='denied'):
                    delegation(db, server, pair)
    with pytest.raises(ValueError):
        delegation(db, server)
    server.catalog = [m for m in server.catalog if m['model'] != MODELS['Luna']]
    with pytest.raises(ValueError, match='catalog'):
        delegation(db, server, {'model': MODELS['Luna'], 'effort': 'high'})
    config['bot']['policy'][MODELS['Astra']]['allowed'] = False
    with pytest.raises(ValueError, match='at least one'):
        save(db, server, config)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_manager_delegation_pins_actual_worker_turn_and_resume(system):
    db, registry, settings, server, chat, workers, scheduler = system
    config = restricted(db, server)
    project = registry.list()[0]['id']
    pair = {'model': MODELS['Luna'], 'effort': 'high'}
    params = {'tool': 'manager_action', 'callId': 'call', 'manager_execution_id': 'manager-test',
              'conversation_id': chat.conversation(project)['id'], 'mode': 'auto',
              'arguments': {'action': 'delegate', 'objective': 'Small deterministic change', 'worker_route': pair}}
    result = await scheduler.manager_action(project, params)
    assert result['worker_route'] == {**pair, 'mode': 'automatic', 'settings_revision': config['revision']}
    task = Tasks(db).claim()
    await workers._run(task)
    execution = db.one('SELECT * FROM executions WHERE id=?', (task['lease_owner'],))
    assert execution['status'] == 'completed'
    assert {k: execution[k] for k in pair} == pair
    assert {k: CatalogServer.turns[-1][k] for k in pair} == pair
    assert CatalogServer.threads[-1]['model'] == pair['model']
    restored = Database(db.path)
    # Changing manual settings never mutates the assignment choice.
    config['bot']['mode'] = 'manual'
    config['bot']['manual'] = {'model': MODELS['Sol'], 'effort': 'low'}
    config = save(restored, server, config)
    assert assignment_route(restored, server, Tasks(restored).get(task['id'])) == pair
    # A current deny blocks continuation, keeping thread and pair intact.
    config['bot']['policy'][MODELS['Luna']]['allowed'] = False
    save(restored, server, config)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET state='queued',lease_owner=NULL WHERE id=?", (task['id'],))
    resumed = Tasks(db).claim()
    before = len(CatalogServer.turns)
    await workers._run(resumed)
    assert Tasks(db).get(task['id'])['status'] == 'failed' and 'denied' in Tasks(db).get(task['id'])['blocker']
    assert len(CatalogServer.turns) == before
    assert Tasks(db).get(task['id'])['worker_route']['model'] == pair['model']
    assert Tasks(db).get(task['id'])['native_thread'] == execution['thread_id']


@pytest.mark.asyncio
async def test_manager_execution_honors_choice_and_receives_policy(system):
    db, registry, _, server, chat, *_ = system
    config = restricted(db, server)
    config['manager'] = {'model': MODELS['Astra'], 'effort': 'high'}
    save(db, server, config)
    await chat.submit(registry.list()[0]['id'], 'Discuss only', mode='conversation')
    await asyncio.gather(*chat.jobs.values())
    execution = db.one('SELECT model,effort FROM executions WHERE task_id IS NULL')
    assert execution == config['manager']
    assert {k: server.turns[-1][k] for k in execution} == execution
    prompt = json.loads(server.prompts[-1])
    assert prompt['bot_selection']['permitted_pairs'] == permitted(current(db, server), server)


def test_api_auth_csrf_and_saved_manual_restoration(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    server = CatalogServer(settings)
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/model-settings').status_code == 401
        sign_in(client, app, settings)
        initial = client.get('/api/model-settings').json()['settings']
        initial['bot']['manual'] = {'model': MODELS['Luna'], 'effort': 'high'}
        assert client.put('/api/model-settings', json=initial, headers={'X-CSRF-Token': 'bad'}).status_code == 403
        saved = client.put('/api/model-settings', json=initial).json()
        saved['bot']['mode'] = 'automatic'
        saved = client.put('/api/model-settings', json=saved).json()
        assert client.get('/api/model-settings').json()['settings']['bot']['manual'] == initial['bot']['manual']
        saved['bot']['mode'] = 'manual'
        assert client.put('/api/model-settings', json=saved).json()['bot']['manual'] == initial['bot']['manual']


def test_manual_override_and_pre_feature_resume(system):
    db, registry, _, server, *_ = system
    config = save(db, server, current(db, server))
    explicit = config['bot']['manual']
    assert {k: delegation(db, server, {'model': MODELS['Astra'], 'effort': 'high'})[k] for k in explicit} == explicit
    task = Tasks(db).add(registry.list()[0]['id'], 'Existing task')
    claim = Tasks(db).claim()
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET model=?,effort=? WHERE id=?', (MODELS['Sol'], 'high', claim['lease_owner']))
    config['bot']['mode'] = 'automatic'
    save(db, server, config)
    assert assignment_route(db, server, task) == {'model': MODELS['Sol'], 'effort': 'high'}
    assert Tasks(db).get(task['id'])['worker_route']['mode'] == 'manual'


@pytest.mark.asyncio
async def test_policy_edit_during_thread_start_blocks_dispatch(system):
    db, registry, settings, server, _, _, _ = system
    config = restricted(db, server)
    pair = {'model': MODELS['Luna'], 'effort': 'high'}
    task = Tasks(db).add(registry.list()[0]['id'], 'Delayed thread preparation', model_route=delegation(db, server, pair))
    claim = Tasks(db).claim()

    class DenyingServer(CatalogServer):
        async def thread(self, *args, **kwargs):
            config['bot']['policy'][MODELS['Luna']]['allowed'] = False
            save(db, server, config)
            return await super().thread(*args, **kwargs)

    before = len(CatalogServer.turns)
    await Core(db, registry, settings, Pipeline(db, registry), DenyingServer)._run(claim)
    assert len(CatalogServer.turns) == before
    assert Tasks(db).get(task['id'])['status'] == 'failed' and 'denied' in Tasks(db).get(task['id'])['blocker']
    assert db.one('SELECT launch_key FROM executions WHERE id=?', (claim['lease_owner'],))['launch_key'] == 'not-started'


def test_latest_family_numeric_order_policy_migration_and_pinned_assignment(system):
    from ai_manager.model_families import latest
    db, registry, _, server, chat, *_ = system
    config = current(db, server)
    config['bot']['mode'] = 'automatic'
    config['bot']['policy'][MODELS['Sol']]['levels']['high'] = False
    save(db, server, config)
    pair = {'model': MODELS['Sol'], 'effort': 'medium'}
    task = Tasks(db).add(registry.list()[0]['id'], 'Pinned original', chat.conversation(registry.list()[0]['id'])['id'],
                         model_route=delegation(db, server, pair))
    sol = next(m for m in server.catalog if m['model'] == MODELS['Sol'])
    server.catalog += [{**sol, 'model': model} for model in ('gpt-6.9-sol','gpt-6.10-sol','gpt-6.10-sol-preview','gpt-9-terra')]
    server.catalog.append({**sol, 'model': 'gpt-99-sol', 'hidden': True})
    assert latest(server, 'Sol') == 'gpt-6.10-sol'
    migrated = current(db, server)
    assert migrated['manager'] == {'model':'gpt-6.10-sol','effort':'low'}
    assert not migrated['bot']['policy']['gpt-6.10-sol']['levels']['high']
    assert not migrated['bot']['policy'][MODELS['Astra']]['allowed']
    assert delegation(db, server, pair)['model'] == 'gpt-6.10-sol'
    with pytest.raises(ValueError, match='denied'):
        delegation(db, server, {'model':'gpt-6.10-sol','effort':'high'})
    assert assignment_route(db, server, task) == pair
    server.catalog = [m for m in server.catalog if m['model'] != pair['model']]
    from ai_manager.model_settings import RouteBlocked
    with pytest.raises(RouteBlocked, match='catalog'):
        assignment_route(db, server, task)  # Never upgrades a retained assignment.


def test_latest_family_missing_reasoning_blocks_without_older_fallback(system):
    db, _, _, server, *_ = system
    saved = save(db, server, current(db, server))
    sol = next(m for m in server.catalog if m['model'] == MODELS['Sol'])
    server.catalog.append({**sol,'model':'gpt-7-sol','supportedReasoningEfforts':[{'reasoningEffort':'low'}]})
    assert current(db, server)['bot']['manual']['model'] == 'gpt-7-sol'
    with pytest.raises(ValueError, match='reasoning'):
        delegation(db, server)
    server.catalog = [m for m in server.catalog if not m['model'].endswith('-sol')]
    with pytest.raises(ValueError, match='catalog'):
        delegation(db, server)
    assert db.setting('model_settings') == saved


@pytest.mark.asyncio
async def test_manager_new_execution_resolves_family_but_recovery_keeps_exact_id(system):
    db, _, _, server, chat, *_ = system
    save(db, server, current(db, server))
    sol = next(m for m in server.catalog if m['model'] == MODELS['Sol'])
    server.catalog.append({**sol,'model':'gpt-7.2-sol'})
    result = await chat.submit(None, 'Use selected Sol family')
    await asyncio.gather(*list(chat.jobs.values()))
    assert db.one('SELECT model FROM executions WHERE id=?', (result['execution_id'],))['model'] == 'gpt-7.2-sol'
    assert CatalogServer.turns[-1]['model'] == 'gpt-7.2-sol'
    # A retained execution already has a native thread; its route cannot drift.
    result = await chat.submit(None, 'Retained route')
    await asyncio.gather(*list(chat.jobs.values()))
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET model=?,finished_at=NULL,status='queued',retry_at=0,launch_key='not-started',turn_id=NULL WHERE id=?",
                     (MODELS['Sol'],result['execution_id']))
    await chat.recover()
    await asyncio.gather(*list(chat.jobs.values()))
    assert CatalogServer.turns[-1]['model'] == MODELS['Sol']


@pytest.mark.asyncio
async def test_catalog_refresh_ttl_atomic_pages_bounded_failure_and_empty_cache(system, monkeypatch):
    from ai_manager.codex import AppServer
    from ai_manager.config import Settings
    server = AppServer(Settings())
    calls = []
    now = [100000.0]
    monkeypatch.setattr('ai_manager.codex.time.time', lambda: now[0])
    pages = [{'data':[{'model':'gpt-6-sol'}],'nextCursor':'next'},
             {'data':[{'model':'gpt-6.1-sol'}, {'model':'gpt-99-sol','hidden':True}], 'nextCursor':None}]
    async def request(method, params):
        calls.append((method,params))
        page = pages.pop(0)
        if isinstance(page,Exception):
            raise page
        return page
    server.request = request
    await server.refresh_catalog()
    assert [m['model'] for m in server.catalog] == ['gpt-6-sol','gpt-6.1-sol']
    await server.refresh_catalog()
    assert len(calls) == 2 and all(m == 'model/list' for m,_ in calls)
    now[0] += 3601
    pages.extend([{'data':[{'model':'gpt-7-sol'}],'nextCursor':'next'}, RuntimeError('metadata outage')])
    await server.refresh_catalog()
    assert [m['model'] for m in server.catalog] == ['gpt-6-sol','gpt-6.1-sol']
    assert server.catalog_error
    await server.refresh_catalog()
    assert len(calls) == 4  # Five-minute retry delay, no repeated metadata calls.
    now[0] += 86400
    pages.append(RuntimeError('still unavailable'))
    with pytest.raises(RuntimeError):
        await server.refresh_catalog()
    with pytest.raises(ValueError, match='stale'):
        await server.refresh_catalog()
    empty = AppServer(Settings())
    empty.request = request
    pages.append({'data':[]})
    with pytest.raises(ValueError, match='empty'):
        await empty.refresh_catalog()


def test_manager_favorites_persist_recall_across_providers_without_bot_changes(tmp_path):
    from test_claude_provider import BothServer

    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    server = BothServer(settings)
    app = create_app(settings, server, BothServer)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.put('/api/manager-favorites/0').status_code == 401
        assert client.post('/api/manager-favorites/0/recall').status_code == 401
        sign_in(client, app, settings)
        panel = client.get('/api/model-settings').json()
        assert panel['favorites'] == [None, None]
        bots = {p: v['settings']['bot'] for p, v in panel['providers'].items()}
        pair = {'model': MODELS['Astra'], 'effort': 'high'}
        config = panel['settings']
        config['manager'] = pair
        client.put('/api/model-settings', json=config).raise_for_status()
        assert client.put('/api/manager-favorites/0', headers={'X-CSRF-Token': 'bad'}).status_code == 403
        first = client.put('/api/manager-favorites/0').json()
        assert first[0]['pair'] == pair and first[1] is None
        claude = panel['providers']['claude']['settings']
        claude['manager'] = {'model': 'claude-haiku-5-5', 'effort': 'medium'}
        client.put('/api/model-settings', json={**claude, 'provider': 'claude', 'activate': True}).raise_for_status()
        second = client.put('/api/manager-favorites/1').json()
        assert second[0] == first[0] and second[1]['pair'] == claude['manager']
        recalled = client.post('/api/manager-favorites/0/recall').json()
        assert recalled['provider'] == 'codex' and recalled['settings']['manager'] == pair
        assert {p: v['settings']['bot'] for p, v in recalled['providers'].items()} == bots
        recalled = client.post('/api/manager-favorites/1/recall').json()
        assert recalled['provider'] == 'claude' and recalled['settings']['manager'] == claude['manager']
        assert {p: v['settings']['bot'] for p, v in recalled['providers'].items()} == bots
        # Insert replaces immediately, including a slot originally saved on another provider.
        replaced = client.put('/api/manager-favorites/0').json()
        assert replaced[0] == replaced[1]
        assert len(Database(settings.db_path).setting('manager_favorites')) == 2
        assert client.put('/api/manager-favorites/2').status_code == 409


def test_favorites_follow_family_but_never_substitute_unsupported_effort(system):
    from ai_manager.model_settings import active, favorites, insert_favorite, recall_favorite

    db, _, _, server, *_ = system
    config = current(db, server)
    config['manager'] = {'model': MODELS['Sol'], 'effort': 'high'}
    save(db, server, config)
    insert_favorite(db, server, 0)
    newer = copy.deepcopy(next(m for m in server.catalog if m['model'] == MODELS['Sol']))
    newer['model'] = 'gpt-6.2-sol'
    server.catalog.append(newer)
    assert favorites(db, server)[0]['pair'] == {'model': 'gpt-6.2-sol', 'effort': 'high'}
    recall_favorite(db, server, 0)
    assert current(db, server)['manager']['model'] == 'gpt-6.2-sol'
    newer['supportedReasoningEfforts'] = [{'reasoningEffort': 'low'}]
    before = db.setting('model_settings')
    assert favorites(db, server)[0]['available'] is False
    with pytest.raises(ValueError, match='unavailable'):
        recall_favorite(db, server, 0)
    assert db.setting('model_settings') == before and active(db) == 'codex'
    server.catalog = [m for m in server.catalog if 'sol' not in m['model']]
    assert favorites(db, server)[0]['available'] is False
    with pytest.raises(ValueError, match='unavailable'):
        recall_favorite(db, server, 1)


def test_provider_activation_rejects_stale_or_invalid_pair_atomically(system):
    from test_claude_provider import BothServer

    from ai_manager.model_settings import active

    db, _, settings, *_ = system
    server = BothServer(settings)
    config = current(db, server, 'claude')
    saved = save(db, server, {**config, 'provider': 'claude'})
    with pytest.raises(ValueError, match='Settings changed'):
        save(db, server, {**config, 'provider': 'claude'}, activate=True)
    assert active(db) == 'codex'
    saved['manager']['effort'] = 'ultra'
    with pytest.raises(ValueError):
        save(db, server, {**saved, 'provider': 'claude'}, activate=True)
    assert active(db) == 'codex'


def test_bot_mode_both_providers_atomic_and_routes_remain_independent(tmp_path):
    from test_claude_provider import BothServer

    from ai_manager.model_settings import active, set_active

    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    app = create_app(settings, BothServer(settings), BothServer)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.put('/api/bot-mode', json={}).status_code == 401
        sign_in(client, app, settings)
        panel = client.get('/api/model-settings').json()
        revisions = {p: v['settings']['revision'] for p, v in panel['providers'].items()}
        assert client.put('/api/bot-mode', json={'mode': 'automatic', 'revisions': revisions},
                          headers={'X-CSRF-Token': 'bad'}).status_code == 403
        response = client.put('/api/bot-mode', json={'mode': 'automatic', 'revisions': revisions})
        response.raise_for_status()
        panel = response.json()
        assert active(app.state.db) == 'codex'
        assert all(v['settings']['bot']['mode'] == 'automatic' for v in panel['providers'].values())
        before = {p: v['settings'] for p, v in panel['providers'].items()}
        revisions = {p: v['revision'] for p, v in before.items()}
        # A stale second provider rolls back the already-written first provider too.
        revisions['claude'] -= 1
        assert client.put('/api/bot-mode', json={'mode': 'manual', 'revisions': revisions}).status_code == 409
        after = client.get('/api/model-settings').json()
        assert {p: v['settings'] for p, v in after['providers'].items()} == before
        for provider, model in [('codex', MODELS['Luna']), ('claude', 'claude-haiku-5-5')]:
            config = after['providers'][provider]['settings']
            for name, item in config['bot']['policy'].items():
                item['allowed'] = name == model
                item['levels'] = {e: e == 'high' for e in ('low', 'medium', 'high')}
            client.put('/api/model-settings', json={**config, 'provider': provider}).raise_for_status()
            set_active(app.state.db, provider)
            assert delegation(app.state.db, app.state.chat.server, {'model': model, 'effort': 'high'})['model'] == model
            with pytest.raises(ValueError, match='denied'):
                delegation(app.state.db, app.state.chat.server, {'model': model, 'effort': 'low'})
        panel = client.get('/api/model-settings').json()
        revisions = {p: v['settings']['revision'] for p, v in panel['providers'].items()}
        panel = client.put('/api/bot-mode', json={'mode': 'manual', 'revisions': revisions}).json()
        assert all(v['settings']['bot']['mode'] == 'manual' for v in panel['providers'].values())
        for provider in ('codex', 'claude'):
            set_active(app.state.db, provider)
            pair = panel['providers'][provider]['settings']['bot']['manual']
            assert {k: delegation(app.state.db, app.state.chat.server)[k] for k in pair} == pair


def test_expected_favorite_guards_late_recall_and_slot_replacement(tmp_path):
    from test_claude_provider import BothServer

    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    app = create_app(settings, BothServer(settings), BothServer)
    with TestClient(app, base_url='http://localhost') as client:
        sign_in(client, app, settings)
        first = client.get('/api/model-settings').json()
        slot = client.put('/api/manager-favorites/0').json()[0]
        expected = {'provider': slot['provider'], 'pair': slot['pair'], 'revision': first['settings']['revision']}
        updated = first['settings']
        updated['manager'] = {'model': MODELS['Astra'], 'effort': 'high'}
        saved = client.put('/api/model-settings', json=updated).json()
        assert client.post('/api/manager-favorites/0/recall', json=expected).status_code == 409
        assert client.get('/api/model-settings').json()['settings'] == saved
        expected['revision'] = saved['revision']
        client.put('/api/manager-favorites/0').raise_for_status()
        assert client.post('/api/manager-favorites/0/recall', json=expected).status_code == 409
        expected['pair'] = saved['manager']
        recalled = client.post('/api/manager-favorites/0/recall', json=expected)
        recalled.raise_for_status()
        assert recalled.json()['settings']['manager'] == saved['manager']

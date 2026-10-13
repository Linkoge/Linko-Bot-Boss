import asyncio
import copy
import json

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_web import sign_in

from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.model_settings import MODELS, assignment_route, current, delegation, permitted, save
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.web import create_app
from ai_manager.workers import Workers


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
    workers = Workers(db, registry, settings, CatalogServer)
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
    task = Tasks(db).claim(2)
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
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?", (task['id'],))
    resumed = Tasks(db).claim(2)
    before = len(CatalogServer.turns)
    await workers._run(resumed)
    assert Tasks(db).get(task['id'])['status'] == 'waiting'
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
    claim = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET model=?,effort=? WHERE id=?', (MODELS['Sol'], 'high', claim['lease_owner']))
    config['bot']['mode'] = 'automatic'
    save(db, server, config)
    assert assignment_route(db, server, task) == {'model': MODELS['Sol'], 'effort': 'high'}
    assert Tasks(db).get(task['id'])['worker_route']['mode'] == 'manual'

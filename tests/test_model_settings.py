
import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_web import sign_in

from ai_manager.chat import Chat
from ai_manager.claude_host import CATALOG
from ai_manager.config import Settings
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.model_settings import (
    active,
    assignment_route,
    choices,
    current,
    delegation,
    favorites,
    insert_favorite,
    manager_route,
    recall_favorite,
    save,
    worker_context,
)
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry
from ai_manager.web import create_app

MODELS = {'Luna': 'gpt-6-luna', 'Sol': 'gpt-6.1-sol', 'Astra': 'gpt-6-astra'}
SOL, SONNET = MODELS['Sol'], 'claude-sonnet-5-5'


class CatalogServer(FakeServer):
    turns = []
    threads = []

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.catalog = [{'model': model, 'displayName': name, 'isDefault': name == 'Sol',
                         'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('low', 'medium', 'high')],
                         'defaultReasoningEffort': 'low'} for name, model in MODELS.items()] + [dict(m) for m in CATALOG]

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


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_sol_worker_is_what_is_saved_and_what_is_dispatched_even_with_a_claude_manager(system):
    db, registry, settings, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    save(db, server, {**current(db, server), 'manager': {'model': SONNET, 'effort': 'high'},
                              'worker': {'model': SOL, 'effort': 'medium'}})
    assert current(db, server)['worker'] == {'model': SOL, 'effort': 'medium'} and active(db) == 'claude'
    params = {'tool': 'manager_action', 'callId': 'c', 'manager_execution_id': 'manager-test', 'mode': 'auto',
              'conversation_id': chat.conversation(project)['id'],
              'arguments': {'action': 'delegate', 'objective': 'Small change'}}
    result = await scheduler.manager_action(project, params)
    assert result['worker_route'] == {'model': SOL, 'effort': 'medium'}
    task = Tasks(db).claim()
    await workers._run(task)
    execution = db.one('SELECT * FROM executions WHERE id=?', (task['lease_owner'],))
    assert (execution['model'], execution['effort']) == (SOL, 'medium')
    assert (CatalogServer.turns[-1]['model'], CatalogServer.turns[-1]['effort']) == (SOL, 'medium')  # actual turn/start arguments
    assert CatalogServer.threads[-1]['model'] == SOL
    # Changing the saved worker later never moves a pinned task.
    save(db, server, {**current(db, server), 'worker': {'model': SONNET, 'effort': 'low'}})
    assert assignment_route(db, server, Tasks(db).get(task['id'])) == {'model': SOL, 'effort': 'medium'}
    assert delegation(db, server)['model'] == SONNET


def test_one_global_choice_any_provider_validation_and_revisions(system):
    db, _, _, server, *_ = system
    config = current(db, server)
    assert set(config) == {'revision', 'manager', 'worker'} and config['revision'] == 0
    first = save(db, server, {**config, 'manager': {'model': 'claude-opus-5-5', 'effort': 'low'},
                              'worker': {'model': MODELS['Luna'], 'effort': 'high'}})
    assert first['revision'] == 1 and manager_route(db) == first['manager']
    with pytest.raises(ValueError, match='changed'):
        save(db, server, config)  # stale revision
    for bad in ({'model': 'nope', 'effort': 'low'}, {'model': SOL, 'effort': 'ultra'}, {'model': SOL}):
        with pytest.raises(ValueError):
            save(db, server, {**first, 'worker': bad})
    assert current(db, server)['worker'] == first['worker']  # nothing half-saved


def test_picker_shows_the_compact_family_layout_while_dispatch_accepts_any_catalog_model(system):
    db, _, _, server, *_ = system
    server.catalog.append({'model': 'custom-model-x', 'displayName': 'Custom X', 'isDefault': False,
                           'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('minimal', 'ultra')],
                           'defaultReasoningEffort': 'minimal'})
    server.catalog.append({'model': 'gpt-5-sol', 'displayName': 'Old Sol', 'isDefault': False,
                           'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('low', 'xhigh')],
                           'defaultReasoningEffort': 'low'})
    # The normal picker: three families per provider in fixed order, newest model each, Low/Medium/High only.
    codex = choices(server, 'codex')
    assert [(c['name'], c['model'], c['levels']) for c in codex] == [
        ('Luna', MODELS['Luna'], ['low', 'medium', 'high']), ('Sol', SOL, ['low', 'medium', 'high']),
        ('Astra', MODELS['Astra'], ['low', 'medium', 'high'])]
    claude = choices(server, 'claude')
    assert [c['name'] for c in claude] == ['Haiku', 'Sonnet', 'Opus'] and len(claude) == 3
    assert all(set(c['levels']) <= {'low', 'medium', 'high'} for c in claude)
    # A family missing from the catalog stays visible but disabled; no other family is substituted.
    server.catalog[:] = [m for m in server.catalog if 'astra' not in m['model']]
    assert next(c for c in choices(server, 'codex') if c['name'] == 'Astra') == {'name': 'Astra', 'model': 'Astra', 'levels': []}
    # Dispatch is unrestricted: any catalog model and effort saves and is dispatched...
    saved = save(db, server, {**current(db, server), 'manager': {'model': 'custom-model-x', 'effort': 'ultra'},
                              'worker': {'model': 'gpt-5-sol', 'effort': 'xhigh'}})
    assert saved['manager'] == {'model': 'custom-model-x', 'effort': 'ultra'}
    # ...and a saved off-family selection stays visible (and so pressed) without cluttering the normal picker.
    shown = choices(server, 'codex', {'custom-model-x', 'gpt-5-sol'})
    assert [c['name'] for c in shown] == ['Luna', 'Sol', 'Astra', 'Custom X', 'Old Sol']
    assert shown[3]['levels'] == ['minimal', 'ultra'] and shown[4]['levels'] == ['low', 'xhigh']
    with pytest.raises(ValueError, match='not supported'):
        save(db, server, {**saved, 'manager': {'model': SOL, 'effort': 'ultra'}})  # the catalog decides, nothing else


def test_a_newer_family_version_replaces_the_older_one_numerically(system):
    _, _, _, server, *_ = system
    for model in ('gpt-6.10-sol', 'gpt-6.9-sol', 'claude-sonnet-5-10'):
        server.catalog.append({'model': model, 'displayName': model, 'isDefault': False,
                               'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ('low', 'medium', 'high')],
                               'defaultReasoningEffort': 'low'})
    assert next(c for c in choices(server, 'codex') if c['name'] == 'Sol')['model'] == 'gpt-6.10-sol'
    assert next(c for c in choices(server, 'claude') if c['name'] == 'Sonnet')['model'] == 'claude-sonnet-5-10'


@pytest.mark.asyncio
async def test_automatic_worker_lets_the_manager_pick_any_model_and_the_task_records_what_ran(system):
    db, registry, _, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    save(db, server, {**current(db, server), 'worker': None})
    assert current(db, server)['worker'] is None
    context = worker_context(db, server)
    assert 'automatic' in context and set(context['available']) == {m['model'] for m in server.catalog}

    async def delegate(objective, **picks):
        params = {'tool': 'manager_action', 'callId': objective, 'manager_execution_id': 'm-' + objective,
                  'conversation_id': chat.conversation(project)['id'],
                  'arguments': {'action': 'delegate', 'objective': objective, **picks}}
        return await scheduler.manager_action(project, params)

    picked = await delegate('picked', model=MODELS['Luna'], effort='high')
    assert picked['worker_route'] == {'model': MODELS['Luna'], 'effort': 'high'}
    default = await delegate('default')
    assert default['worker_route']['model'] == SOL  # nothing asked for: the provider default
    with pytest.raises(ValueError, match='not in the installed'):
        await delegate('bad pick', model='nope')
    CatalogServer.turns.clear()
    for expected in (MODELS['Luna'], SOL):
        task = Tasks(db).claim()
        await workers._run(task)
        execution = db.one('SELECT * FROM executions WHERE id=?', (task['lease_owner'],))
        assert execution['model'] == expected and CatalogServer.turns[-1]['model'] == expected  # what actually ran
        assert Tasks(db).get(task['id'])['worker_route']['model'] == expected
    # A fixed worker setting wins: the manager's pick is ignored.
    save(db, server, {**current(db, server), 'worker': {'model': SONNET, 'effort': 'low'}})
    assert worker_context(db, server) == {'model': SONNET, 'effort': 'low'}
    fixed = await delegate('fixed', model=MODELS['Luna'], effort='high')
    assert fixed['worker_route'] == {'model': SONNET, 'effort': 'low'}


def test_old_per_provider_settings_migrate_to_the_most_recently_edited_choice(system):
    db, _, _, server, *_ = system
    policy = {}
    db.set_setting('model_settings', {'revision': 84, 'manager': {'model': SOL, 'effort': 'low'},
                                      'bot': {'mode': 'manual', 'manual': {'model': SOL, 'effort': 'medium'}, 'policy': policy}})
    db.set_setting('model_settings:claude', {'revision': 48, 'manager': {'model': SONNET, 'effort': 'low'},
                                             'bot': {'mode': 'manual', 'manual': {'model': SONNET, 'effort': 'high'}, 'policy': policy}})
    db.set_setting('model_provider', 'claude')
    config = current(db, server)
    assert config['worker'] == {'model': SOL, 'effort': 'medium'} and config['manager']['model'] == SOL
    assert delegation(db, server)['model'] == SOL  # the stale Claude store no longer wins


def test_favorites_set_only_the_manager_across_providers(system):
    db, _, _, server, *_ = system
    save(db, server, {**current(db, server), 'manager': {'model': SONNET, 'effort': 'high'}})
    insert_favorite(db, server, 0)
    save(db, server, {**current(db, server), 'manager': {'model': SOL, 'effort': 'low'}, 'worker': {'model': MODELS['Luna'], 'effort': 'low'}})
    assert favorites(db, server)[0]['pair'] == {'model': SONNET, 'effort': 'high'}
    recall_favorite(db, server, 0)
    config = current(db, server)
    assert config['manager'] == {'model': SONNET, 'effort': 'high'} and config['worker']['model'] == MODELS['Luna']
    with pytest.raises(ValueError, match='unavailable'):
        recall_favorite(db, server, 1)


def test_api_saves_exactly_what_the_panel_sent(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    server = CatalogServer(settings)
    server.catalog.append({'model': 'custom-model-x', 'displayName': 'Custom X', 'isDefault': False,
                           'supportedReasoningEfforts': [{'reasoningEffort': 'minimal'}], 'defaultReasoningEffort': 'minimal'})
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/model-settings').status_code == 401
        sign_in(client, app, settings)
        panel = client.get('/api/model-settings').json()
        assert set(panel['providers']) == {'codex', 'claude'} and set(panel['settings']) == {'revision', 'manager', 'worker'}
        assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra']
        assert [c['name'] for c in panel['providers']['claude']['choices']] == ['Haiku', 'Sonnet', 'Opus']
        choice = {**panel['settings'], 'manager': {'model': SONNET, 'effort': 'high'}, 'worker': {'model': SOL, 'effort': 'medium'}}
        assert client.put('/api/model-settings', json=choice, headers={'X-CSRF-Token': 'bad'}).status_code == 403
        assert client.put('/api/model-settings', json=choice).json()['worker'] == {'model': SOL, 'effort': 'medium'}
        panel = client.get('/api/model-settings').json()
        assert panel['provider'] == 'claude' and panel['settings']['worker'] == {'model': SOL, 'effort': 'medium'}
        assert client.put('/api/model-settings', json=choice).status_code == 409  # stale revision
        auto = {**client.get('/api/model-settings').json()['settings'], 'worker': None}
        assert client.put('/api/model-settings', json=auto).json()['worker'] is None
        assert client.get('/api/model-settings').json()['settings']['worker'] is None
        assert client.get('/api/models').status_code == 404  # the unused models endpoint is gone
        # A saved or favorited model outside the three families stays visible; nothing else is added to the picker.
        now = client.get('/api/model-settings').json()
        assert 'Custom X' not in [c['name'] for c in now['providers']['codex']['choices']]
        custom = {**now['settings'], 'manager': {'model': 'custom-model-x', 'effort': 'minimal'}}
        assert client.put('/api/model-settings', json=custom).status_code == 200
        names = [c['name'] for c in client.get('/api/model-settings').json()['providers']['codex']['choices']]
        assert names == ['Luna', 'Sol', 'Astra', 'Custom X']

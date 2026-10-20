
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
    initial_pool,
    insert_favorite,
    manager_route,
    picker_providers,
    recall_favorite,
    save,
    selection_models,
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
    assert set(config) == {'revision', 'manager', 'worker', 'manual'} and config['revision'] == 0
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
        ('Astra', MODELS['Astra'], ['low', 'medium', 'high']), ('Custom X', 'custom-model-x', ['minimal', 'ultra'])]
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


def pool(models=None, efforts=None, provider='codex'):
    return {'mode': 'automatic', 'provider': provider, 'allowed_models': [MODELS['Luna'], SOL] if models is None else models,
            'allowed_efforts': ['low', 'high'] if efforts is None else efforts}


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_automatic_pool_is_saved_shown_dispatched_and_tasks_keep_their_first_pair(system):
    db, registry, settings, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    manual = {'model': MODELS['Luna'], 'effort': 'low'}
    save(db, server, {**current(db, server), 'manager': {'model': SONNET, 'effort': 'low'}, 'worker': manual})
    auto = save(db, server, {**current(db, server), 'worker': pool()})
    assert auto['worker'] == pool() and auto['manual'] == manual
    assert current(Database(settings.db_path), server) == auto
    context = worker_context(db, server)
    assert context['provider'] == 'codex' and context['allowed_models'] == pool()['allowed_models']
    assert context['allowed_efforts'] == ['low', 'high'] and 'cheapest fit' in context['automatic']

    async def delegate(objective, **picks):
        return await scheduler.manager_action(project, {
            'tool': 'manager_action', 'callId': objective, 'manager_execution_id': 'm-' + objective,
            'conversation_id': chat.conversation(project)['id'],
            'arguments': {'action': 'delegate', 'objective': objective, **picks}})

    assert (await delegate('sol', model=SOL, effort='high'))['worker_route'] == {'model': SOL, 'effort': 'high'}
    assert (await delegate('omitted'))['worker_route'] == manual  # never the provider default outside the pool
    for picks in ({'model': MODELS['Astra']}, {'model': SONNET}, {'effort': 'medium'}):
        with pytest.raises(ValueError, match='not in the automatic worker pool:.*gpt-6-luna.*gpt-6.1-sol'):
            await delegate('rejected', **picks)
    assert len(db.all('SELECT id FROM tasks')) == 2
    for expected in ({'model': SOL, 'effort': 'high'}, manual):
        task = Tasks(db).claim()
        await workers._run(task)
        assert db.one('SELECT model,effort FROM executions WHERE id=?', (task['lease_owner'],)) == expected
        assert {k: CatalogServer.turns[-1][k] for k in ('model', 'effort')} == expected
        save(db, server, {**current(db, server), 'worker': pool([MODELS['Luna']], ['low'])})
        assert assignment_route(db, server, task) == expected
    back = save(db, server, {**current(db, server), 'worker': auto['manual']})
    assert worker_context(db, server) == back['worker'] == manual
    assert delegation(db, server, SONNET, 'high') == manual


def test_legacy_null_migrates_once_and_catalog_additions_stay_off(system):
    db, _, settings, server, *_ = system
    assert current(db, server)['worker'] == {'model': SOL, 'effort': 'medium'}
    db.set_setting('model_choice', {'revision': 3, 'manager': {'model': SOL, 'effort': 'low'}, 'worker': None})
    config = current(db, server)
    assert config['worker'] == initial_pool(server, 'codex')
    assert config['manual'] == {'model': SOL, 'effort': 'medium'}
    assert db.setting('model_choice')['worker'] == config['worker']
    server.catalog.append({**server.catalog[0], 'model': 'gpt-7-fable', 'displayName': 'Fable'})
    server.catalog[0]['supportedReasoningEfforts'].append({'reasoningEffort': 'ultra'})
    assert current(Database(settings.db_path), server)['worker'] == config['worker']
    assert 'ultra' in picker_providers(server)['codex']['efforts']
    assert 'ultra' not in config['worker']['allowed_efforts']
    assert 'gpt-7-fable' in [c['model'] for c in choices(server, 'codex')]
    with pytest.raises(ValueError, match='not in the automatic worker pool'):
        delegation(db, server, 'gpt-7-fable')
    assert save(db, server, {**config, 'manager': {'model': SONNET, 'effort': 'low'}})['worker'] == config['worker']
    assert save(db, server, {**current(db, server), 'worker': {'model': 'gpt-7-fable', 'effort': 'low'}})['worker']['model'] == 'gpt-7-fable'


@pytest.mark.parametrize('worker', [None, {**pool(), 'allowed_models': []},
    {**pool(), 'allowed_efforts': []}, {**pool(), 'provider': 'unknown'}, {**pool(), 'provider': []},
    pool([SONNET]), pool([SOL, SOL]), {**pool(), 'allowed_efforts': ['ultra']},
    {**pool(), 'allowed_models': 'gpt-6-luna'}, {**pool(), 'allowed_pairs': []}])
def test_invalid_pools_are_refused_atomically(system, worker):
    db, _, _, server, *_ = system
    before = current(db, server)
    with pytest.raises(ValueError):
        save(db, server, {**before, 'worker': worker})
    assert current(db, server) == before


def test_independent_models_and_efforts_use_catalog_support_without_pair_policy(system):
    db, _, _, server, *_ = system
    server.catalog[0]['supportedReasoningEfforts'] = [{'reasoningEffort': 'low'}]
    worker = pool([MODELS['Luna'], SOL], ['high'])
    save(db, server, {**current(db, server), 'worker': worker})
    assert delegation(db, server) == {'model': SOL, 'effort': 'high'}
    assert delegation(db, server, effort='high') == {'model': SOL, 'effort': 'high'}
    with pytest.raises(ValueError, match='No supported execution pair.*automatic worker pool'):
        delegation(db, server, model=MODELS['Luna'])
    with pytest.raises(ValueError, match='not supported'):
        delegation(db, server, MODELS['Luna'], 'high')
    # A damaged persisted pool has an explicit server-side error, never a provider fallback.
    db.set_setting('model_choice', {**current(db, server), 'worker': {**worker, 'allowed_models': []}})
    with pytest.raises(ValueError, match='pool is empty'):
        delegation(db, server)


def test_claude_legacy_pool_and_catalog_temporarily_unavailable(system):
    db, _, _, server, *_ = system
    db.set_setting('model_choice', {'revision': 2, 'manager': {'model': SONNET, 'effort': 'low'},
                                  'worker': None, 'manual': {'model': SONNET, 'effort': 'high'}})
    catalog, server.catalog = server.catalog, []
    assert current(db, server)['worker']['allowed_models'] == []
    assert db.setting('model_choice')['worker'] is None  # wait for a real catalog to freeze migration
    with pytest.raises(ValueError, match='pool is empty'):
        delegation(db, server)
    server.catalog = catalog
    migrated = current(db, server)['worker']
    assert migrated == initial_pool(server, 'claude')
    assert delegation(db, server)['model'].startswith('claude-')


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
        assert set(panel['providers']) == {'codex', 'claude'} and set(panel['settings']) == {'revision', 'manager', 'worker', 'manual'}
        assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra', 'Custom X']
        assert [c['name'] for c in panel['providers']['claude']['choices']] == ['Haiku', 'Sonnet', 'Opus']
        choice = {**panel['settings'], 'manager': {'model': SONNET, 'effort': 'high'}, 'worker': {'model': SOL, 'effort': 'medium'}}
        assert client.put('/api/model-settings', json=choice, headers={'X-CSRF-Token': 'bad'}).status_code == 403
        assert client.put('/api/model-settings', json=choice).json()['worker'] == {'model': SOL, 'effort': 'medium'}
        panel = client.get('/api/model-settings').json()
        assert panel['provider'] == 'claude' and panel['settings']['worker'] == {'model': SOL, 'effort': 'medium'}
        assert client.put('/api/model-settings', json=choice).status_code == 409  # stale revision
        auto = {**client.get('/api/model-settings').json()['settings'], 'worker': pool()}
        assert client.put('/api/model-settings', json=auto).json()['worker'] == pool()  # Automatic worker
        assert client.get('/api/model-settings').json()['settings']['worker'] == pool()
        now = client.get('/api/model-settings').json()['settings']
        assert client.put('/api/model-settings', json={**now, 'worker': {'model': SOL}}).status_code == 409  # a half pair is refused
        assert client.get('/api/model-settings').json()['settings']['worker'] == pool()
        assert client.get('/api/models').status_code == 404  # the unused models endpoint is gone
        # New families are available for exact selection and stay off in saved automatic pools.
        now = client.get('/api/model-settings').json()
        assert 'Custom X' in [c['name'] for c in now['providers']['codex']['choices']]
        custom = {**now['settings'], 'manager': {'model': 'custom-model-x', 'effort': 'minimal'}}
        assert client.put('/api/model-settings', json=custom).status_code == 200
        names = [c['name'] for c in client.get('/api/model-settings').json()['providers']['codex']['choices']]
        assert names == ['Luna', 'Sol', 'Astra', 'Custom X']


def test_legacy_provider_settings_migrate_automatic_without_reusing_the_pair_policy(system):
    db, _, _, server, *_ = system
    db.set_setting('model_settings:claude', {'revision': 12, 'manager': {'model': SONNET, 'effort': 'low'},
        'bot': {'mode': 'automatic', 'manual': {'model': SONNET, 'effort': 'high'},
                'policy': {'allowed_pairs': []}}})
    assert current(db, server)['worker'] == initial_pool(server, 'claude')
    assert db.setting('model_choice')['worker'] == initial_pool(server, 'claude')
    assert delegation(db, server)['model'].startswith('claude-')


def test_new_family_version_stays_off_and_the_old_pool_member_stays_visible(system):
    db, _, _, server, *_ = system
    before = save(db, server, {**current(db, server), 'worker': pool()})
    server.catalog.append({**server.catalog[1], 'model': 'gpt-9-sol'})
    settings = current(db, server)
    assert settings == before
    shown = picker_providers(server, selection_models(settings))['codex']['choices']
    assert {'name': 'Sol', 'model': 'gpt-9-sol', 'levels': ['low', 'medium', 'high']} in shown
    assert next(m for m in shown if m['model'] == SOL)['name'] == SOL
    assert len({m['name'] for m in shown}) == len(shown)
    with pytest.raises(ValueError, match='not in the automatic worker pool'):
        delegation(db, server, 'gpt-9-sol')

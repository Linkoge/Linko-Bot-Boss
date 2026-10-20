"""Repo settings: one complete lock or global, including migration and actual provider dispatch."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_model_settings import MODELS, SOL, SONNET, CatalogServer, pool
from test_model_settings import system as system_fixture
from test_web import sign_in

from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.model_settings import (
    assignment_route,
    current,
    delegation,
    initial_pool,
    locked,
    manager_route,
    save,
    saved,
    set_lock,
    worker_context,
)
from ai_manager.web import create_app

system = system_fixture
GLOBAL = {'model': SOL, 'effort': 'medium'}
LOCAL = {'model': MODELS['Luna'], 'effort': 'high'}
MANAGER = {'model': SONNET, 'effort': 'high'}


def two_projects(system):
    db, registry, settings, server, *_ = system
    second = settings.project_roots[0] / 'two'
    second.mkdir()
    return registry.list()[0]['id'], registry.add(second)['id']


def toggle(db, project, on):
    set_lock(db, project, {'revision': saved(db, project)['revision'], 'locked': on})


def pick(db, server, project=None, **changes):
    return save(db, server, {**current(db, server, project), **changes}, project)


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize('global_origin', [False, True])
async def test_repo_lock_reaches_manager_worker_and_task_evidence(system, global_origin):
    db, _, _, server, chat, workers, scheduler = system
    one, two = two_projects(system)
    pick(db, server, manager=GLOBAL, worker=GLOBAL)
    toggle(db, one, True)
    pick(db, server, one, manager=MANAGER, worker=LOCAL)
    for project, manager, worker in ((one, MANAGER, LOCAL), (two, GLOBAL, GLOBAL), (None, GLOBAL, GLOBAL)):
        receipt = await chat.submit(project, 'Explain the selected worker')
        await asyncio.wait_for(asyncio.gather(*chat.jobs.values()), timeout=5)
        assert db.one('SELECT model,effort FROM executions WHERE id=?', (receipt['execution_id'],)) == manager
        assert {k: CatalogServer.turns[-1][k] for k in ('model', 'effort')} == manager
        assert json.loads(server.prompts[-1])['worker'] == worker
        assert worker_context(db, server, project) == worker
    origin = None if global_origin else one
    delegated = await scheduler.manager_action(origin, {
        'tool': 'manager_action', 'callId': 'locked', 'manager_execution_id': 'locked',
        'conversation_id': chat.conversation(origin)['id'],
        'arguments': {'project_id': one, 'action': 'delegate', 'objective': 'repo task', 'model': SOL, 'effort': 'low'},
    })
    assert delegated['worker_route'] == LOCAL
    toggle(db, one, False)
    pick(db, server, worker=MANAGER)
    task = Tasks(db).claim()
    await workers._run(task)
    assert db.one('SELECT model,effort FROM executions WHERE id=?', (task['lease_owner'],)) == LOCAL
    assert {k: CatalogServer.turns[-1][k] for k in ('model', 'effort')} == LOCAL
    assert CatalogServer.threads[-1]['model'] == LOCAL['model']
    assert assignment_route(db, server, task) == LOCAL
    assert manager_route(db, one) == GLOBAL
    assert delegation(db, server, project_id=one) == MANAGER
    # Reports are new manager turns in the task's conversation and use its repo manager.
    toggle(db, one, True)
    pick(db, server, one, manager=MANAGER)
    await chat.deliver_results()
    await asyncio.wait_for(asyncio.gather(*chat.jobs.values()), timeout=5)
    delivery = db.one('SELECT delivery_execution FROM executions WHERE id=?', (task['lease_owner'],))
    assert delivery['delivery_execution']
    assert db.one('SELECT model,effort FROM executions WHERE id=?', (delivery['delivery_execution'],)) == MANAGER
    assert json.loads(server.prompts[-1])['worker'] == MANAGER


def test_lock_copy_delete_reload_and_all_picks_share_one_scope(system):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    pick(db, server, manager=GLOBAL, worker=GLOBAL)
    toggle(db, one, True)
    assert saved(db, one) == saved(db)
    pick(db, server, one, manager=MANAGER, worker=LOCAL)
    pick(db, server, one, worker=pool())
    reloaded = Database(settings.db_path)
    assert locked(reloaded, one) and not locked(reloaded, two)
    assert manager_route(reloaded, one) == MANAGER
    assert current(reloaded, server, one)['manual'] == LOCAL
    assert delegation(db, server, SOL, 'high', one) == {'model': SOL, 'effort': 'high'}
    # An unlocked repo edits global; a lock's manager, worker and remembered Manual stay isolated.
    pick(db, server, two, manager=LOCAL, worker=MANAGER)
    assert manager_route(db) == manager_route(db, two) == LOCAL
    assert current(db, server, one)['worker'] == pool()
    pick(db, server, one, worker=current(db, server, one)['manual'])
    task = Tasks(db).add(one, 'direct task')
    assert assignment_route(db, server, task) == LOCAL
    toggle(db, one, False)
    assert db.setting('model_choice:' + one) is None
    assert not locked(Database(settings.db_path), one)
    assert current(db, server, one) == current(db, server)
    assert assignment_route(db, server, task) == LOCAL
    toggle(db, one, True)
    assert saved(db, one) == saved(db)
    assert current(db, server, one)['worker'] == MANAGER  # no prior local state restored


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('legacy_global', [False, True])
def test_schema16_migrates_worker_overrides_once_into_full_locks(system, automatic, legacy_global):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    if legacy_global:
        db.set_setting('model_settings', {'revision': 9, 'manager': MANAGER, 'bot': {'manual': GLOBAL}})
    else:
        pick(db, server, manager=MANAGER, worker=GLOBAL)
    db.set_setting('project_worker:' + one, {'revision': 7, 'override': {'worker': None if automatic else LOCAL, 'manual': LOCAL}})
    db.set_setting('project_worker:' + two, {'revision': 3, 'override': None})
    db.run('DELETE FROM schema_version WHERE version=16')
    reloaded = Database(settings.db_path)
    assert locked(reloaded, one) and not locked(reloaded, two)
    assert manager_route(reloaded, one) == MANAGER
    assert current(reloaded, server, one)['worker'] == (initial_pool(server, 'codex') if automatic else LOCAL)
    assert current(reloaded, server, one)['manual'] == LOCAL
    assert reloaded.all("SELECT key FROM settings WHERE key GLOB 'project_worker:*'") == []
    toggle(reloaded, one, False)
    assert not locked(Database(settings.db_path), one)  # migration never recreates a discarded lock


def test_repo_api_revision_scope_favorites_and_removed_worker_endpoint(system):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    pick(db, server, manager=GLOBAL, worker=GLOBAL)
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        endpoint = f'/api/projects/{one}/model-lock'
        assert client.put(endpoint, json={'revision': 1, 'locked': True}).status_code == 401
        sign_in(client, app, settings)
        def panel(project=one):
            return client.get('/api/model-settings', params={'project_id': project}).json()
        original = panel()
        assert original['locked'] is False and original['repo_name'] == 'one'
        body = {'revision': original['settings']['revision'], 'locked': True}
        assert client.put(endpoint, json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
        local = client.put(endpoint, json=body).json()
        assert local['locked'] and local['settings'] == original['settings']
        choice = {**local['settings'], 'manager': MANAGER, 'worker': LOCAL}
        url = f'/api/model-settings?project_id={one}&locked=true'
        assert client.put(url, json=choice).status_code == 200
        assert client.put(url, json=choice).status_code == 409
        assert panel(two)['settings'] == original['settings']
        assert panel()['settings']['worker'] == LOCAL
        client.put(f'/api/manager-favorites/0?project_id={one}')
        new = panel()['settings']
        client.put(url, json={**new, 'manager': GLOBAL})
        recalled = client.post(f'/api/manager-favorites/0/recall?project_id={one}&locked=true').json()
        assert recalled['settings']['manager'] == MANAGER and recalled['settings']['worker'] == LOCAL
        assert panel(two)['settings']['manager'] == GLOBAL
        assert client.put(endpoint, json={'revision': recalled['settings']['revision'], 'locked': False}).json()['locked'] is False
        # A stale local editor cannot accidentally save to global after an unlock.
        assert client.put(url, json=panel()['settings']).status_code == 409
        assert client.put(f'/api/projects/{one}/worker-model', json={}).status_code == 404
        assert client.get('/api/model-settings?project_id=missing').status_code == 409


def test_repo_pair_validation_is_atomic_and_all_catalog_pairs_are_accepted(system):
    db, _, _, server, *_ = system
    one, _ = two_projects(system)
    toggle(db, one, True)
    before = saved(db, one)
    for bad in ({'model': 'missing', 'effort': 'low'}, {'model': SOL}, {'model': SOL, 'effort': 'ultra'}):
        with pytest.raises(ValueError):
            pick(db, server, one, worker=bad)
        assert saved(db, one) == before
    with pytest.raises(ValueError, match='Unknown project'):
        toggle(db, 'missing', True)
    server.catalog.append({'model': 'custom-worker', 'displayName': 'Custom worker', 'isDefault': False,
                           'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}], 'defaultReasoningEffort': 'ultra'})
    custom = {'model': 'custom-worker', 'effort': 'ultra'}
    pick(db, server, one, manager=custom, worker=custom)
    assert manager_route(db, one) == delegation(db, server, project_id=one) == custom


def test_repo_api_exposes_other_catalog_families_in_each_scope(system):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    for model in ('custom-global', 'custom-repo'):
        server.catalog.append({'model': model, 'displayName': model, 'isDefault': False,
                               'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}],
                               'defaultReasoningEffort': 'ultra'})
    pick(db, server, worker={'model': 'custom-global', 'effort': 'ultra'})
    toggle(db, one, True)
    pick(db, server, one, worker={'model': 'custom-repo', 'effort': 'ultra'})
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        sign_in(client, app, settings)
        for project, extra in ((one, 'custom-repo'), (two, 'custom-global')):
            panel = client.get('/api/model-settings', params={'project_id': project}).json()
            assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra', 'custom-global', 'custom-repo']
            assert panel['providers']['codex']['choices'][-1]['levels'] == ['ultra']
        pick(db, server, one, worker=pool())
        panel = client.get('/api/model-settings', params={'project_id': one}).json()
        assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra', 'custom-global', 'custom-repo']


def test_lock_freezes_the_current_default_pairs_before_global_provider_changes(system):
    db, _, _, server, *_ = system
    one, _ = two_projects(system)
    before = current(db, server)
    set_lock(db, one, {'revision': before['revision'], 'locked': True}, server)
    pick(db, server, manager=MANAGER, worker=LOCAL)
    assert current(db, server, one) == before
    assert saved(db, one)['manager'] == before['manager']
    assert saved(db, one)['worker'] == before['worker']


def test_lock_migrates_legacy_global_automatic_and_keeps_its_pool_after_catalog_growth(system):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    db.set_setting('model_choice', {'revision': 4, 'manager': MANAGER, 'worker': None})
    set_lock(db, one, {'revision': 4, 'locked': True}, server)
    pool = initial_pool(server, 'claude')
    assert saved(db, one)['worker'] == saved(db)['worker'] == pool
    assert delegation(db, server, project_id=one)['model'].startswith('claude-')
    pick(db, server, two, worker=GLOBAL)
    server.catalog.append({**server.catalog[-1], 'model': 'claude-fable-9', 'displayName': 'Fable'})
    reloaded = Database(settings.db_path)
    assert current(reloaded, server, one)['worker'] == pool
    assert current(reloaded, server, two)['worker'] == GLOBAL
    assert 'claude-fable-9' not in worker_context(reloaded, server, one)['allowed_models']

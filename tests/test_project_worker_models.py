"""Project worker selection: isolation, durable inheritance and the arguments that actually reach a worker."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_model_settings import MODELS, SOL, SONNET, CatalogServer
from test_model_settings import system as system_fixture
from test_web import sign_in

from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.model_settings import (
    assignment_route,
    current,
    delegation,
    manager_route,
    project_worker,
    save,
    save_project_worker,
    worker_context,
)
from ai_manager.web import create_app

DEFAULT = {'model': SOL, 'effort': 'medium'}
OVERRIDE = {'model': MODELS['Luna'], 'effort': 'high'}
system = system_fixture


def set_override(db, server, project, pair):
    return save_project_worker(db, server, project, {
        'revision': project_worker(db, project)['revision'],
        'override': None if pair is None else {'worker': pair, 'manual': pair},
    })


def two_projects(system):
    db, registry, settings, server, *_ = system
    second = settings.project_roots[0] / 'two'
    second.mkdir()
    return registry.list()[0]['id'], registry.add(second)['id']


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_project_overrides_and_inheritance_reach_actual_dispatch_and_task_evidence(system):
    db, registry, settings, server, chat, workers, scheduler = system
    one, two = two_projects(system)
    save(db, server, {**current(db, server), 'manager': {'model': SONNET, 'effort': 'low'}, 'worker': DEFAULT})
    set_override(db, server, one, OVERRIDE)
    assert current(db, server, one)['worker'] == worker_context(db, server, one) == OVERRIDE
    assert current(db, server, two)['worker'] == worker_context(db, server, two) == DEFAULT

    async def delegate(project, name):
        # A global manager can target either project; the tool's requested model cannot bypass a fixed override.
        return await scheduler.manager_action(None, {
            'tool': 'manager_action', 'callId': name, 'manager_execution_id': name,
            'conversation_id': chat.conversation()['id'],
            'arguments': {'action': 'delegate', 'project_id': project, 'objective': name,
                          'model': SONNET, 'effort': 'high'},
        })

    first = await delegate(one, 'project override')
    second = await delegate(two, 'inherited global')
    assert first['worker_route'] == OVERRIDE and second['worker_route'] == DEFAULT
    # Clear the override, then change the default. Earlier tasks retain their selected route.
    set_override(db, server, one, None)
    new_default = {'model': MODELS['Astra'], 'effort': 'low'}
    save(db, server, {**current(db, server), 'worker': new_default})
    third = await delegate(one, 'cleared override')
    assert third['worker_route'] == new_default
    for selected, expected in ((first, OVERRIDE), (second, DEFAULT), (third, new_default)):
        task = Tasks(db).claim()
        assert task['id'] == selected['id']  # one sequential global queue
        await workers._run(task)
        execution = db.one('SELECT model,effort FROM executions WHERE id=?', (task['lease_owner'],))
        assert execution == expected
        assert {k: CatalogServer.turns[-1][k] for k in ('model', 'effort')} == expected
        assert CatalogServer.threads[-1]['model'] == expected['model']
        event = db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected'", (task['id'],))
        assert json.loads(event['payload']) == expected
        assert Tasks(db).get(task['id'])['worker_route'] == assignment_route(db, server, task) == expected
    assert manager_route(db) == {'model': SONNET, 'effort': 'low'}
    assert project_worker(db, two) == {'revision': 0, 'override': None}


def test_project_selection_survives_reload_clearing_and_global_automatic_without_leaking(system):
    db, registry, settings, server, *_ = system
    one, two = two_projects(system)
    save(db, server, {**current(db, server), 'worker': DEFAULT})
    set_override(db, server, one, OVERRIDE)
    reloaded = Database(settings.db_path)
    assert current(reloaded, server, one)['worker'] == OVERRIDE
    assert current(reloaded, server, two)['worker'] == DEFAULT
    second_override = {'model': SONNET, 'effort': 'medium'}
    set_override(db, server, two, second_override)
    assert current(reloaded, server, two)['worker'] == second_override
    assert current(reloaded, server, one)['worker'] == OVERRIDE
    assert current(reloaded, server)['worker'] == DEFAULT
    set_override(db, server, two, None)
    save(db, server, {**current(db, server), 'worker': None})
    assert worker_context(reloaded, server, one) == OVERRIDE
    assert 'automatic' in worker_context(reloaded, server, two)
    assert delegation(reloaded, server, SONNET, 'high', two) == {'model': SONNET, 'effort': 'high'}
    assert delegation(reloaded, server, SONNET, 'high', one) == OVERRIDE
    # Explicit Automatic is distinct from an absent override and remembers only this project's manual selection.
    save_project_worker(db, server, one, {'revision': 1, 'override': {'worker': None, 'manual': OVERRIDE}})
    save(db, server, {**current(db, server), 'worker': DEFAULT})
    assert current(reloaded, server, one)['worker'] is None
    assert current(reloaded, server, one)['manual'] == OVERRIDE
    assert current(reloaded, server, two)['worker'] == DEFAULT
    set_override(db, server, one, None)
    assert project_worker(Database(settings.db_path), one)['override'] is None
    assert current(reloaded, server, one)['worker'] == DEFAULT
    # Legacy/direct queue insertion resolves through the same project-aware path at first execution.
    set_override(db, server, two, OVERRIDE)
    task = Tasks(db).add(two, 'direct task')
    assert assignment_route(reloaded, server, task) == OVERRIDE
    set_override(db, server, two, None)
    assert assignment_route(reloaded, server, task) == OVERRIDE


@pytest.mark.asyncio
async def test_manager_prompt_uses_its_project_worker_while_manager_dispatch_stays_global(system):
    db, _, _, server, chat, *_ = system
    one, two = two_projects(system)
    manager = {'model': SONNET, 'effort': 'high'}
    save(db, server, {**current(db, server), 'manager': manager, 'worker': DEFAULT})
    set_override(db, server, one, OVERRIDE)
    for project, expected in ((one, OVERRIDE), (two, DEFAULT), (None, DEFAULT)):
        receipt = await chat.submit(project, 'Explain the selected worker')
        await asyncio.wait_for(asyncio.gather(*chat.jobs.values()), timeout=5)
        execution = db.one('SELECT model,effort,status FROM executions WHERE id=?', (receipt['execution_id'],))
        assert execution == {**manager, 'status': 'completed'}
        prompt = json.loads(server.prompts[-1])
        assert prompt['worker'] == expected


def test_project_override_validation_is_atomic_revisioned_and_unrestricted(system):
    db, _, _, server, *_ = system
    one, two = two_projects(system)
    before = current(db, server)
    first = set_override(db, server, one, OVERRIDE)
    for bad in ({'model': 'missing', 'effort': 'low'}, {'model': SOL}, {'model': SOL, 'effort': 'ultra'}):
        with pytest.raises(ValueError):
            set_override(db, server, one, bad)
        assert project_worker(db, one) == first
    with pytest.raises(ValueError, match='changed'):
        save_project_worker(db, server, one, {'revision': 0, 'override': None})
    with pytest.raises(ValueError, match='Unknown project'):
        set_override(db, server, 'missing-project', OVERRIDE)
    server.catalog.append({'model': 'custom-worker', 'displayName': 'Custom worker', 'isDefault': False,
                           'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}],
                           'defaultReasoningEffort': 'ultra'})
    custom = {'model': 'custom-worker', 'effort': 'ultra'}
    set_override(db, server, one, custom)
    assert delegation(db, server, project_id=one) == custom
    assert current(db, server) == before
    assert project_worker(db, two)['override'] is None


def test_project_worker_api_persists_effective_selection_and_keeps_global_manager_independent(system):
    db, registry, settings, server, *_ = system
    one, two = two_projects(system)
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        endpoint = f'/api/projects/{one}/worker-model'
        assert client.put(endpoint, json={'revision': 0, 'override': None}).status_code == 401
        sign_in(client, app, settings)
        panel = client.get('/api/model-settings', params={'project_id': one}).json()
        assert panel['project_worker'] == {'revision': 0, 'override': None}
        assert panel['worker_selection']['worker'] == DEFAULT
        body = {'revision': 0, 'override': {'worker': OVERRIDE, 'manual': OVERRIDE}}
        assert client.put(endpoint, json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
        result = client.put(endpoint, json=body).json()
        assert result['worker_selection']['worker'] == OVERRIDE
        assert result['settings']['worker'] == DEFAULT  # global settings never become the project settings
        assert client.put(endpoint, json=body).status_code == 409
        for project, expected in ((one, OVERRIDE), (two, DEFAULT), (None, DEFAULT)):
            url = '/api/model-settings' + (f'?project_id={project}' if project else '')
            assert client.get(url).json()['worker_selection']['worker'] == expected
        global_settings = client.get('/api/model-settings').json()['settings']
        client.put('/api/model-settings', json={**global_settings, 'manager': {'model': SONNET, 'effort': 'high'}})
        client.put('/api/manager-favorites/0')
        recalled = client.post(f'/api/manager-favorites/0/recall?project_id={one}').json()
        assert recalled['project_id'] == one and recalled['worker_selection']['worker'] == OVERRIDE
        assert recalled['settings']['manager'] == {'model': SONNET, 'effort': 'high'}
        assert client.put(endpoint, json={'revision': 1, 'override': {'worker': {'model': SOL}, 'manual': DEFAULT}}).status_code == 409
        cleared = client.put(endpoint, json={'revision': 1, 'override': None}).json()
        assert cleared['project_worker'] == {'revision': 2, 'override': None}
        assert cleared['worker_selection']['worker'] == DEFAULT
        assert client.get('/api/model-settings?project_id=missing').status_code == 409
        session_cookies = dict(client.cookies)
    with TestClient(create_app(settings, CatalogServer(settings), CatalogServer), base_url='http://localhost') as client:
        client.cookies.update(session_cookies)
        assert client.get(f'/api/model-settings?project_id={one}').json()['project_worker']['override'] is None


def test_project_picker_adds_only_the_effective_off_family_worker(system):
    db, _, settings, server, *_ = system
    one, two = two_projects(system)
    for model in ('custom-global', 'custom-project'):
        server.catalog.append({'model': model, 'displayName': model, 'isDefault': False,
                               'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}],
                               'defaultReasoningEffort': 'ultra'})
    save(db, server, {**current(db, server), 'worker': {'model': 'custom-global', 'effort': 'ultra'}})
    set_override(db, server, one, {'model': 'custom-project', 'effort': 'ultra'})
    app = create_app(settings, server, CatalogServer)
    with TestClient(app, base_url='http://localhost') as client:
        sign_in(client, app, settings)
        for project, extra in ((one, 'custom-project'), (two, 'custom-global')):
            panel = client.get('/api/model-settings', params={'project_id': project}).json()
            assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra', extra]
            assert panel['providers']['codex']['choices'][-1]['levels'] == ['ultra']
        manual = current(db, server, one)['manual']
        save_project_worker(db, server, one, {'revision': 1, 'override': {'worker': None, 'manual': manual}})
        panel = client.get('/api/model-settings', params={'project_id': one}).json()
        assert [c['name'] for c in panel['providers']['codex']['choices']] == ['Luna', 'Sol', 'Astra']

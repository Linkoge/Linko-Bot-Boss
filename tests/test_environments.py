"""Main's harness panel environment picker: Main plus every registered instance, per-instance harness/autonomy/worker
harness written to that instance's own database and used from its next manager turn (models come from the model
picker's environment selector); office mode on every other instance."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import hashlib
import json
import time

import pytest
from test_instance_models import pair, registry  # noqa: F401
from test_model_settings import MODELS, CatalogServer

from ai_manager import harness
from ai_manager.model_settings import manager_route


def fixed_project(user_app):
    return user_app.state.registry.list()[0]['id']


@pytest.mark.smoke
def test_environment_list_comes_from_the_instance_registry(pair, tmp_path):
    owner, client, app, user_app, main, user, entry = pair
    assert owner.get('/api/environments').json() == {'environments': [{'name': 'Main', 'main': True},
                                                                      {'name': 'Writer'}]}
    registry(main, [entry, {**entry, 'name': 'Second', 'data': str(tmp_path / 'second')}])
    assert [e['name'] for e in owner.get('/api/environments').json()['environments']] == ['Main', 'Writer', 'Second']
    (main.data_dir / 'deploy' / 'installed.json').unlink()
    assert owner.get('/api/environments').json()['environments'] == [{'name': 'Main', 'main': True}]
    # Only Main has the picker; a restricted instance gets no other environment's settings.
    assert client.get('/api/environments').status_code == 403
    assert client.get('/api/instance-harness?name=Writer').status_code == 403


@pytest.mark.smoke
def test_instance_harness_autonomy_and_model_apply_to_that_instance_only(pair):
    owner, client, app, user_app, main, user, entry = pair
    project = fixed_project(user_app)
    shown = owner.get('/api/instance-harness?name=Writer').json()
    assert shown['name'] == 'Writer'
    assert shown['harness']['manager']['id'] == 'linko' and shown['harness']['autonomy'] == 'finish_request'
    assert shown['harness']['worker']['id'] == 'coding' and shown['harness']['has_projects']
    assert 'models' not in shown  # models are chosen only in the model picker

    saved = owner.put('/api/instance-harness?name=Writer', json={'manager': 'zero'}).json()
    assert saved['harness']['manager']['id'] == 'zero' and saved['harness']['autonomy'] == 'ask'
    saved = owner.put('/api/instance-harness?name=Writer', json={'autonomy': 'finish_request'}).json()
    assert saved['harness']['autonomy'] == 'finish_request'
    for scope in (None, project):  # the instance's own chat scope reads it at the next turn
        found, autonomy = harness.manager_turn(user_app.state.db, scope)
        assert (found.id, autonomy) == ('zero', 'finish_request')
    assert client.get('/api/harnesses').json()['manager']['id'] == 'zero'
    assert harness.manager_turn(app.state.db, None)[0].id == 'linko'  # Main is untouched
    saved = owner.put('/api/instance-harness?name=Writer', json={'worker': 'coding'}).json()
    assert saved['harness']['worker']['id'] == 'coding' and harness.saved(user_app.state.db, project)['worker'] == 'coding'
    assert 'worker' not in harness.saved(user_app.state.db, None)  # All Projects has no worker harness

    current = owner.get('/api/instance-model-settings?name=Writer').json()['settings']
    model = {**current, 'manager': {'model': MODELS['Luna'], 'effort': 'high'}}
    assert owner.put('/api/instance-model-settings?name=Writer', json=model).status_code == 200
    assert owner.get('/api/instance-model-settings?name=Writer').json()['settings']['manager'] == \
        {'model': MODELS['Luna'], 'effort': 'high'}
    assert manager_route(user_app.state.db, project) == {'model': MODELS['Luna'], 'effort': 'high'}

    # The instance's next manager turn runs under the chosen harness and model.
    CatalogServer.turns.clear()
    receipt = client.post('/api/chat', json={'content': 'Hello'}).json()
    db = user_app.state.db
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        execution = db.one('SELECT * FROM executions WHERE id=?', (receipt['execution_id'],))
        if execution['finished_at']:
            break
        time.sleep(0.01)
    assert execution['status'] == 'completed'
    assert (execution['model'], execution['effort']) == (MODELS['Luna'], 'high')
    zero = harness.builtin('zero')
    tool = harness.manager_tool(zero, True)
    expected = hashlib.sha256((zero.instructions(True) + json.dumps(tool, sort_keys=True)).encode()).hexdigest()
    conversation = user_app.state.chat.conversation(project)['id']
    assert db.setting('manager_tool_schema:' + conversation) == expected


def test_instance_harness_rejects_bad_input_and_reports_unreachable(pair, tmp_path):
    owner, client, app, user_app, main, user, entry = pair
    for body in ({'worker': 'nope'}, {}, {'manager': 'nope'}, {'autonomy': 'rounds'}, {'model': 'x'}):
        assert owner.put('/api/instance-harness?name=Writer', json=body).status_code == 409, body
    assert harness.saved(user_app.state.db, None) == {}
    assert owner.get('/api/instance-harness?name=Ghost').status_code == 409
    registry(main, [entry, {**entry, 'name': 'Offline', 'data': str(tmp_path / 'offline')}])
    response = owner.get('/api/instance-harness?name=Offline')
    assert response.status_code == 409 and 'unreachable' in response.json()['detail']
    assert owner.put('/api/instance-harness?name=Offline', json={'manager': 'zero'}).status_code == 409
    assert not (tmp_path / 'offline').exists()  # never creates an instance database


def test_office_mode_is_reported_for_every_instance_but_main(pair):
    owner, client, *_ = pair
    assert owner.get('/api/auth-status').json()['office'] is False
    assert client.get('/api/auth-status').json()['office'] is True

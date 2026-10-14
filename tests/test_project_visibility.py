import subprocess

import pytest
from test_minimal_flow import system as system
from test_web import client as client
from test_web import sign_in

from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks


@pytest.mark.parametrize('status', ['active', 'paused', 'archived'])
def test_visibility_retains_all_project_state_and_survives_discovery(system, status):
    db, registry, chat, settings = system
    p = registry.list()[0]
    p = registry.update(p['id'], p['revision'], {'status': status})
    path = settings.project_roots[0] / p['name']
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    origin = chat.conversation(p['id'])
    task = Tasks(db).add(p['id'], 'Keep this assignment')
    with db.transaction() as conn:
        conn.execute("INSERT INTO memory(project_id,name,content,updated_at) VALUES(?,'STATE.md','Retain notes',1)", (p['id'],))
        conn.execute("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,thread_id) VALUES('native',?,?,?,'running',1,'retained-native-thread')", (task['id'], p['id'], p['agent_id']))
        conn.execute("INSERT INTO questions(id,execution_id,request,kind,status,created_at) VALUES('question','native','{}','input','pending',1)")
    tables = ['projects', 'tasks', 'executions', 'conversations', 'messages', 'memory', 'questions']
    before = {t: db.all('SELECT * FROM '+t) for t in tables}
    assert registry.set_visibility(p['id'], False)['visible'] is False
    assert registry.get(p['id'])['status'] == status
    reopened = Registry(Database(db.path), settings.project_roots)
    assert reopened.get(p['id'])['visible'] is False
    assert reopened.add(path)['visible'] is False
    discovered = reopened.discover()
    assert next(x for x in discovered if x['id'] == p['id'])['visible'] is False
    assert chat.conversation(p['id'])['id'] == origin['id']
    assert reopened.set_visibility(p['id'], True)['visible'] is True
    assert before == {t: db.all('SELECT * FROM '+t) for t in tables}
    fresh = settings.project_roots[0] / 'new-empty-repo'
    fresh.mkdir()
    subprocess.run(['git', 'init', '-q', str(fresh)], check=True)
    found = reopened.discover()
    new = next(x for x in found if x['name'] == fresh.name)
    assert new['visible'] is True
    removed = reopened.update(p['id'], p['revision'], {'status': 'removed'})
    assert removed['id'] not in {x['id'] for x in reopened.discover()}
    assert reopened.add(path)['status'] == 'removed'
    with pytest.raises(ValueError):
        reopened.set_visibility(p['id'], True)


def test_visibility_api_auth_csrf_strict_boolean_and_portfolio(client):
    browser, app, settings = client
    p = app.state.registry.add(settings.project_roots[0] / 'one')
    url = f"/api/projects/{p['id']}/visibility"
    assert browser.put(url, json={'visible': False}).status_code == 401
    sign_in(browser, app, settings)
    assert browser.put(url, headers={'X-CSRF-Token': 'bad'}, json={'visible': False}).status_code == 403
    assert browser.put(url, json={'visible': 'false'}).status_code == 422
    assert browser.put(url, json={'visible': False}).json()['visible'] is False
    assert browser.get('/api/portfolio').json()['projects'][0]['visible'] is False
    assert browser.put(url, json={'visible': True}).json()['visible'] is True

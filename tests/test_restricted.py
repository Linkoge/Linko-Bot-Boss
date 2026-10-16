"""Restricted work instance: settings drive the instance, and restricted mode is enforced server-side."""
import json
import secrets
import sys
from pathlib import Path

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager.config import CODE_ROOT, Settings
from ai_manager.orchestration import MANAGER_TOOL, RESTRICTED_ACTIONS, RESTRICTED_TOOL
from ai_manager.web import create_app

FORBIDDEN_ACTIONS = ('deploy', 'deployment_status', 'save_deploy_setup', 'emergency_repair', 'repair_status')
SETUP = {'service': 'example.service', 'backup_command': ['ops', 'backup'], 'health_url': 'http://127.0.0.1:9000/health',
         'rollback_command': ['ops', 'rollback']}


def instance(tmp_path, name='work', restricted=True):
    project = tmp_path / name / 'folder'
    project.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / name / 'data', project_roots=(tmp_path / name,), restricted=restricted,
                        project_path=project if restricted else None, secure_cookie=False)
    return settings, create_app(settings, FakeServer(settings))


def sign_in(browser, settings):
    token, owner = (settings.data_dir / 'setup-token').read_text(), {'username': 'owner', 'password': secrets.token_urlsafe(16)}
    assert browser.post('/api/setup', json={**owner, 'token': token}).status_code == 200
    response = browser.post('/api/login', json=owner)
    browser.headers['X-CSRF-Token'] = response.json()['csrf']


@pytest.fixture
def work(tmp_path):
    settings, app = instance(tmp_path)
    with TestClient(app, base_url='http://localhost') as browser:
        sign_in(browser, settings)
        yield browser, app, settings


# ---- settings ---------------------------------------------------------------------------------------------------
@pytest.mark.smoke
def test_defaults_keep_the_main_instance_unchanged(monkeypatch):
    for name in ('AI_MANAGER_DATA', 'AI_MANAGER_PORT', 'AI_MANAGER_PROJECT', 'AI_MANAGER_RESTRICTED',
                 'AI_MANAGER_PROJECT_ROOTS'):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert not settings.restricted and settings.fixed_project is None and settings.port == 8787
    assert settings.data_dir == Path('~/.local/share/linko-bot-manager').expanduser()
    assert settings.project_roots == (Path('~/projects').expanduser(),)


def test_environment_drives_data_dir_port_and_the_one_project(tmp_path, monkeypatch):
    project = tmp_path / 'docs'
    project.mkdir()
    for name, value in {'AI_MANAGER_DATA': tmp_path / 'data', 'AI_MANAGER_PORT': 8899, 'AI_MANAGER_RESTRICTED': 1,
                        'AI_MANAGER_PROJECT': project, 'AI_MANAGER_PROJECT_ROOTS': tmp_path / 'ignored'}.items():
        monkeypatch.setenv(name, str(value))
    settings = Settings()
    assert settings.restricted and settings.port == 8899 and settings.data_dir == tmp_path / 'data'
    assert settings.fixed_project == project.resolve() and settings.project_roots == (project.resolve(),)
    assert settings.db_path == tmp_path / 'data' / 'manager.sqlite3'


def test_serve_binds_the_configured_port(tmp_path, monkeypatch):
    import uvicorn

    from ai_manager import cli

    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', ['linko', 'serve'])
    monkeypatch.setenv('AI_MANAGER_DATA', str(tmp_path / 'data'))
    monkeypatch.setenv('AI_MANAGER_PORT', '8899')
    cli.main()
    assert calls[0]['port'] == 8899 and calls[0]['host'] == '127.0.0.1'


def test_restricted_mode_needs_a_project_outside_the_application_code_and_data(tmp_path):
    data = tmp_path / 'data'
    with pytest.raises(ValueError, match='AI_MANAGER_PROJECT'):
        Settings(data_dir=data, restricted=True, project_path=None)
    with pytest.raises(ValueError, match='AI_MANAGER_PROJECT'):
        Settings(data_dir=data, restricted=True, project_path=tmp_path / 'missing')
    inside_data = data / 'project'
    inside_data.mkdir(parents=True)
    for bad in (inside_data, data, CODE_ROOT, CODE_ROOT / 'ai_manager', CODE_ROOT.parent):  # no self-edit
        with pytest.raises(ValueError, match='application code or data'):
            Settings(data_dir=data, restricted=True, project_path=bad)


# ---- server-side enforcement -----------------------------------------------------------------------------------
@pytest.mark.smoke
def test_restricted_instance_exposes_only_its_one_project(work, tmp_path):
    browser, app, settings = work
    assert browser.get('/api/auth-status').json()['restricted'] is True
    projects = browser.get('/api/portfolio').json()['projects']
    assert [p['path'] for p in projects] == [str(settings.project_path)]
    fixed = projects[0]['id']
    # No other project, and no All Projects view: an omitted project is the fixed one.
    assert browser.get('/api/portfolio').json()['scope_project_id'] == fixed
    assert browser.get('/api/portfolio', params={'project_id': 'other'}).status_code == 403
    assert browser.get('/api/history').json()['conversation']['project_id'] == fixed
    assert browser.get('/api/history', params={'project_id': 'other'}).status_code == 403
    assert browser.get('/api/chat-history', params={'project_id': 'other'}).status_code == 403
    assert browser.get('/api/projects/other').status_code == 403
    assert browser.post('/api/chat', json={'project_id': 'other', 'content': 'hi'}).status_code == 403
    assert browser.post('/api/todos', json={'project_id': 'other', 'title': 'x'}).status_code == 403
    todo = browser.post('/api/todos', json={'project_id': fixed, 'title': 'ok'})
    assert todo.status_code == 200 and browser.get('/api/todos').json()['entries'][0]['title'] == 'ok'


def test_project_add_discover_remove_are_rejected(work, tmp_path):
    browser, app, settings = work
    elsewhere = settings.project_path / 'second'  # inside the root, but not the fixed project itself
    elsewhere.mkdir()
    assert browser.post('/api/projects', json={'path': str(elsewhere)}).status_code == 403
    assert browser.post('/api/projects', json={'path': str(settings.project_path)}).status_code == 403
    assert browser.post('/api/projects/discover').status_code == 403
    with pytest.raises(ValueError, match='one fixed project'):
        app.state.registry.add(elsewhere)
    project = browser.get('/api/portfolio').json()['projects'][0]
    for status in ('removed', 'archived'):
        rejected = browser.patch(f"/api/projects/{project['id']}", json={'revision': project['revision'], 'status': status})
        assert rejected.status_code == 409 and 'fixed project' in rejected.json()['detail']
    assert browser.patch(f"/api/projects/{project['id']}", json={'revision': project['revision'],
                                                                'display_name': 'Work'}).status_code == 200


def test_discover_keeps_only_the_fixed_project(tmp_path):
    settings, app = instance(tmp_path)
    registry = app.state.registry
    with registry.db.transaction() as conn:
        conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('old','old','/old','a',1,1)")
    # A project registered before the instance became restricted is dropped from view.
    assert [p['path'] for p in registry.discover()] == [str(settings.project_path)]
    assert [p['path'] for p in registry.list()] == [str(settings.project_path)]


def test_deploy_deploy_setup_and_host_agent_endpoints_are_rejected(work):
    browser, app, settings = work
    fixed = browser.get('/api/portfolio').json()['projects'][0]['id']
    setup = f'/api/projects/{fixed}/deploy-setup'
    for method, path, body in (('get', '/api/deployment', None), ('post', '/api/deployment/cancel', {}),
                               ('get', setup, None), ('put', setup, {'setup': SETUP}),
                               ('get', '/api/repair', None),
                               ('post', '/api/repair', {'request_id': 'r1', 'instructions': 'install x',
                                                        'conversation_id': 'c'})):
        response = getattr(browser, method)(path, **({'json': body} if body is not None else {}))
        assert response.status_code == 403, (method, path)
    assert not (settings.data_dir / 'repair').exists() and not (settings.data_dir / 'deploy' / 'request.json').exists()
    assert app.state.db.setting('deploy_setup:' + fixed, 'none') == 'none'


@pytest.mark.parametrize('action', FORBIDDEN_ACTIONS)
async def test_manager_cannot_use_deploy_or_host_actions(work, action):
    browser, app, settings = work
    fixed = browser.get('/api/portfolio').json()['projects'][0]['id']
    params = {'tool': 'manager_action', 'manager_execution_id': 'owner-turn', 'conversation_id': 'c',
              'arguments': {'action': action, 'objective': 'install something', 'setup': SETUP, 'project_id': fixed}}
    with pytest.raises(ValueError, match='restricted instance'):
        await app.state.scheduler.manager_action(fixed, params)
    assert not (settings.data_dir / 'repair').exists() and not (settings.data_dir / 'deploy' / 'request.json').exists()


def test_pipeline_never_deploys_even_with_a_stored_setup(work):
    browser, app, settings = work
    fixed = browser.get('/api/portfolio').json()['projects'][0]['id']
    pipeline = app.state.scheduler.pipeline
    app.state.db.set_setting('deploy_setup:' + fixed, SETUP)  # e.g. copied in from another database
    assert not pipeline.deployable(fixed)
    assert pipeline.request(fixed, 'owner')['status'] == 'no deploy setup'
    with pytest.raises(ValueError, match='restricted instance'):
        pipeline.save_setup(fixed, SETUP)
    assert not pipeline.pending()


def test_the_manager_is_offered_no_deploy_or_host_tools(work):
    browser, app, settings = work
    from ai_manager.codex import MANAGER_INSTRUCTIONS, RESTRICTED_INSTRUCTIONS, RESTRICTED_NOTE
    assert app.state.chat.restricted and app.state.scheduler.restricted
    offered = RESTRICTED_TOOL['inputSchema']['properties']
    assert not set(FORBIDDEN_ACTIONS) & set(offered['action']['enum']) and 'setup' not in offered
    assert set(FORBIDDEN_ACTIONS) == RESTRICTED_ACTIONS
    assert set(MANAGER_TOOL['inputSchema']['properties']['action']['enum']) - set(offered['action']['enum']) == RESTRICTED_ACTIONS
    assert 'deploy' not in RESTRICTED_TOOL['description'] and 'emergency' not in json.dumps(RESTRICTED_TOOL)
    assert RESTRICTED_INSTRUCTIONS == MANAGER_INSTRUCTIONS + RESTRICTED_NOTE and 'one fixed project' in RESTRICTED_NOTE


def test_main_instance_behaviour_is_unchanged(tmp_path):
    settings, app = instance(tmp_path, 'main', restricted=False)
    other = tmp_path / 'main' / 'other'
    other.mkdir()
    with TestClient(app, base_url='http://localhost') as browser:
        sign_in(browser, settings)
        assert browser.get('/api/auth-status').json()['restricted'] is False
        assert browser.get('/api/deployment').status_code == 200
        assert browser.get('/api/repair').status_code == 200
        added = browser.post('/api/projects', json={'path': str(other)})
        assert added.status_code == 200
        assert browser.get('/api/portfolio').json()['scope_project_id'] is None
        assert browser.get('/api/portfolio', params={'project_id': added.json()['id']}).status_code == 200
        assert browser.post('/api/projects/discover').status_code == 200
        assert not app.state.chat.restricted and not app.state.scheduler.restricted


def test_two_instances_run_side_by_side_with_their_own_login_and_data(tmp_path):
    (a_settings, a_app), (b_settings, b_app) = instance(tmp_path, 'one'), instance(tmp_path, 'two')
    assert a_settings.db_path != b_settings.db_path
    with TestClient(a_app, base_url='http://localhost') as a, TestClient(b_app, base_url='http://localhost') as b:
        sign_in(a, a_settings)
        assert b.get('/api/portfolio').status_code == 401  # a login on one instance means nothing on the other
        sign_in(b, b_settings)
        assert a.get('/api/portfolio').json()['projects'][0]['path'] == str(a_settings.project_path)
        assert b.get('/api/portfolio').json()['projects'][0]['path'] == str(b_settings.project_path)
        assert a_settings.project_path != b_settings.project_path


def test_the_ui_hides_the_controls_a_restricted_instance_rejects():
    static = CODE_ROOT / 'ai_manager' / 'static'
    html, script, style = ((static / name).read_text() for name in ('index.html', 'app.js', 'style.css'))
    assert 'id="deployment-panel"' in html and 'state.restricted=!!auth.restricted' in script
    rule = next(line for line in style.splitlines() if line.startswith('.restricted '))
    for control in ('#all-projects', '#project-menu-button', '#discover', '#add-project', '#deployment-panel'):
        assert '.restricted ' + control in rule and f'id="{control[1:]}"' in html

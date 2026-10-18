"""Create/rename a GitHub repository from the project menu, against a mocked GitHub API. No delete action exists."""
import json
import logging
import os
import secrets
import subprocess

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager import github_projects
from ai_manager.config import Settings
from ai_manager.core import Tasks
from ai_manager.github_projects import ALLOWED, GitHubApi, GitHubProjects, TokenStore
from ai_manager.web import create_app

COMPANY_PAT = 'ghp_' + secrets.token_hex(18)
PAT = 'ghp_' + secrets.token_hex(18)  # generated at runtime; never a literal credential-looking value


class FakeGitHub:
    """Records every request; `fail` maps (method, path-prefix) to a status for one-shot failures."""

    def __init__(self, scopes='repo'):
        self.calls, self.fail, self.repos, self.scopes, self.login = [], {}, {}, scopes, 'octo'

    def __call__(self, method, url, headers, body, timeout):
        path = url.removeprefix(github_projects.API)
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload, headers['Authorization']))
        for (m, prefix), status in list(self.fail.items()):
            if m == method and path.startswith(prefix):
                del self.fail[m, prefix]
                return status, {}, {'message': f'refused with {headers["Authorization"]}'}
        if (method, path) == ('GET', '/user'):
            received = {} if self.scopes is None else {'x-oauth-scopes': self.scopes}
            return 200, received, {'login': self.login}
        if method == 'GET' and path.startswith('/user/memberships/orgs/'):
            return 200, {}, {'state': 'active', 'role': 'admin'}
        if method == 'GET' and path.startswith('/orgs/'):
            return 200, {}, {'members_can_create_public_repositories': True}
        if method == 'POST' and (path == '/user/repos' or path.startswith('/orgs/')):
            owner = 'octo' if path == '/user/repos' else path.split('/')[2]
            name = payload['name']
            if name in self.repos:
                return 422, {}, {'message': 'Repository creation failed.', 'errors': [{'message': 'name already exists'}]}
            self.repos[name] = {'name': name, 'full_name': f'{owner}/{name}', 'private': payload['private'],
                                'ssh_url': f'git@github.com:{owner}/{name}.git'}
            return 201, {}, self.repos[name]
        if method == 'PATCH' and path.startswith('/repos/'):
            old = path.rsplit('/', 1)[1]
            if old not in self.repos:
                return 404, {}, {'message': 'Not Found'}
            self.repos[payload['name']] = {**self.repos.pop(old), 'name': payload['name']}
            return 200, {}, self.repos[payload['name']]
        return 404, {}, {'message': 'Not Found'}

    def methods(self):
        return [(m, p) for m, p, _, _ in self.calls]


def run(*args):
    subprocess.run(args, check=True, capture_output=True, timeout=60)


@pytest.fixture
def hub(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,), secure_cookie=False,
                        github_token_file=tmp_path / 'config' / 'github-token')
    github = FakeGitHub()
    app = create_app(settings, FakeServer(settings), github_fetch=github)
    app.state.github_projects.pipeline.backups.tick = lambda: None
    with TestClient(app, base_url='http://localhost') as browser:
        owner = {'username': 'owner', 'password': secrets.token_urlsafe(16)}
        browser.post('/api/setup', json={**owner, 'token': (settings.data_dir / 'setup-token').read_text()})
        browser.headers['X-CSRF-Token'] = browser.post('/api/login', json=owner).json()['csrf']
        yield browser, app, settings, github


def with_token(browser, github):
    response = browser.put('/api/github/token', json={'token': PAT})
    assert response.status_code == 200, response.text
    response = browser.put('/api/github/tokens/company', json={'token': COMPANY_PAT})
    assert response.status_code == 200, response.text
    github.calls.clear()


def existing_project(app, settings, name='alpha', repo='alpha', owner='octo'):
    folder = settings.project_roots[0] / name
    folder.mkdir()
    run('git', 'init', '-q', '-b', 'main', str(folder))
    run('git', '-C', str(folder), 'remote', 'add', 'origin', f'git@github.com:{owner}/{repo}.git')
    (folder / 'f.txt').write_text('x')
    run('git', '-C', str(folder), 'add', '.')
    run('git', '-C', str(folder), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'first')
    return app.state.registry.add(folder)


def remote_of(folder):
    return subprocess.run(['git', '-C', str(folder), 'remote', 'get-url', 'origin'], capture_output=True, text=True,
                          check=True, timeout=60).stdout.strip()


@pytest.mark.smoke
def test_create_needs_a_token_and_then_creates_private_repo_folder_and_project(hub):
    browser, app, settings, github = hub
    refused = browser.post('/api/github/projects', json={'name': 'fresh'})
    assert refused.status_code == 409 and 'No GitHub token' in refused.json()['detail']
    assert github.calls == [] and not (settings.project_roots[0] / 'fresh').exists()
    with_token(browser, github)
    created = browser.post('/api/github/projects', json={'name': 'fresh'})
    assert created.status_code == 200, created.text
    body = created.json()
    assert github.calls[0][:3] == ('POST', '/user/repos', {'name': 'fresh', 'private': True})
    assert github.repos['fresh']['private'] is True and body['private'] is True
    folder = settings.project_roots[0] / 'fresh'
    assert remote_of(folder) == 'git@github.com:octo/fresh.git'
    assert subprocess.run(['git', '-C', str(folder), 'rev-list', '--count', 'main'], capture_output=True,
                          text=True, timeout=60).stdout.strip() == '1'
    project = app.state.registry.get(body['project']['id'])
    assert project['path'] == str(folder) and project['name'] == 'fresh'
    assert project['repository'] == 'git@github.com:octo/fresh.git'
    assert [s['status'] for s in body['steps']] == ['done', 'done', 'done']
    assert browser.post('/api/github/projects', json={'name': 'fresh'}).status_code == 409  # folder now exists


@pytest.mark.parametrize('tier', ['personal', 'company'])
def test_create_tiers_and_name_validation(hub, tier):
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    private = tier == 'personal'
    path = '/user/repos' if private else '/orgs/acme/repos'
    created = browser.post('/api/github/projects', json={'name': 'fresh', 'tier': tier, 'public_confirmed': not private})
    assert created.status_code == 200, created.text
    assert created.json()['private'] is private and github.repos['fresh']['private'] is private
    assert github.calls[0][:3] == ('POST', path, {'name': 'fresh', 'private': private})
    assert created.json()['project']['github_tier'] == tier
    assert (settings.project_roots[0] / 'fresh').is_dir()
    assert app.state.registry.get(created.json()['project']['id'])['name'] == 'fresh'
    for bad in ('', '../x', 'a/b', '.hidden', 'x.git', 'a b', 'a' * 101):
        assert browser.post('/api/github/projects', json={'name': bad}).status_code == 409
    assert github.methods() == [('POST', path)]


def test_create_failure_at_github_does_nothing_locally(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    github.repos['taken'] = {}
    response = browser.post('/api/github/projects', json={'name': 'taken'})
    assert response.status_code == 409 and 'name already exists' in response.json()['detail']
    assert response.json()['steps'] == [{'step': 'create GitHub repository', 'status': 'failed',
                                         'detail': response.json()['steps'][0]['detail']}]
    assert not (settings.project_roots[0] / 'taken').exists()


def test_create_failure_midway_rolls_back_folder_and_reports_repo_left_on_github(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)

    def explode(folder, name, remote):
        (folder / 'partial').write_text('x')
        raise OSError('disk full')

    monkeypatch.setattr(GitHubProjects, 'initialize', staticmethod(explode))
    response = browser.post('/api/github/projects', json={'name': 'half'})
    detail = response.json()
    assert response.status_code == 409
    statuses = {s['step']: s['status'] for s in detail['steps']}
    assert statuses == {'create GitHub repository': 'done', 'create local folder and git init': 'failed',
                        'remove the new local folder': 'rolled back', 'register in manager': 'not run'}
    assert 'WAS created and was not deleted' in detail['detail'] and 'octo/half' in detail['detail']
    assert not (settings.project_roots[0] / 'half').exists()
    assert 'half' not in [p['name'] for p in app.state.registry.list()]
    assert all(m != 'DELETE' for m, _ in github.methods())


def test_create_failure_at_registration_rolls_back_folder(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)
    monkeypatch.setattr(app.state.registry, 'add', lambda *a, **k: (_ for _ in ()).throw(ValueError('nope')))
    response = browser.post('/api/github/projects', json={'name': 'unreg'})
    statuses = {s['step']: s['status'] for s in response.json()['steps']}
    assert statuses['register in manager'] == 'failed' and statuses['remove the new local folder'] == 'rolled back'
    assert not (settings.project_roots[0] / 'unreg').exists()


@pytest.mark.parametrize('private', [True, False])
def test_rename_renames_github_folder_remote_and_manager_record(hub, private):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha', 'private': private}
    # A retained task workspace (git worktree) must keep working after the folder moves.
    worktree = settings.data_dir / 'assignment-workspaces' / 'w1'
    run('git', '-C', project['path'], 'worktree', 'add', '-q', '-b', 'linko/w1', str(worktree))
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta', 'private': not private})
    assert response.status_code == 200, response.text
    assert github.methods() == [('PATCH', '/repos/octo/alpha')] and github.calls[0][2] == {'name': 'beta'}
    assert github.repos['beta']['private'] is private
    new = settings.project_roots[0] / 'beta'
    assert new.is_dir() and not (settings.project_roots[0] / 'alpha').exists()
    assert remote_of(new) == 'git@github.com:octo/beta.git'
    saved = app.state.registry.get(project['id'])
    assert (saved['name'], saved['path'], saved['repository']) == ('beta', str(new), 'git@github.com:octo/beta.git')
    assert subprocess.run(['git', '-C', str(worktree), 'status', '--short'], capture_output=True, timeout=60).returncode == 0
    assert [s['status'] for s in response.json()['steps']] == ['done'] * 4


def test_rename_keeps_a_custom_display_name_and_clears_a_stale_one(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    custom = existing_project(app, settings, 'one', 'one')
    stale = existing_project(app, settings, 'two', 'two')
    github.repos.update(one={'name': 'one'}, two={'name': 'two'})
    app.state.registry.update(custom['id'], custom['revision'], {'display_name': 'Pretty'})
    app.state.registry.update(stale['id'], stale['revision'], {'display_name': 'two'})
    browser.post(f"/api/github/projects/{custom['id']}/rename", json={'name': 'uno'})
    browser.post(f"/api/github/projects/{stale['id']}/rename", json={'name': 'dos'})
    assert app.state.registry.get(custom['id'])['display_name'] == 'Pretty'
    assert app.state.registry.get(stale['id'])['display_name'] is None


def test_rename_is_blocked_while_the_project_has_a_running_task(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha'}
    task = Tasks(app.state.db).add(project['id'], 'busy work')
    app.state.db.run("UPDATE tasks SET state='running' WHERE id=?", (task['id'],))
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    assert response.status_code == 409 and 'running task' in response.json()['detail']
    assert github.calls == [] and (settings.project_roots[0] / 'alpha').is_dir()
    assert remote_of(settings.project_roots[0] / 'alpha').endswith('/alpha.git')
    app.state.db.run("UPDATE tasks SET state='done' WHERE id=?", (task['id'],))
    assert browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'}).status_code == 200


def test_rename_is_blocked_by_a_saved_deploy_setup_and_by_existing_target(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    (settings.project_roots[0] / 'beta').mkdir()
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    assert response.status_code == 409 and 'already exists' in response.json()['detail']
    app.state.db.set_setting('deploy_setup:' + project['id'], {'service': 'x.service'})
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'gamma'})
    assert response.status_code == 409 and 'deploy' in response.json()['detail']
    assert github.calls == []


def test_rename_github_refusal_changes_nothing(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha'}
    github.fail['PATCH', '/repos/octo/alpha'] = 403
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    assert response.status_code == 409 and 'Nothing was changed' in response.json()['detail']
    assert (settings.project_roots[0] / 'alpha').is_dir() and not (settings.project_roots[0] / 'beta').exists()
    assert app.state.registry.get(project['id'])['name'] == 'alpha'


def test_rename_failure_after_github_rolls_github_back(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha'}

    def refuse(src, dst):
        raise PermissionError('read-only')

    monkeypatch.setattr(github_projects.os, 'rename', refuse)
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    statuses = {s['step']: s['status'] for s in response.json()['steps']}
    assert statuses == {'rename GitHub repository': 'done', 'rename local folder': 'failed',
                        'rename GitHub repository back': 'rolled back'}
    assert [m for m, _ in github.methods()] == ['PATCH', 'PATCH'] and 'alpha' in github.repos and 'beta' not in github.repos
    assert app.state.registry.get(project['id'])['name'] == 'alpha'


def test_rename_failure_at_remote_or_database_rolls_back_everything(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha'}
    real = GitHubProjects.update_project

    def broken(self, *args):
        raise RuntimeError('database locked')

    monkeypatch.setattr(GitHubProjects, 'update_project', broken)
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    statuses = {s['step']: s['status'] for s in response.json()['steps']}
    assert statuses == {'rename GitHub repository': 'done', 'rename local folder': 'done', 'update git remote': 'done',
                        'update project in manager': 'failed', 'restore git remote': 'rolled back',
                        'rename local folder back': 'rolled back', 'rename GitHub repository back': 'rolled back'}
    folder = settings.project_roots[0] / 'alpha'
    assert folder.is_dir() and remote_of(folder) == 'git@github.com:octo/alpha.git' and 'alpha' in github.repos
    assert app.state.registry.get(project['id'])['path'] == str(folder)
    monkeypatch.setattr(GitHubProjects, 'update_project', real)
    assert browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'}).status_code == 200


def test_rollback_that_cannot_complete_is_reported_exactly(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = existing_project(app, settings)
    github.repos['alpha'] = {'name': 'alpha'}
    monkeypatch.setattr(GitHubProjects, 'update_project', lambda self, *a: (_ for _ in ()).throw(RuntimeError('x')))
    github.fail['PATCH', '/repos/octo/beta'] = 500  # the GitHub rollback itself is refused
    response = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'beta'})
    statuses = {s['step']: s['status'] for s in response.json()['steps']}
    assert statuses['rename GitHub repository back'] == 'failed'
    assert statuses['rename local folder back'] == 'rolled back'


def test_token_validation_storage_and_permissions(hub):
    browser, app, settings, github = hub
    assert browser.get('/api/github').json()['personal'] == {'configured': False, 'login': '', 'validation': 'missing'}
    github.scopes = 'repo, delete_repo'
    refused = browser.put('/api/github/token', json={'token': PAT})
    assert refused.status_code == 409 and 'only the repo scope' in refused.json()['detail']
    github.scopes = None
    assert 'classic' in browser.put('/api/github/token', json={'token': PAT}).json()['detail']
    github.scopes = 'read:org'
    assert 'repo scope' in browser.put('/api/github/token', json={'token': PAT}).json()['detail']
    assert browser.put('/api/github/token', json={'token': 'short'}).status_code == 409
    assert not settings.github_token_file.exists()
    github.scopes = 'repo'
    assert browser.put('/api/github/token', json={'token': PAT}).json() == {'configured': True, 'login': 'octo', 'validation': 'valid'}
    assert browser.get('/api/github').json()['personal'] == {'configured': True, 'login': 'octo', 'validation': 'valid'}
    assert settings.github_token_file.read_text().strip() == PAT
    assert settings.github_token_file.stat().st_mode & 0o777 == 0o600
    assert settings.github_token_file.parent.stat().st_mode & 0o777 == 0o700
    settings.github_token_file.chmod(0o644)  # a readable-by-others file is never used
    response = browser.post('/api/github/projects', json={'name': 'late'})
    assert response.status_code == 409 and 'readable only by the service user' in response.json()['detail']


def test_token_is_never_logged_stored_or_returned(hub, caplog, monkeypatch):
    browser, app, settings, github = hub
    caplog.set_level(logging.DEBUG)
    texts = [browser.put('/api/github/token', json={'token': PAT}).text, browser.get('/api/github').text]
    texts.append(browser.post('/api/github/projects', json={'name': 'quiet'}).text)
    github.fail['POST', '/user/repos'] = 401  # GitHub's error text echoes the Authorization header
    texts.append(browser.post('/api/github/projects', json={'name': 'loud'}).text)

    def broken(*args):
        raise OSError(f'connection to https://x failed for {PAT}')

    monkeypatch.setattr(app.state.registry, 'add', broken)
    texts.append(browser.post('/api/github/projects', json={'name': 'boom'}).text)
    api = GitHubApi(TokenStore(settings.github_token_file), broken)
    with pytest.raises(github_projects.GitHubError) as error:
        api.request('GET', '/user')
    texts.append(str(error.value))
    assert not any(PAT in text for text in texts)
    assert PAT not in caplog.text
    database = b''.join(path.read_bytes() for path in settings.data_dir.glob('manager.sqlite3*'))
    assert PAT.encode() not in database
    assert not any(PAT in str(p.read_bytes()) for p in settings.project_roots[0].rglob('*') if p.is_file())


def test_there_is_no_delete_action_anywhere(hub):
    browser, app, settings, github = hub
    assert not [r for r in app.routes if 'DELETE' in getattr(r, 'methods', ()) and 'github' in r.path]
    assert {m for m, _ in ALLOWED} == {'GET', 'POST', 'PATCH'}
    with_token(browser, github)
    api = GitHubApi(TokenStore(settings.github_token_file), github)
    for method, path in (('DELETE', '/repos/octo/alpha'), ('PATCH', '/repos/octo'), ('GET', '/repos/octo/alpha'),
                         ('POST', '/repos/octo/alpha/transfer'), ('PUT', '/user')):
        with pytest.raises(github_projects.GitHubError):
            api.request(method, path)
    assert browser.delete('/api/github/projects/x').status_code in {404, 405}
    assert github.calls == []
    assert "'DELETE'" not in open(github_projects.__file__).read()


def test_all_github_routes_need_the_owner_and_csrf(hub):
    browser, app, settings, github = hub
    routes = (('get', '/api/github', None), ('put', '/api/github/token', {'token': PAT}),
              ('put', '/api/github/organization', {'company_org': 'acme'}),
              *((method, f'/api/github/tokens/{tier}' + suffix, body)
                for tier in ('personal', 'company')
                for method, suffix, body in [('put', '', {'token': PAT}), ('post', '/validate', None), ('post', '/remove', None)]),
              ('post', '/api/github/projects', {'name': 'n'}), ('post', '/api/github/projects/p/rename', {'name': 'n'}))
    for method, path, body in routes:
        anonymous = TestClient(app, base_url='http://localhost')  # no lifespan: the running app is shared
        assert anonymous.request(method, path, json=body).status_code == 401
        if method != 'get':
            assert getattr(browser, method)(path, json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert github.calls == []


def test_restricted_instance_rejects_every_github_action(tmp_path):
    project = tmp_path / 'work' / 'folder'
    project.mkdir(parents=True)
    github = FakeGitHub()
    settings = Settings(data_dir=tmp_path / 'work' / 'data', project_roots=(tmp_path / 'work',), restricted=True,
                        project_path=project, secure_cookie=False, github_token_file=tmp_path / 'tok')
    app = create_app(settings, FakeServer(settings), github_fetch=github)
    app.state.github_projects.pipeline.backups.tick = lambda: None
    with TestClient(app, base_url='http://localhost') as browser:
        owner = {'username': 'owner', 'password': secrets.token_urlsafe(16)}
        browser.post('/api/setup', json={**owner, 'token': (settings.data_dir / 'setup-token').read_text()})
        browser.headers['X-CSRF-Token'] = browser.post('/api/login', json=owner).json()['csrf']
        project_id = app.state.registry.add(project)['id']
        for method, path, body in (('get', '/api/github', None), ('put', '/api/github/token', {'token': PAT}),
                                   ('put', '/api/github/organization', {'company_org': 'acme'}),
              *((method, f'/api/github/tokens/{tier}' + suffix, body)
                for tier in ('personal', 'company')
                for method, suffix, body in [('put', '', {'token': PAT}), ('post', '/validate', None), ('post', '/remove', None)]),
                                   ('post', '/api/github/projects', {'name': 'n'}),
                                   ('post', f'/api/github/projects/{project_id}/rename', {'name': 'n'})):
            response = getattr(browser, method)(path, **({'json': body} if body else {}))
            assert response.status_code == 403 and 'Not available on a restricted instance' in response.text
    assert github.calls == [] and not (tmp_path / 'tok').exists()
    # The domain object refuses on its own too, whatever route reaches it.
    for call in (lambda: app.state.github_projects.create('n'), lambda: app.state.github_projects.save_token(PAT)):
        with pytest.raises(ValueError, match='Not available on a restricted instance'):
            call()


def test_menu_offers_create_and_rename_only_on_the_main_instance():
    source = open(os.path.join(os.path.dirname(github_projects.__file__), 'static', 'app.js')).read()
    menu = source[source.index('function renderProjectMenu'):source.index("$('project-menu-button').onclick")]
    assert "if(!state.restricted){" in menu and 'Create project' in menu and 'Rename project' in menu
    assert 'delete' not in source[source.index('function createGithubProject'):source.index('function renameProject()')].lower()


def test_company_org_is_one_persisted_setting_and_validated(hub):
    browser, app, settings, github = hub
    response = browser.put('/api/github/organization', json={'company_org': ' Example-Co '})
    assert response.status_code == 200
    assert response.json()['company_org'] == 'Example-Co'
    assert app.state.db.setting('github_company_org') == 'Example-Co'
    # A newly constructed service reads the same saved source, without a cached configuration value.
    service = GitHubProjects(app.state.db, app.state.registry, settings, app.state.github_projects.pipeline, fetch=github)
    assert service.status()['company_org'] == browser.get('/api/github').json()['company_org'] == 'Example-Co'
    for org in ('../other', 'two/orgs', 'bad_name', '-edge', 'edge-', 'two--hyphens', 'a' * 40, 'a\nb'):
        response = browser.put('/api/github/organization', json={'company_org': org})
        assert response.status_code == 409, org
        assert service.company_org() == 'Example-Co'
    for org in ('a', 'A-1', 'a' * 39, ''):
        assert browser.put('/api/github/organization', json={'company_org': org}).status_code == 200
    assert service.company_org() == '' and github.calls == []


def test_allow_list_accepts_only_the_saved_org_and_exact_creation_path(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    api = app.state.github_projects.api
    for configured in ('', 'bad/name', 'acme'):
        app.state.db.set_setting('github_company_org', configured)
        for path in ('/orgs/other/repos', '/orgs/acme/repos/', '/orgs/acme/repos?x=1',
                     '/orgs/acme/repos/transfer', '/orgs/acme/../other/repos', '/orgs/acme%2Fother/repos',
                     '/orgs/acme/repos\n', '/user/repos\n'):
            with pytest.raises(github_projects.GitHubError, match='not available'):
                api.request('POST', path, {'name': 'x'})
        if configured != 'acme':
            with pytest.raises(github_projects.GitHubError, match='not available'):
                api.request('POST', '/orgs/acme/repos', {'name': 'x'})
    assert github.calls == []
    browser.put('/api/github/organization', json={'company_org': 'other'})
    with pytest.raises(github_projects.GitHubError, match='not available'):
        api.request('POST', '/orgs/acme/repos', {'name': 'x'})
    api.request('POST', '/orgs/other/repos', {'name': 'x', 'private': False})
    assert github.methods() == [('POST', '/orgs/other/repos')]


def test_company_requires_org_and_explicit_public_confirmation(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    body = {'name': 'company', 'tier': 'company', 'public_confirmed': True}
    response = browser.post('/api/github/projects', json=body)
    assert response.status_code == 409 and 'Settings' in response.json()['detail']
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    for confirmation in (False, None):
        body.pop('public_confirmed', None)
        if confirmation is False:
            body['public_confirmed'] = False
        response = browser.post('/api/github/projects', json=body)
        assert response.status_code == 409 and 'public' in response.json()['detail']
    assert github.calls == [] and list(settings.project_roots[0].iterdir()) == []
    assert browser.post('/api/github/projects', json={'name': 'x', 'tier': 'other'}).status_code == 409
    # Legacy or forged visibility/owner inputs cannot change the fixed Personal destination.
    response = browser.post('/api/github/projects', json={'name': 'personal', 'private': False, 'owner': 'other'})
    assert response.status_code == 200
    assert github.calls[0][:3] == ('POST', '/user/repos', {'name': 'personal', 'private': True})


def test_secret_check_runs_before_company_api_creation_and_blocks_findings(hub, monkeypatch):
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    initialize = GitHubProjects.initialize

    def contaminated(folder, name, remote):
        initialize(folder, name, remote)
        (folder / 'config.txt').write_text(PAT)
        run('git', '-C', str(folder), 'add', 'config.txt')
        run('git', '-C', str(folder), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'config')

    monkeypatch.setattr(GitHubProjects, 'initialize', staticmethod(contaminated))
    response = browser.post('/api/github/projects', json={'name': 'unsafe', 'tier': 'company', 'public_confirmed': True})
    assert response.status_code == 409 and 'Secret check failed' in response.json()['detail']
    assert PAT not in response.text
    assert github.calls == [] and app.state.registry.list() == []
    assert not (settings.project_roots[0] / 'unsafe').exists()


@pytest.mark.parametrize('status', [401, 403, 404])
def test_org_permission_error_is_clear_and_removes_prepared_folder(hub, status):
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    github.fail['POST', '/orgs/acme/repos'] = status
    response = browser.post('/api/github/projects', json={'name': 'denied', 'tier': 'company', 'public_confirmed': True})
    assert response.status_code == 409
    assert "token's account needs permission to create repositories in the organization acme" in response.json()['detail']
    assert PAT not in response.text and app.state.registry.list() == []
    assert not (settings.project_roots[0] / 'denied').exists()


@pytest.mark.parametrize('tier,owner', [('personal', 'octo'), ('company', 'acme')])
def test_created_tier_survives_rename_and_org_settings_change(hub, tier, owner):
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    created = browser.post('/api/github/projects', json={'name': 'first', 'tier': tier, 'public_confirmed': True})
    assert created.status_code == 200, created.text
    project = created.json()['project']
    assert project['github_tier'] == tier
    browser.put('/api/github/organization', json={'company_org': 'other'})
    github.calls.clear()
    renamed = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'second'})
    assert renamed.status_code == 200, renamed.text
    assert github.calls[0][:3] == ('PATCH', f'/repos/{owner}/first', {'name': 'second'})
    assert renamed.json()['project']['github_tier'] == tier
    assert app.state.registry.list()[0]['github_tier'] == tier
    assert remote_of(settings.project_roots[0] / 'second') == f'git@github.com:{owner}/second.git'
    assert github.repos['second']['private'] == (tier == 'personal')


def test_tier_save_failure_rolls_back_registration(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    app.state.db.run("""CREATE TRIGGER refuse_tier BEFORE INSERT ON settings
                       WHEN NEW.key LIKE 'github_tier:%'
                       BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END""")
    response = browser.post('/api/github/projects', json={'name': 'unregistered'})
    assert response.status_code == 409
    assert 'WAS created and was not deleted' in response.json()['detail']
    assert app.state.registry.list() == []
    assert not (settings.project_roots[0] / 'unregistered').exists()


@pytest.mark.parametrize('tier,credential', [('personal', PAT), ('company', COMPANY_PAT)])
def test_separate_token_lifecycle_keeps_values_out_of_responses_and_logs(hub, caplog, tier, credential):
    browser, app, settings, github = hub
    caplog.set_level(logging.DEBUG)
    endpoint = f'/api/github/tokens/{tier}'
    other = 'company' if tier == 'personal' else 'personal'
    assert browser.get('/api/github').json()[tier]['validation'] == 'missing'
    github.login = 'connected'
    saved = browser.put(endpoint, json={'token': credential})
    assert saved.status_code == 200 and saved.json()['login'] == 'connected'
    assert saved.json()['validation'] == 'valid'
    store = app.state.github_projects.stores[tier]
    assert store.read() == credential
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert not app.state.github_projects.stores[other].configured()
    github.calls.clear()
    validated = browser.post(endpoint + '/validate')
    assert validated.status_code == 200
    assert github.calls[0][3] == 'Bearer ' + credential
    replacement = secrets.token_hex(20)
    github.login = 'replacement'
    replaced = browser.put(endpoint, json={'token': replacement})
    assert replaced.status_code == 200 and replaced.json()['login'] == 'replacement'
    assert store.read() == replacement
    github.fail['GET', '/user'] = 401
    rejected = browser.post(endpoint + '/validate')
    assert rejected.status_code == 409
    assert browser.get('/api/github').json()[tier]['validation'] == 'invalid'
    github.scopes = 'repo, delete_repo'
    bad_replacement = browser.put(endpoint, json={'token': credential})
    assert bad_replacement.status_code == 409 and store.read() == replacement
    github.scopes = 'repo'
    invalid_body = browser.put(endpoint, json={'token': credential * 20})
    invalid_type = browser.put(endpoint, json={'token': {'value': credential}})
    assert invalid_body.status_code == invalid_type.status_code == 422
    removed = browser.post(endpoint + '/remove')
    assert removed.json() == {'configured': False, 'login': '', 'validation': 'missing'}
    assert not store.path.exists()
    assert browser.post(endpoint + '/remove').status_code == 200
    assert browser.post(endpoint + '/validate').status_code == 409
    texts = [r.text for r in (saved, validated, replaced, rejected, bad_replacement, invalid_body, invalid_type, removed)]
    texts += [caplog.text, browser.get('/api/github').text]
    assert all(credential not in text and replacement not in text for text in texts)
    database = b''.join(path.read_bytes() for path in settings.data_dir.glob('manager.sqlite3*'))
    assert credential.encode() not in database and replacement.encode() not in database


@pytest.mark.parametrize('tier', ['personal', 'company'])
@pytest.mark.parametrize('scopes,message', [(None, 'classic'), ('read:org', 'repo scope'),
                                            ('repo, delete_repo', 'only the repo scope'),
                                            ('repo, read:org', 'only the repo scope')])
def test_both_tokens_require_classic_repo_only(hub, tier, scopes, message):
    browser, app, _, github = hub
    github.scopes = scopes
    response = browser.put(f'/api/github/tokens/{tier}', json={'token': PAT})
    assert response.status_code == 409 and message in response.json()['detail']
    assert not app.state.github_projects.stores[tier].configured()


def test_legacy_file_becomes_personal_without_copying_it_to_company(hub):
    browser, app, settings, github = hub
    legacy = TokenStore(settings.github_token_file)
    legacy.write(PAT)
    before = legacy.path.stat().st_ino
    service = GitHubProjects(app.state.db, app.state.registry, settings, app.state.github_projects.pipeline, fetch=github)
    assert service.store.path == legacy.path and service.store.read() == PAT
    assert legacy.path.stat().st_ino == before
    assert service.token_status('personal')['validation'] == 'unvalidated'
    assert not service.stores['company'].configured()
    assert service.validate_token('personal')['login'] == 'octo'
    github.calls.clear()
    assert service.create('migrated')['tier'] == 'personal'
    assert github.calls[0][3] == 'Bearer ' + PAT
    assert not service.stores['company'].configured()


def test_company_requires_its_own_token_and_org_without_personal_fallback(hub):
    browser, app, settings, github = hub
    assert browser.put('/api/github/token', json={'token': PAT}).status_code == 200
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    github.calls.clear()
    response = browser.post('/api/github/projects', json={'name': 'missing', 'tier': 'company', 'public_confirmed': True})
    assert response.status_code == 409 and 'GitHub settings' in response.json()['detail']
    assert not (settings.project_roots[0] / 'missing').exists() and github.calls == []
    browser.put('/api/github/tokens/company', json={'token': COMPANY_PAT})
    browser.post('/api/github/tokens/personal/remove')
    github.calls.clear()
    response = browser.post('/api/github/projects', json={'name': 'missing', 'tier': 'personal'})
    assert response.status_code == 409 and github.calls == []
    browser.put('/api/github/organization', json={'company_org': ''})
    assert browser.post('/api/github/projects', json={'name': 'missing', 'tier': 'company', 'public_confirmed': True}).status_code == 409
    assert github.calls == []


@pytest.mark.parametrize('tier,credential,owner', [('personal', PAT, 'octo'), ('company', COMPANY_PAT, 'acme')])
def test_create_rename_and_push_use_only_the_tiers_credential(hub, monkeypatch, tier, credential, owner):
    import asyncio

    from ai_manager import github_backup
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    github.calls.clear()
    created = browser.post('/api/github/projects', json={'name': 'first', 'tier': tier, 'public_confirmed': True})
    assert created.status_code == 200
    project = created.json()['project']
    renamed = browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'second'})
    assert renamed.status_code == 200
    assert [call[3] for call in github.calls] == ['Bearer ' + credential] * 2
    root = settings.project_roots[0] / 'second'
    calls = []
    original = github_backup.git_bytes

    def offline_git(path, *args, **kwargs):
        if args[0] in {'ls-remote', 'fetch', 'push'}:
            env = kwargs['env']
            assert env['LINKO_GITHUB_CREDENTIAL'] == credential
            assert env['GIT_CONFIG_VALUE_0'] == ''  # Disable host credential helpers.
            assert f'https://github.com/{owner}/second.git' in args
            assert credential not in str(args)
            helper = env['GIT_ASKPASS']
            assert credential not in open(helper).read()
            assert os.stat(helper).st_mode & 0o777 == 0o700
            user = subprocess.run([helper, 'Username'], env={**os.environ, **env}, capture_output=True, text=True, timeout=10)
            value = subprocess.run([helper, 'Password'], env={**os.environ, **env}, capture_output=True, text=True, timeout=10)
            assert user.stdout.strip() == 'x-access-token' and value.stdout.strip() == credential
            calls.append((args, helper))
            return b''
        return original(path, *args, **kwargs)

    monkeypatch.setattr(github_backup, 'git_bytes', offline_git)
    backup = app.state.github_projects.pipeline.backups
    backup.request(project['id'])
    state = app.state.db.setting(github_backup.PREFIX + project['id'])
    asyncio.run(backup.attempt(renamed.json()['project'], state))
    assert [args[0] for args, _ in calls] == ['ls-remote', 'push']
    assert not any(os.path.exists(helper) for _, helper in calls)
    assert app.state.db.setting(github_backup.PREFIX + project['id'])['state'] == 'synced'
    assert credential not in (root / '.git/config').read_text()
    browser.post(f'/api/github/tokens/{tier}/remove')
    calls.clear()
    asyncio.run(backup.attempt(renamed.json()['project'], state))
    assert calls == []  # No SSH or other-tier fallback.
    assert app.state.db.setting(github_backup.PREFIX + project['id'])['state'] == 'needs-attention'
    assert browser.post(f"/api/github/projects/{project['id']}/rename", json={'name': 'third'}).status_code == 409


@pytest.mark.parametrize('membership,policy,expected', [
    ({'state': 'active', 'role': 'admin'}, {}, 'Organization owner'),
    ({'state': 'active', 'role': 'member'}, {'members_can_create_public_repositories': True}, 'policy allows'),
    ({'state': 'active', 'role': 'member'}, {}, 'did not expose creation policy'),
    ({'state': 'pending', 'role': 'member'}, {}, 'not an active member'),
    ({'state': 'active', 'role': 'member'}, {'members_can_create_public_repositories': False}, 'does not allow'),
])
def test_company_validation_checks_account_membership_and_public_creation_policy(hub, membership, policy, expected):
    browser, app, _, github = hub
    browser.put('/api/github/organization', json={'company_org': 'acme'})

    def organization_api(method, url, headers, body, timeout):
        if url.endswith('/user/memberships/orgs/acme'):
            return 200, {}, membership
        if url.endswith('/orgs/acme'):
            return 200, {}, policy
        return github(method, url, headers, body, timeout)

    app.state.github_projects.apis['company'].fetch = organization_api
    response = browser.put('/api/github/tokens/company', json={'token': COMPANY_PAT})
    assert expected in response.text
    assert response.status_code == (409 if expected in {'not an active member', 'does not allow'} else 200)


def test_company_permission_check_reports_api_limit_without_adding_scope(hub):
    browser, app, _, github = hub
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    github.fail['GET', '/user/memberships/orgs/acme'] = 403
    response = browser.put('/api/github/tokens/company', json={'token': COMPANY_PAT})
    assert response.status_code == 200 and 'did not expose' in response.json()['org_permission']
    assert github.calls[-1][3] == 'Bearer ' + COMPANY_PAT
    browser.put('/api/github/organization', json={'company_org': 'other'})
    assert browser.get('/api/github').json()['company']['validation'] == 'unvalidated'
    github.calls.clear()
    assert browser.post('/api/github/tokens/company/validate').status_code == 200
    assert ('GET', '/user/memberships/orgs/other') in github.methods()


def test_token_store_refuses_files_inside_a_repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    run('git', 'init', '-q', str(root))
    store = TokenStore(root / 'config' / 'credential')
    with pytest.raises(github_projects.GitHubError, match='outside repositories'):
        store.write(PAT)
    assert not store.path.exists()


def test_legacy_endpoint_validation_errors_never_echo_submitted_values(hub, caplog):
    browser, _, _, _ = hub
    for value in [PAT * 20, {'value': PAT}, [PAT]]:
        response = browser.put('/api/github/token', json={'token': value})
        assert response.status_code == 422 and PAT not in response.text
    assert PAT not in caplog.text

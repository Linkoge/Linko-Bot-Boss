"""Offline (local-only) projects: registry field, creation, backup skip, GitHub rejection, visibility cache."""
import asyncio
import subprocess

import pytest
from test_github_projects import FakeGitHub, existing_project, hub, run, with_token  # noqa: F401

from ai_manager import github_backup as backup_module
from ai_manager.github_backup import PREFIX, GitHubBackup


def create_offline(browser, name='local'):
    response = browser.post('/api/projects/offline', json={'name': name})
    assert response.status_code == 200, response.text
    return response.json()


def test_offline_creation_makes_git_repo_on_main_without_github(hub):
    browser, app, settings, github = hub
    body = create_offline(browser)
    folder = settings.project_roots[0] / 'local'
    branch = subprocess.run(['git', '-C', str(folder), 'branch', '--show-current'], capture_output=True, text=True,
                            check=True, timeout=60).stdout.strip()
    assert branch == 'main'
    project = app.state.registry.get(body['project']['id'])
    assert project['github_tier'] == 'offline' and project['repo_visibility'] == 'offline'
    assert project['path'] == str(folder) and github.calls == []
    assert browser.post('/api/projects/offline', json={'name': 'local'}).status_code == 409
    assert browser.post('/api/projects/offline', json={'name': '../x'}).status_code == 409


def test_registry_offline_field_and_invalid_tier(hub):
    browser, app, settings, _ = hub
    listed = [p for p in app.state.registry.list() if p['github_tier'] == 'offline']
    assert listed == []
    create_offline(browser)
    assert [p['repo_visibility'] for p in app.state.registry.list()] == ['offline']
    folder = settings.project_roots[0] / 'other'
    folder.mkdir()
    with pytest.raises(ValueError):
        app.state.registry.add(folder, github_tier='bogus')


@pytest.mark.asyncio
async def test_backup_skips_offline_even_with_remote(hub, monkeypatch):
    browser, app, settings, github = hub
    project = create_offline(browser)['project']
    run('git', '-C', project['path'], 'remote', 'add', 'origin', 'git@github.com:octo/local.git')
    calls = []
    monkeypatch.setattr(backup_module, 'git_bytes', lambda *a, **k: calls.append(a) or b'')
    backups = GitHubBackup(app.state.db, app.state.registry)
    backups.credentials = lambda p: calls.append('credentials') or 'x'
    backups.request(project['id'])
    backups.tick()
    assert backups.job is None
    await backups.attempt(app.state.registry.get(project['id']), {})
    assert calls == [] and github.calls == []
    assert app.state.db.setting(PREFIX + project['id']) is None
    assert app.state.registry.get(project['id'])['github_backup'] == {}


def test_github_actions_reject_offline_projects(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    project = create_offline(browser)['project']
    refused = browser.post(f'/api/github/projects/{project["id"]}/rename', json={'name': 'renamed'})
    assert refused.status_code == 409 and 'offline' in refused.json()['detail'].lower()
    assert (settings.project_roots[0] / 'local').is_dir() and github.calls == []
    with pytest.raises(ValueError):
        app.state.github_projects.push_credentials(app.state.registry.get(project['id']))
    refresh = browser.post('/api/github/visibility/refresh')
    assert refresh.status_code == 200 and github.calls == []


def test_visibility_is_cached_on_create_and_refreshed_boundedly(hub):
    browser, app, settings, github = hub
    with_token(browser, github)
    browser.put('/api/github/organization', json={'company_org': 'acme'})
    private = browser.post('/api/github/projects', json={'name': 'priv'}).json()['project']
    public = browser.post('/api/github/projects', json={'name': 'pub', 'tier': 'company', 'public_confirmed': True}).json()['project']
    unknown = existing_project(app, settings)
    registry = app.state.registry
    assert registry.get(private['id'])['repo_visibility'] == 'private'
    assert registry.get(public['id'])['repo_visibility'] == 'public'
    assert registry.get(unknown['id'])['repo_visibility'] is None
    github.calls.clear()
    github.repos['alpha'] = {'name': 'alpha', 'full_name': 'octo/alpha', 'private': False}
    github.repos['priv'] = {'private': True}
    assert browser.post('/api/github/visibility/refresh').json()['refreshed'] == 1  # fresh cache is not re-fetched
    assert registry.get(unknown['id'])['repo_visibility'] == 'public'
    assert [m for m, _ in github.methods()] == ['GET'] and github.methods()[0][1] == '/repos/octo/alpha'
    github.calls.clear()
    assert browser.post('/api/github/visibility/refresh').json()['refreshed'] == 0 and github.calls == []


@pytest.mark.asyncio
async def test_github_project_backup_still_attempted(hub):
    browser, app, settings, github = hub
    project = existing_project(app, settings)
    backups = GitHubBackup(app.state.db, app.state.registry)
    backups.request(project['id'])
    assert app.state.db.setting(PREFIX + project['id'])['state'] == 'pending'
    backups.tick()
    assert backups.job is not None
    await backups.close()

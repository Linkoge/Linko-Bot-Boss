"""Chromium shared-editor regressions against a disposable app with a mocked GitHub transport."""
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from test_github_projects import FakeGitHub, PAT, existing_project
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
github = FakeGitHub()
app = create_app(s, FakeServer(s), github_fetch=github)
app.state.github_projects.store.write(PAT)
existing_project(app, s)
github.repos['alpha'] = {'name': 'alpha', 'private': True}
uvicorn.run(app, host='127.0.0.1', port=s.port, log_level='error')
'''


@pytest.fixture(scope='module')
def browser_app(tmp_path_factory):
    root = tmp_path_factory.mktemp('github-editor')
    (root / 'projects').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PORT': str(port),
           'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0',
           'AI_MANAGER_RESTRICTED': '0', 'AI_MANAGER_PROJECT': '', 'PYTHONPATH': str(ROOT),
           'AI_MANAGER_GITHUB_TOKEN_FILE': str(root / 'config' / 'github-token')}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM], env=env, cwd=ROOT)
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/api/auth-status', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail('Disposable server did not start')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1200, 'height': 900})
            login = context.new_page()
            login.set_default_timeout(10000)
            login.goto(url)
            login.locator('#username').fill('owner')
            login.locator('#password').fill(secrets.token_urlsafe(16))
            login.locator('#setup-token').fill((root / 'data/setup-token').read_text())
            login.locator('#login-button').click()
            login.wait_for_function('()=>state.ready&&!!state.history')
            login.close()
            yield context, url, root
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def page(browser_app):
    context, url, _ = browser_app
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_function('()=>state.ready&&!!state.history&&!pollRunning')
    page.evaluate("async()=>{clearTimeout(pollTimer);await selectProject(state.portfolio.projects.find(p=>p.name==='alpha').id)}")
    # Count calls directly, so unrelated background reads cannot conceal a cancellation refresh.
    page.evaluate("""()=>{
      window.editorChecks={submit:0,invalid:0,refresh:0,select:0};
      $('editor-form').addEventListener('submit',()=>editorChecks.submit++);
      $('editor-form').addEventListener('invalid',()=>editorChecks.invalid++,true);
      const originalRefresh=refresh,originalSelect=selectProject;
      refresh=(...args)=>{editorChecks.refresh++;return originalRefresh(...args)};
      selectProject=(...args)=>{editorChecks.select++;return originalSelect(...args)};
    }""")
    yield page
    assert errors == []
    page.close()


def menu_action(page, name):
    page.locator('#project-menu-button').click()
    page.locator(f'#project-menu [data-project="{name}"]').click()


def writes(page):
    requests = []
    page.on('request', lambda request: requests.append(request) if request.method not in ('GET', 'HEAD') else None)
    return requests


def assert_draft_closed(page, requests):
    expect(page.locator('#editor')).not_to_be_visible()
    assert requests == []
    assert page.evaluate('()=>editorChecks') == {'submit': 0, 'invalid': 0, 'refresh': 0, 'select': 0}


@pytest.mark.parametrize('operation', ['github-create', 'github-rename'])
@pytest.mark.parametrize('value', ['', 'unsubmitted'])
@pytest.mark.parametrize('dismiss', ['cancel', 'escape'])
def test_github_cancel_and_escape_close_empty_or_populated_required_fields(page, operation, value, dismiss):
    requests = writes(page)
    selected = page.evaluate('()=>state.project')
    menu_action(page, operation)
    field = page.locator('#editor-fields input[name=name]')
    assert field.get_attribute('required') is not None
    field.fill(value)
    if operation == 'github-create':
        page.locator('#editor-fields select[name=private]').select_option('false')
    if dismiss == 'cancel':
        page.locator('#editor-cancel').click()
    else:
        field.press('Escape')
    assert_draft_closed(page, requests)
    assert page.evaluate('()=>state.project') == selected
    # Reopening needs no reload and gets fresh values, not the abandoned draft.
    menu_action(page, operation)
    expect(field).to_have_value('' if operation == 'github-create' else 'alpha')
    if operation == 'github-create':
        expect(page.locator('select[name=private]')).to_have_value('true')
    page.locator('#editor-cancel').click()
    assert_draft_closed(page, requests)


@pytest.mark.parametrize('operation', ['display-name', 'add-project', 'project-settings', 'todo'])
@pytest.mark.parametrize('value', ['', 'Abandoned draft'])
def test_other_shared_editors_cancel_without_writes_validation_or_refresh(page, operation, value):
    requests = writes(page)
    if operation == 'display-name':
        menu_action(page, 'rename')
    elif operation == 'todo':
        page.locator('[data-view=todo]').click()
        page.locator('#todo-add').click()
    else:
        page.locator('[data-view=settings]').click()
        page.locator('#' + operation).click()
    page.locator('#editor-fields input').first.fill(value)
    page.locator('#editor-cancel').click()
    assert_draft_closed(page, requests)


@pytest.mark.parametrize('private', [True, False])
def test_create_keeps_private_default_and_public_choice_and_submits_selected_visibility(page, browser_app, private):
    requests = writes(page)
    name = 'created-' + uuid.uuid4().hex[:8]
    menu_action(page, 'github-create')
    visibility = page.locator('#editor-fields select[name=private]')
    expect(visibility).to_have_value('true')
    assert visibility.locator('option').all_inner_texts() == ['Private (default)', 'Public']
    page.locator('#editor-fields input[name=name]').fill(name)
    if not private:
        visibility.select_option('false')
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    assert [(r.method, r.post_data_json) for r in requests] == [('POST', {'name': name, 'private': private})]
    assert page.evaluate('()=>state.portfolio.projects.find(p=>p.id===state.project).name') == name
    assert (browser_app[2] / 'projects' / name / '.git').is_dir()


def test_shared_editor_display_name_save_and_todo_save_still_work(page):
    requests = writes(page)
    menu_action(page, 'rename')
    page.locator('input[name=display_name]').fill('Project label')
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    project = page.evaluate('()=>state.portfolio.projects.find(p=>p.id===state.project)')
    assert project['display_name'] == 'Project label' and project['name'] == 'alpha'
    assert len(requests) == 1 and requests[0].method == 'PATCH'
    assert requests[0].post_data_json['display_name'] == 'Project label'
    assert page.evaluate('()=>editorChecks.refresh>0&&editorChecks.select===1')
    before = page.evaluate('()=>({...editorChecks})')
    page.locator('[data-view=todo]').click()
    page.locator('#todo-add').click()
    page.locator('input[name=title]').fill('Saved idea')
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#todo-list')).to_contain_text('Saved idea')
    after = page.evaluate('()=>editorChecks')
    assert (after['refresh'], after['select']) == (before['refresh'], before['select'])
    assert len(requests) == 2 and requests[1].post_data_json['title'] == 'Saved idea'


def test_successful_write_closes_even_if_the_following_refresh_fails(page, browser_app):
    requests = writes(page)
    name = 'refresh-' + uuid.uuid4().hex[:8]
    menu_action(page, 'github-create')
    page.locator('input[name=name]').fill(name)
    page.route('**/api/portfolio?*', lambda route: route.fulfill(status=503, json={'detail': 'Refresh unavailable'}))
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#notice')).to_contain_text('Saved, but project refresh failed: Refresh unavailable')
    assert len(requests) == 1 and requests[0].post_data_json == {'name': name, 'private': True}
    assert (browser_app[2] / 'projects' / name / '.git').is_dir()


@pytest.mark.parametrize('operation', ['github-create', 'github-rename'])
@pytest.mark.parametrize('outcome', ['success', 'failure'])
def test_dispatched_save_cannot_be_cancelled_and_failure_restores_cancel(page, operation, outcome):
    # Use a new project for rename so every test leaves the shared alpha fixture intact.
    name = 'flight-' + uuid.uuid4().hex[:8]
    if operation == 'github-rename':
        project = page.evaluate("async name=>(await api('/api/github/projects',{method:'POST',body:JSON.stringify({name})})).project", name)
        page.evaluate('id=>selectProject(id)', project['id'])
    requests = writes(page)
    held = []
    pattern = '**/api/github/projects' + (f'/{project["id"]}/rename' if operation == 'github-rename' else '')
    page.route(pattern, lambda route: held.append(route))
    menu_action(page, operation)
    page.locator('input[name=name]').fill(name + '-saved')
    page.locator('#editor .primary').click()
    deadline = time.monotonic() + 5
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(10)
    assert len(held) == 1
    # For success, the server has already performed the mutation while its response is held back.
    response = held[0].fetch(timeout=10000) if outcome == 'success' else None
    expect(page.locator('#editor-cancel')).to_be_disabled()
    expect(page.locator('#editor .primary')).to_be_disabled()
    page.locator('input[name=name]').press('Escape')
    page.locator('#editor-cancel').dispatch_event('click')
    page.evaluate("()=>{$('editor-form').requestSubmit();editor('Other editor',[],()=>{})}")
    expect(page.locator('#editor')).to_be_visible()
    expect(page.locator('#editor-title')).to_have_text('Create project' if operation == 'github-create' else 'Rename project')
    assert len(requests) == 1
    if operation == 'github-rename':
        assert requests[0].post_data_json == {'name': name + '-saved'}
    if outcome == 'success':
        assert response.status == 200
        held[0].fulfill(response=response)
        expect(page.locator('#editor')).not_to_be_visible()
        assert page.evaluate('()=>state.portfolio.projects.find(p=>p.id===state.project).name') == name + '-saved'
    else:
        held[0].fulfill(status=409, json={'detail': 'Save refused; nothing changed'})
        expect(page.locator('#editor-error')).to_have_text('Save refused; nothing changed')
        expect(page.locator('#editor-cancel')).to_be_enabled()
        expect(page.locator('#editor .primary')).to_be_enabled()
        page.locator('#editor-cancel').click()
        expect(page.locator('#editor')).not_to_be_visible()
    assert len(requests) == 1


@pytest.mark.parametrize('project_selected', [True, False])
def test_github_settings_menu_shortcut_focuses_the_existing_token_form(page, project_selected):
    if not project_selected:
        page.evaluate('()=>selectProject(null)')
    requests = writes(page)
    menu_action(page, 'github-settings')
    expect(page.locator('#project-menu')).not_to_be_visible()
    expect(page.locator('#view-settings')).to_be_visible()
    expect(page.locator('#github-panel')).to_be_visible()
    expect(page.locator('#github-token')).to_be_focused()
    assert page.locator('#github-token-form').count() == 1
    assert page.locator('input[type=password]').filter(visible=True).count() == 1
    page.locator('[data-view=chat]').click()
    page.locator('[data-view=settings]').click()
    expect(page.locator('#github-token-form')).to_be_visible()
    assert requests == []

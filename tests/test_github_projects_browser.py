"""Chromium project-menu and shared-editor regressions against a disposable app with a mocked GitHub transport."""
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
from test_github_projects import FakeGitHub, PAT, COMPANY_PAT, existing_project
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
github = FakeGitHub()
app = create_app(s, FakeServer(s), github_fetch=github)
app.state.github_projects.pipeline.backups.tick = lambda: None
app.state.github_projects.store.write(PAT)
app.state.github_projects.stores['company'].write(COMPANY_PAT)
app.state.github_projects.save_company_org('acme')
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
        page.locator('#editor-fields select[name=tier]').select_option('company')
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
        expect(page.locator('select[name=tier]')).to_have_value('personal')
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


@pytest.mark.parametrize('tier', ['personal', 'company'])
def test_create_fixed_destinations_and_public_confirmation(page, browser_app, tier):
    requests = writes(page)
    name = 'created-' + uuid.uuid4().hex[:8]
    menu_action(page, 'github-create')
    destination = page.locator('#editor-fields select[name=tier]')
    expect(destination).to_have_value('personal')
    assert destination.locator('option').all_inner_texts() == ['Personal · private', 'Company · acme · public']
    confirmation = page.locator('input[name=public_confirmed]')
    expect(confirmation).not_to_be_visible()
    page.locator('#editor-fields input[name=name]').fill(name)
    if tier == 'company':
        destination.select_option('company')
        expect(confirmation).to_be_visible()
        expect(confirmation.locator('..')).to_contain_text('will be public')
        page.locator('#editor .primary').click()
        expect(page.locator('#editor')).to_be_visible()
        assert requests == []  # Native required validation refuses unchecked public creation.
        confirmation.check()
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    assert [(r.method, r.post_data_json) for r in requests] == [
        ('POST', {'name': name, 'tier': tier, 'public_confirmed': tier == 'company'})]
    assert page.evaluate('()=>state.portfolio.projects.find(p=>p.id===state.project).name') == name
    assert (browser_app[2] / 'projects' / name / '.git').is_dir()
    page.locator('#project-menu-button').click()
    expect(page.locator('#project-menu .github-tier')).to_have_text(tier.title())
    expect(page.locator('#project-menu')).to_contain_text(('octo' if tier == 'personal' else 'acme') + '/' + name)
    assert page.locator('#projects .github-tier').count() == 0
    page.locator('#project-menu-button').click()
    page.locator('[data-view=settings]').click()
    expect(page.locator('#github-project-repository .github-tier')).to_have_text(tier.title())


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


@pytest.mark.parametrize('remote', [
    'git@github.com:octo/alpha.git',
    'ssh://git@github.com/octo/alpha.git',
    'https://github.com/octo/alpha.git',
])
def test_project_name_editor_shows_both_copyable_github_forms(page, remote):
    page.evaluate("remote=>{const p=state.portfolio.projects.find(p=>p.id===state.project);p.repository=remote;renameProject()}", remote)
    rows = page.locator('.project-repository-row')
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text('SSH')
    expect(rows.nth(0)).to_contain_text('git@github.com:octo/alpha.git')
    expect(rows.nth(1)).to_contain_text('HTTPS')
    expect(rows.nth(1)).to_contain_text('https://github.com/octo/alpha.git')
    copied = page.evaluate("""async()=>{
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
      let value='';document.execCommand=command=>{value=document.activeElement.value;return command==='copy'};
      await document.querySelector('.project-repository-copy').onclick();
      return {value,label:document.querySelector('.project-repository-copy').textContent};
    }""")
    assert copied == {'value': 'git@github.com:octo/alpha.git', 'label': 'Copied'}


def test_project_name_editor_shows_one_copyable_non_github_remote(page):
    remote = '/srv/git/alpha'
    page.evaluate("remote=>{const p=state.portfolio.projects.find(p=>p.id===state.project);p.repository=remote;renameProject()}", remote)
    rows = page.locator('.project-repository-row')
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(remote)
    expect(rows.first.locator('button')).to_have_attribute('aria-label', 'Copy Repository repository link')


def test_successful_write_closes_even_if_the_following_refresh_fails(page, browser_app):
    requests = writes(page)
    name = 'refresh-' + uuid.uuid4().hex[:8]
    menu_action(page, 'github-create')
    page.locator('input[name=name]').fill(name)
    page.route('**/api/portfolio?*', lambda route: route.fulfill(status=503, json={'detail': 'Refresh unavailable'}))
    page.locator('#editor .primary').click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#notice')).to_contain_text('Saved, but project refresh failed: Refresh unavailable')
    assert len(requests) == 1 and requests[0].post_data_json == {'name': name, 'tier': 'personal', 'public_confirmed': False}
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


@pytest.mark.parametrize('width', [1200, 390])
@pytest.mark.parametrize('project_selected', [True, False])
def test_settings_menu_entry_opens_the_existing_github_token_forms(page, browser_app, project_selected, width):
    page.set_viewport_size({'width': width, 'height': 900})
    if not project_selected:
        page.evaluate('()=>selectProject(null)')
    requests = writes(page)
    menu_action(page, 'github-settings')
    expect(page.locator('#project-menu')).not_to_be_visible()
    expect(page.locator('#view-settings')).to_be_visible()
    expect(page.locator('#github-panel')).to_be_visible()
    expect(page.locator('#github-token')).to_be_focused()
    assert page.locator('#github-token-form').count() == 1
    expect(page.locator('#github-company-token')).to_be_visible()
    expect(page.locator('#github-company-org')).to_have_value('acme')
    expect(page.locator('#github-panel h2')).to_have_text('GitHub Settings')
    for selector in ['#github-panel', '#github-token', '#github-company-token']:
        assert page.locator(selector).evaluate('e=>getComputedStyle(e).display') != 'none'
    assert page.locator('input[type=password]').filter(visible=True).count() == 2
    assert page.locator('#projects [data-project=github-settings], #projects input[type=password]').count() == 0
    page.screenshot(path=str(browser_app[2] / f'github-settings-{width}.png'), full_page=True)
    page.locator('[data-view=chat]').click()
    page.locator('[data-view=settings]').click()
    expect(page.locator('#github-token-form')).to_be_visible()
    assert requests == []


def test_org_setting_is_saved_shown_after_reload_and_used_for_company(page):
    menu_action(page, 'github-settings')
    field = page.locator('#github-company-org')
    expect(field).to_have_value('acme')
    field.fill('Example-Co')
    page.locator('#github-org-form button').click()
    expect(page.locator('#github-org-status')).to_have_text('Company organization saved.')
    page.reload()
    page.wait_for_function('()=>state.ready')
    page.locator('[data-view=settings]').click()
    expect(field).to_have_value('Example-Co')
    menu_action(page, 'github-create')
    expect(page.locator('select[name=tier] option[value=company]')).to_have_text('Company · Example-Co · public')
    page.locator('#editor-cancel').click()
    page.evaluate("()=>api('/api/github/organization',{method:'PUT',body:JSON.stringify({company_org:'acme'})})")


def test_company_disabled_without_saved_org_and_hint_points_to_settings(page):
    page.evaluate("()=>api('/api/github/organization',{method:'PUT',body:JSON.stringify({company_org:''})})")
    menu_action(page, 'github-create')
    expect(page.locator('select[name=tier] option[value=company]')).to_have_attribute('disabled', '')
    expect(page.locator('#editor-fields')).to_contain_text('GitHub settings')
    expect(page.locator('select[name=tier]')).to_have_value('personal')
    page.locator('#editor-cancel').click()
    page.evaluate("()=>api('/api/github/organization',{method:'PUT',body:JSON.stringify({company_org:'acme'})})")


@pytest.mark.parametrize('width,height', [(1200, 800), (390, 800), (320, 420), (590, 320)])
def test_project_menu_scrolls_with_a_sticky_close_header_and_settings_at_the_end(page, browser_app, width, height):
    page.set_viewport_size({'width': width, 'height': height})
    expect(page.locator('#github-panel')).not_to_be_visible()
    page.evaluate("""()=>{state.portfolio.projects.push(...Array.from({length:30},(_,i)=>({
      ...state.portfolio.projects[0],id:'menu-'+i,name:'000-'+i,display_name:null,visible:true
    })));}""")
    page.locator('#project-menu-button').click()
    entry = page.locator('[data-project=github-settings]')
    expect(entry).to_have_text('Settings')
    menu = page.locator('#project-menu')
    assert menu.evaluate('e=>e.scrollHeight>e.clientHeight')
    bounds = menu.bounding_box()
    assert bounds['x'] >= 0 and bounds['y'] >= 0
    assert bounds['x'] + bounds['width'] <= width
    assert bounds['y'] + bounds['height'] <= height
    if width <= 600:
        assert bounds == {'x': 0, 'y': 0, 'width': width, 'height': height}
    page.screenshot(path=str(browser_app[2] / f'github-menu-{width}-top.png'))
    entry.scroll_into_view_if_needed()
    expect(entry).to_be_in_viewport()
    expect(page.locator('[data-project=github-create]')).to_be_in_viewport()
    close = page.get_by_role('button', name='Close project menu')
    expect(close).to_be_in_viewport()
    page.screenshot(path=str(browser_app[2] / f'github-menu-{width}-bottom.png'))
    close.click()
    expect(menu).not_to_be_visible()
    expect(page.locator('#project-menu-button')).to_be_focused()
    page.locator('#project-menu-button').click()
    assert menu.evaluate('e=>e.scrollTop') == 0
    entry.click()
    for selector in ['#github-panel', '#github-token', '#github-company-token']:
        expect(page.locator(selector)).to_be_visible()
        assert page.locator(selector).evaluate('e=>getComputedStyle(e).display') != 'none'
    assert not page.evaluate("()=>document.body.classList.contains('restricted')")


@pytest.mark.parametrize('tier,form_id,input_id,status_id', [
    ('personal', 'github-token-form', 'github-token', 'github-status'),
    ('company', 'github-company-token-form', 'github-company-token', 'github-company-status'),
])
def test_each_token_form_saves_validates_replaces_and_removes_without_echo(page, tier, form_id, input_id, status_id):
    menu_action(page, 'github-settings')
    form, field, status = page.locator('#' + form_id), page.locator('#' + input_id), page.locator('#' + status_id)
    assert field.get_attribute('type') == 'password'
    assert field.get_attribute('autocomplete') == 'off'
    response_texts = []
    page.on('response', lambda response: response_texts.append(response.text()) if '/api/github' in response.url else None)
    form.locator('[data-token-action=remove]').click()
    expect(status).to_contain_text('Validation: missing')
    expect(form.locator('[data-token-action=save]')).to_be_enabled()
    for operation in ['validate', 'replace', 'remove']:
        expect(form.locator(f'[data-token-action={operation}]')).to_be_disabled()
    first, second = secrets.token_hex(20), secrets.token_hex(20)
    for operation, value in [('save', first), ('replace', second)]:
        field.fill(value)
        form.locator(f'[data-token-action={operation}]').click()
        expect(status).to_contain_text('Connected as octo. Validation: valid')
        expect(field).to_have_value('')
        expect(form.locator('[data-token-action=save]')).to_be_disabled()
        expect(form.locator('[data-token-action=replace]')).to_be_enabled()
    form.locator('[data-token-action=validate]').click()
    expect(status).to_contain_text('Validation: valid')
    expect(form.locator('[data-token-action=validate]')).to_be_enabled()
    page.reload()
    page.wait_for_function('()=>state.ready')
    menu_action(page, 'github-settings')
    expect(status).to_contain_text('Connected as octo. Validation: valid')
    expect(field).to_have_value('')
    stored = page.evaluate('()=>JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])')
    assert first not in stored and second not in stored
    assert all(first not in text and second not in text for text in response_texts)
    form.locator('[data-token-action=remove]').click()
    expect(status).to_contain_text('Validation: missing')
    # Restore a disposable fixture credential for other workflow tests.
    field.fill(secrets.token_hex(20))
    form.locator('[data-token-action=save]').click()
    expect(status).to_contain_text('Validation: valid')


@pytest.mark.parametrize('missing', ['company', 'personal', 'both'])
def test_create_options_require_their_own_tokens_and_link_to_settings(page, missing):
    tiers = ['personal', 'company'] if missing == 'both' else [missing]
    for tier in tiers:
        page.evaluate("tier=>api(`/api/github/tokens/${tier}/remove`,{method:'POST'})", tier)
    page.evaluate('()=>loadGithubStatus()')
    if 'personal' in tiers:
        page.locator('#project-menu-button').click()
        assert page.locator('[data-project=github-rename]').count() == 0
        expect(page.locator('#project-menu')).not_to_contain_text('open GitHub settings')
        page.locator('#project-menu-button').click()
    menu_action(page, 'github-create')
    for tier in tiers:
        expect(page.locator(f'select[name=tier] option[value={tier}]')).to_have_attribute('disabled', '')
    if missing == 'both':
        expect(page.locator('#editor .primary')).to_be_disabled()
    page.get_by_role('button', name='Open GitHub settings', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#github-panel')).to_be_visible()
    for tier in tiers:
        page.evaluate("args=>api(`/api/github/tokens/${args.tier}`,{method:'PUT',body:JSON.stringify({token:args.value})})", {'tier': tier, 'value': secrets.token_hex(20)})


@pytest.mark.parametrize('width', [1200, 390])
def test_project_menu_labels_groups_repository_and_global_order(page, browser_app, width):
    page.set_viewport_size({'width': width, 'height': 800})
    page.evaluate("""()=>{
      state.portfolio.global_last_used_at=Number.MAX_SAFE_INTEGER;
      for(const project of state.portfolio.projects)project.last_used_at=0;
      state.portfolio.projects.push(...[
        {id:'menu-z',name:'Zulu',visible:true,last_used_at:100},
        {id:'menu-a',name:'Aardvark',visible:true,last_used_at:200},
        {id:'menu-stashed',name:'Stashed',visible:false,last_used_at:300}
      ]);
      renderProjects();
    }""")
    tabs = page.locator('#projects button').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.project)')
    assert tabs[-1] == '' and tabs[:2] == ['menu-a', 'menu-z']
    assert 'menu-stashed' not in tabs
    page.screenshot(path=str(browser_app[2] / f'project-overview-{width}.png'))
    page.locator('#project-menu-button').click()
    menu = page.locator('#project-menu')
    assert all('…' not in label and '...' not in label for label in menu.locator('button, summary').all_inner_texts())
    actions = menu.get_by_role('group', name='Project actions', exact=True)
    assert actions.locator('button').all_inner_texts() == ['Change display name', 'Rename project', 'Create project']
    assert actions.locator('[data-project=rename] + p').inner_text() == page.evaluate(
        '()=>state.portfolio.projects.find(p=>p.id===state.project).repository')
    assert menu.get_by_role('button', name='GitHub settings', exact=True).count() == 0
    settings = menu.get_by_role('group', name='Settings', exact=True)
    assert settings.locator('button').all_inner_texts() == ['Settings']
    assert actions.evaluate('e=>e.nextElementSibling.getAttribute("aria-label")') == 'Settings'
    projects = menu.get_by_role('group', name='Projects and stashed projects')
    ids = projects.locator('button').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.project)')
    assert ids[0] == 'menu-a' and ids[-2:] == ['menu-z', '']
    assert projects.locator(':scope > :last-child').get_attribute('data-project') == ''
    assert projects.locator('[data-project=""] input').count() == 0
    assert projects.locator('input').count() == len(tabs)  # includes stashed; excludes global
    expect(menu.locator('[data-project=github-rename]')).to_be_enabled()
    menu.locator('summary').click()
    expect(menu.locator('[data-visibility=menu-stashed]')).not_to_be_checked()
    assert menu.locator('input').first.evaluate('e=>e.getBoundingClientRect().width') == 16
    page.screenshot(path=str(browser_app[2] / f'project-menu-{width}.png'))
    # Keyboard navigation and focus survive a background render, including the fixed header.
    menu.locator('[data-project=github-settings]').focus()
    page.keyboard.press('Tab')
    expect(page.locator('#project-menu-close')).to_be_focused()
    page.evaluate('()=>renderProjectMenu()')
    expect(page.locator('#project-menu-close')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(menu.locator('[data-project=github-settings]')).to_be_focused()
    page.keyboard.press('Escape')
    expect(menu).not_to_be_visible()
    expect(page.locator('#project-menu-button')).to_be_focused()


@pytest.mark.parametrize('width', [1200, 390])
def test_menu_stash_restore_keeps_global_available_and_last(page, width):
    page.set_viewport_size({'width': width, 'height': 800})
    project = page.evaluate('()=>state.project')
    page.locator('#message-input').fill('Retained draft')
    page.locator('#project-menu-button').click()
    page.locator(f'[data-visibility="{project}"]').uncheck()
    page.wait_for_function('()=>state.project===null&&state.history?.conversation.project_id===null')
    expect(page.locator('#projects button').last).to_have_attribute('id', 'all-projects')
    expect(page.locator('#project-menu')).to_be_visible()
    summary = page.locator('#project-menu summary')
    if not summary.evaluate('e=>e.parentElement.open'):
        summary.click()
    page.locator(f'[data-visibility="{project}"]').check()
    page.wait_for_function('(id)=>state.portfolio.projects.find(p=>p.id===id).visible', arg=project)
    assert page.evaluate('()=>state.project') is None
    page.locator(f'#project-menu [data-project="{project}"]').click()
    expect(page.locator('#message-input')).to_have_value('Retained draft')
    expect(page.locator('#projects button').last).to_have_attribute('id', 'all-projects')

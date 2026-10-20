"""Real Chromium: the model picker as the owner sees it. Disposable server and data; the provider is a protocol fixture.

Provider cards, pool toggles, persistence and exact manual choices at desktop and mobile widths."""
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

from ai_manager.db import Database

PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from test_model_settings import CatalogServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
server = CatalogServer(s)
for model, efforts in (('gpt-5.6-sol', ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')), ('gpt-5.6-terra', ('low', 'xhigh', 'ultra'))):
    server.catalog.append({'model': model, 'displayName': model, 'isDefault': False, 'defaultReasoningEffort': 'low',
                           'supportedReasoningEfforts': [{'reasoningEffort': e} for e in efforts]})
uvicorn.run(create_app(s, server, CatalogServer), host='127.0.0.1', port=PORT, log_level='error')
'''
ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ['Luna', 'Sol', 'Astra', 'gpt-5.6-terra', 'Low', 'Medium', 'High', 'Haiku', 'Sonnet', 'Opus', 'Low', 'Medium', 'High']
LUNA, SOL, SONNET = 'gpt-6-luna', 'gpt-6.1-sol', 'claude-sonnet-5-5'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp('model-picker')
    (root / 'projects').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'),
           'AI_MANAGER_SECURE_COOKIE': '0', 'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM.replace('PORT', str(port))], env=env, cwd=ROOT)
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
        yield url, root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope='module')
def browser_context(server):
    url, root = server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 393, 'height': 851})
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(url)
        page.locator('#username').fill('owner')
        page.locator('#password').fill('disposable-browser-password')
        page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
        page.locator('#login-button').click()
        page.wait_for_function('()=>state.ready&&!!state.history')
        page.close()
        yield context, url, root
        browser.close()


@pytest.fixture
def page(browser_context):
    context, url, _ = browser_context
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_function('()=>state.ready&&!!state.history')
    yield page
    assert errors == []
    page.close()


def open_panel(page):
    page.locator('#model-settings-button').click()
    page.wait_for_function('()=>modelDialog.open&&!modelOpening&&!modelSaving')


def choose(page, key):
    page.locator(f'[data-choice="{key}"]').click()
    page.wait_for_function('()=>!modelSaving')
    assert page.locator('#model-error').inner_text() == ''


def pressed(page, container):
    return page.locator(f'{container} button[aria-pressed=true]:not(.provider-logo)').all_inner_texts()


def saved(page):
    return page.evaluate("()=>api('/api/model-settings')")['settings']


@pytest.mark.parametrize('width,height,dialog_width', [(1200, 900, 420), (390, 851, 358), (320, 640, 304)])
def test_menu_has_the_known_good_layout_at_desktop_and_narrow_widths(page, width, height, dialog_width):
    page.set_viewport_size({'width': width, 'height': height})
    open_panel(page)
    box = page.locator('#model-dialog').bounding_box()
    assert box['width'] == dialog_width and box['height'] <= height - 16, box
    assert page.evaluate("()=>modelDialog.scrollWidth<=modelDialog.clientWidth")
    heads = page.locator('.model-section-head h2:visible, .model-favorites h2:visible').all_inner_texts()
    assert heads == ['Manager', 'Worker', 'Favorites']
    for section in ('#manager-choices', '#worker-choices'):  # a model row and an effort row per provider, Low/Medium/High
        assert page.locator(f'{section} .model-choice-row button').all_inner_texts() == FAMILIES
        assert page.locator(f'{section} .model-choice-row').count() == 4
        columns = page.locator(f'{section} .model-choice-row').first.evaluate("n=>getComputedStyle(n).gridTemplateColumns.split(' ').length")
        assert columns == 3
    for section in ('#manager-choices', '#worker-choices'):
        cards = page.locator(f'{section} .provider-card')
        assert cards.count() == 2
        assert cards.nth(0).evaluate('n=>getComputedStyle(n).backgroundColor') != cards.nth(1).evaluate('n=>getComputedStyle(n).backgroundColor')
        for card in cards.all():
            logo = card.locator('.provider-logo')
            assert logo.locator('svg').count() == 1
            bounds, first, last = (loc.bounding_box() for loc in (logo, card.locator('.model-choice-row').first, card.locator('.model-choice-row').last))
            assert bounds['width'] == 38 and bounds['y'] == first['y']
            assert bounds['y'] + bounds['height'] == last['y'] + last['height']
            assert bounds['x'] > first['x'] + first['width']
            assert logo.is_disabled()  # label only in manager/manual
    assert page.locator('.favorite-row').count() == 2
    # The Manual | Automatic switch: two equal buttons in the Worker heading row, right-aligned with the close button.
    switch = page.locator('#worker-mode')
    assert switch.locator('button').all_inner_texts() == ['Manual', 'Automatic']
    sw, close, head = switch.bounding_box(), page.locator('#model-close').bounding_box(), page.locator('#worker-heading').bounding_box()
    assert abs((sw['x'] + sw['width']) - (close['x'] + close['width'])) <= 1
    assert sw['width'] == (240 if width > 320 else sw['width']) and sw['width'] <= 240
    assert abs((sw['y'] + sw['height'] / 2) - (head['y'] + head['height'] / 2)) <= 3  # same line as the heading
    left, right = (switch.locator('button').nth(i).bounding_box() for i in (0, 1))
    assert abs(left['width'] - right['width']) <= 1 and left['height'] == right['height'] == 38
    # Same look as every other segment: neutral fill, accent text only on the pressed one, no gradient.
    style = switch.locator('button').first.evaluate("n=>{const s=getComputedStyle(n);return [s.backgroundColor,s.fontWeight,s.boxShadow]}")
    assert style == ['rgb(32, 39, 52)', '400', 'none']
    page.locator('#model-close').click()


def test_manager_and_worker_are_independent_and_automatic_persists(page, browser_context):
    _, _, root = browser_context
    open_panel(page)
    choose(page, 'manager-Sonnet')
    choose(page, 'manager-claude-high')
    choose(page, 'worker-Luna')
    choose(page, 'worker-codex-high')
    assert saved(page)['manager'] == {'model': SONNET, 'effort': 'high'}
    assert saved(page)['worker'] == {'model': LUNA, 'effort': 'high'}  # a Claude manager with a Codex worker
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High'] and pressed(page, '#worker-choices') == ['Luna', 'High']
    assert pressed(page, '#worker-mode') == ['Manual']
    choose(page, 'worker-automatic')
    auto = saved(page)['worker']
    assert auto['mode'] == 'automatic' and auto['provider'] == 'codex'
    assert saved(page)['manual'] == {'model': LUNA, 'effort': 'high'}
    assert pressed(page, '#worker-mode') == ['Automatic']
    assert page.locator('[data-choice="worker-codex-provider"]').get_attribute('aria-pressed') == 'true'
    # Max is supported only by an older catalog model; its independent toggle remains visible even unlit.
    assert page.locator('[data-choice="worker-codex-max"]').get_attribute('aria-pressed') == 'false'
    assert page.locator('[data-choice="worker-codex-max"]').is_enabled()
    choose(page, 'worker-Astra')
    choose(page, 'worker-codex-medium')
    auto = saved(page)['worker']
    assert 'gpt-6-astra' not in auto['allowed_models'] and 'medium' not in auto['allowed_efforts']
    assert pressed(page, '#worker-choices') == ['Luna', 'Sol', 'gpt-5.6-terra', 'Low', 'High', 'Xhigh', 'Ultra']
    assert page.locator('#worker-choices [data-provider=claude] .model-family:enabled').count() == 0
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High']
    page.locator('#model-close').click()
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    open_panel(page)
    assert saved(page)['worker'] == auto
    assert pressed(page, '#worker-mode') == ['Automatic']
    assert page.locator('[data-choice="worker-Astra"]').get_attribute('aria-pressed') == 'false'
    # The manager choice and favorites keep working while the worker is Automatic.
    choose(page, 'favorite-insert-0')
    choose(page, 'manager-Opus')
    choose(page, 'manager-claude-low')
    assert saved(page)['worker'] == auto
    page.locator('[data-choice="favorite-recall-0"]').click()
    page.wait_for_function('()=>!modelSaving')
    open_panel(page)
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High'] and saved(page)['worker'] == auto
    assert page.locator('[data-choice="favorite-recall-0"]').get_attribute('aria-pressed') == 'true'
    # Fast Send is unaffected: the message goes out at once on the manager's pair, with no worker involved.
    page.locator('#model-close').click()
    started = time.monotonic()
    requests = []
    page.on('request', lambda request: requests.append(time.monotonic()) if request.url.endswith('/api/chat') else None)
    page.locator('#message-input').fill('Send while the worker is Automatic')
    page.locator('#send').click()
    page.wait_for_function('()=>!state.pending.size', timeout=5000)
    assert requests and requests[0] - started < 1.5
    page.wait_for_function('()=>!state.execution')
    with sqlite3.connect(root / 'data/manager.sqlite3') as connection:
        route = connection.execute("SELECT model,effort FROM executions WHERE task_id IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    assert route == (SONNET, 'high')
    # Manual restores the fixed worker; pool model buttons remain toggles while Automatic is selected.
    open_panel(page)
    choose(page, 'worker-manual')
    assert saved(page)['worker'] == {'model': LUNA, 'effort': 'high'} and pressed(page, '#worker-mode') == ['Manual']
    choose(page, 'worker-automatic')
    choose(page, 'worker-Astra')
    assert pressed(page, '#worker-mode') == ['Automatic']
    assert 'gpt-6-astra' not in saved(page)['worker']['allowed_models']
    choose(page, 'worker-claude-provider')
    pool = saved(page)['worker']
    assert pool['provider'] == 'claude' and all(m.startswith('claude-') for m in pool['allowed_models'])
    assert page.locator('[data-choice="worker-codex-provider"]').get_attribute('aria-pressed') == 'false'
    choose(page, 'worker-Opus')
    choose(page, 'worker-Sonnet')
    choose(page, 'worker-claude-low')
    choose(page, 'worker-claude-medium')
    assert page.locator('[data-choice="worker-Haiku"]').is_disabled()
    assert page.locator('[data-choice="worker-claude-high"]').is_disabled()
    assert saved(page)['worker']['allowed_efforts'] == ['high']
    assert saved(page)['manager'] == {'model': SONNET, 'effort': 'high'}
    page.locator('#model-close').click()
    # Restore the fixed worker for independent subsequent tests.
    open_panel(page)
    choose(page, 'worker-manual')
    page.locator('#model-close').click()


def test_picker_stays_compact_with_the_real_catalog_shape(page):
    """Manual stays compact: newest known families plus new catalog families, with three basic effort buttons."""
    open_panel(page)
    for section in ('#manager-choices', '#worker-choices'):
        assert page.locator(f'{section} button').count() == 15
    assert page.locator('#model-dialog button[data-choice]').count() == 15 * 2 + 2 + 2 * 2 + 1  # repo lock
    page.locator('#model-close').click()


def test_repo_toggle_scopes_every_pick_and_discards_local_state(page, browser_context):
    _, _, root = browser_context
    projects = []
    for name in ('lock-one', 'lock-two'):
        path = root / 'projects' / name
        path.mkdir()
        projects.append(page.evaluate("path=>api('/api/projects',{method:'POST',body:JSON.stringify({path})})", str(path))['id'])
    one, two = projects
    original = saved(page)
    page.evaluate("choice=>api('/api/model-settings',{method:'PUT',body:JSON.stringify(choice)})",
                  {**original, 'manager': {'model': SOL, 'effort': 'low'}, 'worker': {'model': SOL, 'effort': 'medium'}})

    def select(project):
        page.evaluate('id=>selectProject(id)', project)
        open_panel(page)

    def panel(project):
        return page.evaluate("id=>api('/api/model-settings?project_id='+id)", project)

    select(one)
    toggle = page.locator('[data-choice="repo-lock"]')
    assert toggle.inner_text() == 'lock-one' and toggle.get_attribute('aria-pressed') == 'false'
    off_style = toggle.evaluate('n=>getComputedStyle(n).backgroundColor')
    choose(page, 'repo-lock')
    assert toggle.get_attribute('aria-pressed') == 'true'
    assert toggle.evaluate('n=>getComputedStyle(n).backgroundColor') != off_style
    assert panel(one)['settings'] == saved(page)
    choose(page, 'manager-Sonnet')
    choose(page, 'manager-claude-high')
    choose(page, 'worker-Luna')
    choose(page, 'worker-codex-high')
    choose(page, 'worker-automatic')
    assert panel(one)['settings']['worker']['mode'] == 'automatic'
    assert saved(page)['worker'] == {'model': SOL, 'effort': 'medium'}
    assert saved(page)['manager'] == {'model': SOL, 'effort': 'low'}
    page.locator('#model-close').click()
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    select(one)
    assert toggle.get_attribute('aria-pressed') == 'true'
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High']
    assert pressed(page, '#worker-mode') == ['Automatic']
    choose(page, 'worker-manual')
    assert pressed(page, '#worker-choices') == ['Luna', 'High']
    choose(page, 'favorite-insert-1')
    choose(page, 'manager-Sol')
    choose(page, 'favorite-recall-1')
    open_panel(page)
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High']
    assert saved(page)['manager']['model'] == SOL
    page.locator('#model-close').click()
    # A failed refresh must clear the previous repo's settings and lock button.
    page.evaluate('id=>selectProject(id)', two)
    failed_url = '**/api/model-settings?project_id=' + two
    page.route(failed_url, lambda route: route.fulfill(status=503, json={'detail': 'Refresh unavailable'}))
    open_panel(page)
    assert page.locator('#worker-choices button, #manager-choices button, #repo-lock-container button').count() == 0
    page.locator('#model-close').click()
    page.unroute(failed_url)
    open_panel(page)
    assert toggle.inner_text() == 'lock-two' and toggle.get_attribute('aria-pressed') == 'false'
    choose(page, 'manager-Opus')
    choose(page, 'manager-claude-low')
    choose(page, 'worker-Astra')
    choose(page, 'worker-codex-low')
    assert panel(one)['settings']['manager']['model'] == SONNET
    assert panel(two)['settings'] == saved(page)
    page.locator('#model-close').click()
    select(one)
    choose(page, 'repo-lock')
    assert toggle.get_attribute('aria-pressed') == 'false'
    assert pressed(page, '#manager-choices') == ['Opus', 'Low']
    assert pressed(page, '#worker-choices') == ['Astra', 'Low']
    choose(page, 'repo-lock')
    assert panel(one)['settings'] == saved(page)  # no previous local manager, worker or Manual is restored
    choose(page, 'repo-lock')
    page.locator('#model-close').click()
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    select(one)
    assert toggle.get_attribute('aria-pressed') == 'false'
    assert panel(one)['settings'] == saved(page)
    page.locator('#model-close').click()
    now = saved(page)
    page.evaluate("choice=>api('/api/model-settings',{method:'PUT',body:JSON.stringify(choice)})", {**original, 'revision': now['revision']})
    page.evaluate('()=>selectProject(null)')


def test_users_reuse_controls_persist_and_show_access_errors(page, browser_context, tmp_path):
    _, _, root = browser_context
    data = tmp_path / 'writer'
    user_db = Database(data / 'manager.sqlite3')
    manifest = root / 'data' / 'deploy' / 'installed.json'
    manifest.parent.mkdir(exist_ok=True)
    import json
    entries = [{'name': 'Writer <one>', 'data': str(data), 'url': 'http://127.0.0.1:1', 'service': 'offline.service'},
               {'name': 'Missing', 'data': str(tmp_path / 'missing'), 'url': 'http://127.0.0.1:2', 'service': 'missing.service'}]
    manifest.write_text(json.dumps({'instances': entries}))
    original = saved(page)
    try:
        open_panel(page)
        users = page.locator('#instance-models')
        assert users.is_visible()
        assert users.locator('h3').all_inner_texts() == ['Writer <one>', 'Missing']
        writer = users.locator('[data-instance="Writer <one>"]')
        assert writer.locator('.model-family').count() == 14
        assert 'cannot access' in users.locator('[data-instance="Missing"] [role=alert]').inner_text()
        choose(page, 'user-0-manager-Luna')
        choose(page, 'user-0-manager-codex-high')
        choose(page, 'user-0-worker-Sonnet')
        choose(page, 'user-0-worker-claude-high')
        expected = user_db.setting('model_choice')
        assert expected['manager'] == {'model': LUNA, 'effort': 'high'}
        assert expected['worker'] == {'model': SONNET, 'effort': 'high'}
        assert saved(page) == original
        choose(page, 'user-0-worker-automatic')
        assert user_db.setting('model_choice')['worker']['provider'] == 'claude'
        page.locator('#model-close').click()
        page.reload()
        page.wait_for_function('()=>state.ready&&!!state.history')
        open_panel(page)
        assert writer.locator('button[aria-pressed=true]:not(.provider-logo)').all_inner_texts() == ['Luna', 'High', 'Automatic', 'Haiku', 'Sonnet', 'Opus', 'Low', 'Medium', 'High']
        choose(page, 'user-0-worker-manual')
        assert user_db.setting('model_choice')['worker'] == expected['worker']
        page.screenshot(path=str(tmp_path / 'users-overview.png'))
        users.screenshot(path=str(tmp_path / 'users-controls.png'))
        # A real filesystem denial after opening the panel produces an inline error and retains the saved pair.
        before = user_db.setting('model_choice')
        data.joinpath('manager.sqlite3').chmod(0o400)
        page.locator('[data-choice="user-0-manager-Sol"]').click()
        page.wait_for_function('()=>!modelSaving')
        assert 'read/write access' in writer.locator('[role=alert]').inner_text()
        assert user_db.setting('model_choice') == before
        assert page.locator('#model-error').inner_text() == ''  # the main selection remains usable
        page.locator('#model-close').click()
        # A restricted page never renders user choices, even if a payload unexpectedly includes them.
        page.evaluate('()=>{state.restricted=true;renderModels();}')
        assert users.is_hidden() and users.locator('button').count() == 0
        page.evaluate('()=>{state.restricted=false;renderModels();}')
    finally:
        data.joinpath('manager.sqlite3').chmod(0o600)
        for path in data.glob('manager.sqlite3-*'):
            path.chmod(0o600)
        manifest.unlink()

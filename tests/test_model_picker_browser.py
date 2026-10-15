"""Real Chromium: the model picker as the owner sees it. Disposable server and data; the provider is a protocol fixture.

Layout numbers are the ones measured on the known-good renderings (208554a4cd for the sections, 81711bd for the Manual |
Automatic switch) with the same fixture catalog; docs/evidence/model-picker/ holds the screenshots they come from."""
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
FAMILIES = ['Luna', 'Sol', 'Astra', 'Low', 'Medium', 'High', 'Haiku', 'Sonnet', 'Opus', 'Low', 'Medium', 'High']
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
    return page.locator(f'{container} button[aria-pressed=true]').all_inner_texts()


def saved(page):
    return page.evaluate("()=>api('/api/model-settings')")['settings']


@pytest.mark.parametrize('width,height,dialog_width', [(1200, 900, 420), (393, 851, 361), (320, 640, 304)])
def test_menu_has_the_known_good_layout_at_desktop_and_narrow_widths(page, width, height, dialog_width):
    page.set_viewport_size({'width': width, 'height': height})
    open_panel(page)
    box = page.locator('#model-dialog').bounding_box()
    assert box['width'] == dialog_width and 566 <= box['height'] <= 574, box  # one screen, no scrolling at any width
    assert page.evaluate("()=>modelDialog.scrollHeight<=modelDialog.clientHeight")
    heads = page.locator('.model-section-head h2, .model-favorites h2').all_inner_texts()
    assert heads == ['Manager', 'Worker', 'Favorites']
    for section in ('#manager-choices', '#worker-choices'):  # a model row and an effort row per provider, Low/Medium/High
        assert page.locator(f'{section} button').all_inner_texts() == FAMILIES
        assert page.locator(f'{section} .model-choice-row').count() == 4
        columns = page.locator(f'{section} .model-choice-row').first.evaluate("n=>getComputedStyle(n).gridTemplateColumns.split(' ').length")
        assert columns == 3
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
    # Automatic: the worker is the manager's pick; nothing in the worker rows is pressed; the manager is untouched.
    choose(page, 'worker-automatic')
    assert saved(page)['worker'] is None and saved(page)['manual'] == {'model': LUNA, 'effort': 'high'}
    assert pressed(page, '#worker-mode') == ['Automatic'] and pressed(page, '#worker-choices') == []
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High']
    assert page.locator('#worker-choices .effort-choice:enabled').count() == 0  # efforts wait for a model, like any other
    page.locator('#model-close').click()
    page.reload()  # saved: it survives a reload
    page.wait_for_function('()=>state.ready&&!!state.history')
    open_panel(page)
    assert pressed(page, '#worker-mode') == ['Automatic'] and pressed(page, '#worker-choices') == []
    # The manager choice and favorites keep working while the worker is Automatic.
    choose(page, 'favorite-insert-0')
    choose(page, 'manager-Opus')
    choose(page, 'manager-claude-low')
    assert saved(page)['worker'] is None
    page.locator('[data-choice="favorite-recall-0"]').click()
    page.wait_for_function('()=>!modelSaving')
    open_panel(page)
    assert pressed(page, '#manager-choices') == ['Sonnet', 'High'] and saved(page)['worker'] is None
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
    # Automatic is switched off: the remembered fixed worker comes back; picking a model while Automatic is a manual pick.
    open_panel(page)
    choose(page, 'worker-manual')
    assert saved(page)['worker'] == {'model': LUNA, 'effort': 'high'} and pressed(page, '#worker-mode') == ['Manual']
    choose(page, 'worker-automatic')
    choose(page, 'worker-Astra')
    assert pressed(page, '#worker-mode') == ['Manual'] and pressed(page, '#worker-choices')[0] == 'Astra'
    assert saved(page)['worker']['model'] == 'gpt-6-astra'
    assert saved(page)['manager'] == {'model': SONNET, 'effort': 'high'}
    page.locator('#model-close').click()


def test_picker_stays_compact_with_the_real_catalog_shape(page):
    """Unrestricted dispatch must not widen the picker: older models and extra efforts in the catalog add no buttons."""
    open_panel(page)
    for section in ('#manager-choices', '#worker-choices'):
        assert page.locator(f'{section} button').count() == 12
    assert page.locator('#model-dialog button[data-choice]').count() == 12 * 2 + 2 + 2 * 2
    page.locator('#model-close').click()


def test_project_picker_persists_its_effective_worker_and_clear_follows_default(page, browser_context):
    _, _, root = browser_context
    projects = []
    for name in ('override-one', 'override-two'):
        path = root / 'projects' / name
        path.mkdir()
        projects.append(page.evaluate("path=>api('/api/projects',{method:'POST',body:JSON.stringify({path})})", str(path))['id'])
    one, two = projects
    original = saved(page)
    page.evaluate("choice=>api('/api/model-settings',{method:'PUT',body:JSON.stringify(choice)})",
                  {**original, 'worker': {'model': SOL, 'effort': 'medium'}})

    def select(project):
        page.evaluate('id=>selectProject(id)', project)
        open_panel(page)

    def panel(project):
        return page.evaluate("id=>api('/api/model-settings?project_id='+id)", project)

    select(one)
    assert pressed(page, '#worker-choices') == ['Sol', 'Medium', 'Following default worker']
    assert page.locator('[data-choice="worker-default"]').is_disabled()
    choose(page, 'worker-Luna')
    choose(page, 'worker-codex-high')
    assert panel(one)['worker_selection']['worker'] == {'model': LUNA, 'effort': 'high'}
    assert saved(page)['worker'] == {'model': SOL, 'effort': 'medium'}
    page.locator('#model-close').click()
    # An unavailable project refresh must never enable the previous project's cached selection.
    page.evaluate('id=>selectProject(id)', two)
    failed_url = '**/api/model-settings?project_id=' + two
    page.route(failed_url, lambda route: route.fulfill(status=503, json={'detail': 'Refresh unavailable'}))
    open_panel(page)
    assert page.locator('#worker-choices button').count() == 0
    assert page.locator('#worker-mode button').count() == 0
    assert panel(one)['worker_selection']['worker'] == {'model': LUNA, 'effort': 'high'}
    page.locator('#model-close').click()
    page.unroute(failed_url)
    open_panel(page)
    assert pressed(page, '#worker-choices') == ['Sol', 'Medium', 'Following default worker']
    assert panel(two)['project_worker']['override'] is None
    page.locator('#model-close').click()
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    select(one)
    assert pressed(page, '#worker-choices') == ['Luna', 'High']
    # Editing and recalling a manager favorite stays global and preserves this project's worker and scope.
    choose(page, 'manager-Sonnet')
    choose(page, 'favorite-insert-1')
    choose(page, 'manager-Sol')
    choose(page, 'favorite-recall-1')
    page.wait_for_function('()=>!modelSaving')
    open_panel(page)
    assert page.evaluate('()=>modelPanel.project_id') == one
    assert pressed(page, '#worker-choices') == ['Luna', 'High']
    assert saved(page)['manager']['model'] == SONNET
    # Manual/Automatic stays independent in each scope; clearing is a separate operation from Automatic.
    choose(page, 'worker-automatic')
    assert pressed(page, '#worker-mode') == ['Automatic'] and pressed(page, '#worker-choices') == []
    assert panel(one)['project_worker']['override']['worker'] is None
    choose(page, 'worker-manual')
    assert pressed(page, '#worker-choices') == ['Luna', 'High']
    choose(page, 'worker-default')
    assert pressed(page, '#worker-choices') == ['Sol', 'Medium', 'Following default worker']
    page.locator('#model-close').click()
    now = saved(page)
    page.evaluate("choice=>api('/api/model-settings',{method:'PUT',body:JSON.stringify(choice)})",
                  {**now, 'worker': {'model': 'gpt-6-astra', 'effort': 'low'}})
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    for project in (one, two):
        select(project)
        assert pressed(page, '#worker-choices') == ['Astra', 'Low', 'Following default worker']
        assert panel(project)['project_worker']['override'] is None
        page.locator('#model-close').click()
    # Leave the shared browser fixture in its original global context and settings.
    now = saved(page)
    page.evaluate("choice=>api('/api/model-settings',{method:'PUT',body:JSON.stringify(choice)})", {**original, 'revision': now['revision']})
    page.evaluate('()=>selectProject(null)')

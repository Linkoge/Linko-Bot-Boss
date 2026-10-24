"""Real Chromium geometry of the Projects menu: names keep useful width and status text wraps beneath."""
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LONG_NAME = 'An-exceptionally-long-project-name-' + 'segment-' * 8
# Long unbroken tokens are the worst case for wrapping; they are not credentials.
LONG_CAUSE = 'GitHub fetch failed after 2 attempts: ' + 'unclassifiedfailuredetail' * 8
PROGRAM = '''
import subprocess, sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
app = create_app(s, FakeServer(s))
app.state.github_projects.pipeline.backups.tick = lambda: None
app.state.github_projects.pipeline.sync.tick = lambda: None
db = app.state.registry.db
CASES = {
    'Bot Boss': ('public', {'state': 'busy', 'reason': 'Checkout is busy; update deferred.'}, {}),
    'Business': ('private', {'state': 'warning', 'reason': 'Uncommitted changes in a.py, b.py; update skipped, local files left untouched.'}, {}),
    'FitBean': ('private', {'state': 'warning', 'reason': %(cause)r}, {}),
    'Plain': (None, {}, {}),
    %(long)r: ('public', {'state': 'diverged', 'reason': 'Out of sync with GitHub, needs attention: both main branches have moved.'},
               {'state': 'needs-attention', 'message': 'GitHub denied access: authentication failed.', 'unverified': True,
                'commit': 'abcdef1234567', 'warnings': [{'type': 'GitHub token', 'path': 'src/very/long/path/file.py', 'line': 3, 'commit': 'abc'}]}),
}
for name, (visibility, sync, backup) in CASES.items():
    folder = s.project_roots[0] / name
    folder.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(folder)], check=True)
    project = app.state.registry.add(folder)
    if visibility:
        app.state.registry.cache_repo_visibility(project['id'], visibility)
    if sync:
        db.set_setting('github_sync:' + project['id'], sync)
    if backup:
        db.set_setting('github_backup:' + project['id'], backup)
uvicorn.run(app, host='127.0.0.1', port=s.port, log_level='error')
''' % {'cause': LONG_CAUSE, 'long': LONG_NAME}


@pytest.fixture(scope='module')
def browser_app(tmp_path_factory):
    root = tmp_path_factory.mktemp('menu-layout')
    (root / 'projects').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PORT': str(port),
           'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0',
           'AI_MANAGER_RESTRICTED': '0', 'AI_MANAGER_PROJECT': '', 'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM], env=env, cwd=ROOT)
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 30
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
            yield context, url
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def open_menu(browser_app, width, height):
    context, url = browser_app
    page = context.new_page()
    page.set_default_timeout(10000)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(url)
    page.wait_for_function('()=>state.ready&&!!state.history&&!pollRunning')
    page.evaluate('()=>clearTimeout(pollTimer)')
    page.locator('#project-menu-button').click()
    page.locator('#project-menu .project-menu-row').first.wait_for()
    return page


GEOMETRY = '''()=>{
  const menu=document.querySelector('#project-menu'),box=menu.getBoundingClientRect();
  const rect=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
  return {menu:{left:box.left,right:box.right,overflow:menu.scrollWidth-menu.clientWidth},
    rows:[...menu.querySelectorAll('.project-menu-row')].map(row=>({
      name:row.querySelector('button')?.textContent||'',
      checkbox:rect(row.querySelector('input[type=checkbox]')),
      button:rect(row.querySelector('button')),
      badge:row.querySelector('.visibility-badge')&&rect(row.querySelector('.visibility-badge')),
      status:[...row.querySelectorAll('.project-menu-status span')].map(span=>({text:span.textContent,cls:span.className,...rect(span),
        overflow:span.scrollWidth-span.clientWidth})),
      lineHeight:parseFloat(getComputedStyle(row.querySelector('button')).lineHeight)}))};
}'''


@pytest.mark.parametrize('width,height,minimum', [(320, 640, 140), (390, 800, 190), (1200, 900, 200)])
def test_names_keep_width_and_status_wraps_beneath(browser_app, width, height, minimum):
    page = open_menu(browser_app, width, height)
    try:
        geometry = page.evaluate(GEOMETRY)
    finally:
        page.close()
    assert geometry['menu']['overflow'] <= 0
    by_name = {row['name']: row for row in geometry['rows']}
    for name in ('Bot Boss', 'Business', 'FitBean', 'Plain'):
        row = by_name[name]
        # Short names stay on one line with a readable width, never a vertical letter strip.
        assert row['button']['width'] >= minimum
        assert row['button']['height'] < row['lineHeight'] * 2 + 30
        assert row['checkbox']['right'] <= row['button']['left'] + 1
    assert any(row['status'] for row in geometry['rows'])
    for row in geometry['rows']:
        if row['badge']:
            assert row['button']['right'] <= row['badge']['left'] + 1
            assert row['badge']['right'] <= geometry['menu']['right'] + 1
            assert abs((row['badge']['top'] + row['badge']['bottom']) / 2
                       - (row['button']['top'] + row['button']['bottom']) / 2) < 12 or row['button']['height'] > 60
        for status in row['status']:
            assert status['top'] >= row['button']['bottom'] - 1
            assert status['left'] >= geometry['menu']['left'] and status['right'] <= geometry['menu']['right'] + 1
            assert status['overflow'] <= 0
    # Long names wrap inside their own column instead of widening the menu.
    long_row = next(row for name, row in by_name.items() if name.startswith('An-exceptionally'))
    assert long_row['button']['right'] <= geometry['menu']['right']
    assert len(long_row['status']) == 4  # sync, backup message, backup warning, unverified note
    classes = {name: [s['cls'] for s in row['status']] for name, row in by_name.items()}
    assert any('muted' in c and 'warning' not in c for c in classes['Bot Boss'])  # busy is informational
    assert any('warning' in c for c in classes['Business'])


def test_menu_remains_scrollable_and_actions_work_on_mobile(browser_app):
    page = open_menu(browser_app, 360, 500)
    try:
        assert page.evaluate("()=>{const m=document.querySelector('#project-menu');return m.scrollHeight>m.clientHeight}")
        before = page.evaluate("()=>document.querySelector('#project-menu').scrollTop")
        page.evaluate("()=>document.querySelector('#project-menu').scrollTo(0,9999)")
        assert page.evaluate("()=>document.querySelector('#project-menu').scrollTop") > before
        checkbox = page.locator('#project-menu input[data-visibility]').first
        assert checkbox.get_attribute('aria-label').startswith('Show ')
        page.locator('#project-menu [data-project="' + page.evaluate("()=>state.portfolio.projects[0].id") + '"]').click()
        assert page.locator('#project-menu').is_hidden()
    finally:
        page.close()

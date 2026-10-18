"""Real Chromium: a restricted work instance opens straight into its one project and hides what the server rejects."""
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
uvicorn.run(create_app(s, FakeServer(s)), host='127.0.0.1', port=s.port, log_level='error')
'''


def test_restricted_ui_selects_the_one_project_and_hides_rejected_controls(tmp_path):
    (tmp_path / 'work').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(tmp_path / 'data'), 'AI_MANAGER_PORT': str(port),
           'AI_MANAGER_RESTRICTED': '1', 'AI_MANAGER_PROJECT': str(tmp_path / 'work'), 'AI_MANAGER_SECURE_COOKIE': '0',
           'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM], env=env, cwd=ROOT)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(200):
            try:
                if httpx.get(url + '/api/auth-status', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1200, 'height': 800})
            page.set_default_timeout(10000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.locator('#username').fill('owner')
            page.locator('#password').fill(secrets.token_urlsafe(16))
            page.locator('#setup-token').fill((tmp_path / 'data/setup-token').read_text())
            page.locator('#login-button').click()
            page.wait_for_function('()=>state.ready&&!!state.history&&!!state.project')
            assert page.evaluate('()=>state.restricted') is True
            assert page.locator('#projects button').all_inner_texts() == ['work']
            assert page.locator('#projects button.selected').count() == 1
            page.locator('[data-view=settings]').click()
            for hidden in ('#all-projects', '#project-menu-button', '#discover', '#add-project', '#deployment-panel',
                           '#model-settings-button', '#models-panel', '#model-dialog', '#github-panel'):
                assert not page.locator(hidden).is_visible(), hidden
            page.evaluate('()=>renderProjectMenu()')
            assert page.locator('[data-project="github-settings"], [data-project="github-create"], [data-project="github-rename"]').count() == 0
            assert page.locator('#rename-project').is_visible()
            assert page.evaluate("async()=>(await fetch('/api/deployment')).status") == 403
            assert errors == []
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

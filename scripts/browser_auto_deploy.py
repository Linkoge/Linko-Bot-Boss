"""Disposable desktop/mobile Settings persistence and owner/CSRF acceptance."""
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix='linko-auto-deploy-browser-') as temporary:
        root = Path(temporary)
        projects = root / 'projects'
        for name in ('Boss', 'Other'):
            (projects / name).mkdir(parents=True)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        program = '''import os, sys
from pathlib import Path
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
app = create_app(s, FakeServer(s))
root = Path(os.environ['AI_MANAGER_PROJECT_ROOTS'])
app.state.registry.add(root / 'Boss', name='Linko-Bot-Boss')
app.state.registry.add(root / 'Other', name='Other')
uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')
'''.replace('PORT', str(port))
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(projects),
               'AI_MANAGER_SECURE_COOKIE': '0', 'AI_MANAGER_REPO': str(projects / 'Boss')}
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Fixture did not start')
            assert httpx.put(base + '/api/settings/auto-deploy', json={'enabled': True}).status_code == 401
            token = (root / 'data/setup-token').read_text()
            assert httpx.post(base + '/api/setup', json={'username': 'owner', 'password': 'long-private-browser-password',
                                                       'token': token}).status_code == 200
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width, height in ((1440, 1000), (393, 851)):
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    page.goto(base)
                    page.locator('#username').fill('owner')
                    page.locator('#password').fill('long-private-browser-password')
                    page.locator('#auth-form button').click()
                    page.locator('[data-view=settings]').click()
                    boss = page.get_by_role('checkbox', name='Linko-Bot-Boss: Auto-merge and deploy finished work', exact=True)
                    other = page.get_by_role('checkbox', name='Other: Auto-merge and deploy finished work', exact=True)
                    boss.wait_for()
                    assert boss.is_checked()
                    assert not other.is_checked()
                    project = boss.get_attribute('data-project-id')
                    assert page.evaluate('''async project => (await fetch('/api/settings/auto-deploy', {
                        method:'PUT',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({project_id:project,enabled:false})})).status''', project) == 403
                    boss.uncheck()
                    page.wait_for_function("() => !document.querySelector('#auto-deploy-settings input').disabled")
                    page.reload()
                    page.locator('[data-view=settings]').click()
                    boss.wait_for()
                    assert not boss.is_checked()
                    assert not other.is_checked()
                    boss.check()
                    page.wait_for_function("() => !document.querySelector('#auto-deploy-settings input').disabled")
                    page.reload()
                    page.locator('[data-view=settings]').click()
                    boss.wait_for()
                    assert boss.is_checked()
                    assert not other.is_checked()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.close()
                browser.close()
            print('Auto-deploy browser checks passed: owner/CSRF, ON seed, per-project toggle persistence, desktop/mobile')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

"""Disposable desktop/mobile check of the Tasks view: states, Hold/Release/Cancel/Continue, repair-round status."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

SEED = '''
import time
p = app.state.registry.list()[0]
tasks = app.state.scheduler.tasks
db = app.state.db
t = {name: tasks.add(p['id'], f'{name} objective') for name in ('running', 'queued', 'held', 'failed', 'done')}
with db.transaction() as conn:
    conn.execute("UPDATE tasks SET state='running',lease_owner='lease' WHERE id=?", (t['running']['id'],))
    conn.execute("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,turn_id) VALUES('lease',?,?,'execution','running',?,'turn')", (t['running']['id'], p['id'], time.time()))
    conn.execute("UPDATE tasks SET held=1 WHERE id=?", (t['held']['id'],))
    conn.execute("UPDATE tasks SET state='failed',blocker='Stopped after 5 repair rounds; still failing: [smoke]',rounds=5 WHERE id=?", (t['failed']['id'],))
    conn.execute("UPDATE tasks SET state='done' WHERE id=?", (t['done']['id'],))
    # Keep the fixture independent of dispatch: nothing may start while the browser looks at it.
    conn.execute("UPDATE tasks SET queued_at=queued_at+1e9 WHERE state='queued'")
'''


def main():
    with tempfile.TemporaryDirectory(prefix='linko-tasks-browser-') as temporary:
        root = Path(temporary)
        (root / 'projects' / 'Alpha').mkdir(parents=True)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        program = '''import sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
app = create_app(s, FakeServer(s))
app.state.registry.add(s.project_roots[0] / 'Alpha')
''' + SEED + '''
uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')
'''.replace('PORT', str(port))
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'),
               'AI_MANAGER_SECURE_COOKIE': '0'}
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
            token = (root / 'data/setup-token').read_text()
            assert httpx.post(base + '/api/setup', json={'username': 'owner', 'password': 'long-private-browser-password',
                                                       'token': token}).status_code == 200
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width, height, interact in ((393, 851, False), (1440, 1000, True)):  # layout first, then the mutations
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base)
                    page.locator('#username').fill('owner')
                    page.locator('#password').fill('long-private-browser-password')
                    page.locator('#auth-form button').click()
                    page.locator('[data-view=tasks]').click()

                    def row(name):
                        return page.locator('#task-list article', has_text=f'{name} objective')

                    row('running').wait_for()

                    def buttons(name):
                        return [b.inner_text() for b in row(name).locator('.task-controls button').all() if b.inner_text() != 'History']

                    assert buttons('running') == ['Cancel']
                    assert buttons('queued') == ['Hold', 'Cancel']
                    assert buttons('held') == ['Release', 'Cancel'] and 'Held' in row('held').inner_text()
                    assert buttons('failed') == ['Continue'] and buttons('done') == []
                    failed = row('failed').inner_text()
                    assert 'Stopped after 5 repair rounds' in failed and 'Repair rounds 5 of 5' in failed
                    assert page.locator('#task-count').inner_text() == '3'  # running, queued, held
                    if interact:
                        row('queued').get_by_role('button', name='Hold').click()
                        page.wait_for_function("() => [...document.querySelectorAll('#task-list article')].some(a=>a.innerText.includes('queued objective')&&a.innerText.includes('Held'))")
                        row('queued').get_by_role('button', name='Release').click()
                        page.wait_for_function("() => [...document.querySelectorAll('#task-list article')].some(a=>a.innerText.includes('queued objective')&&!a.innerText.includes('Held'))")
                        row('held').get_by_role('button', name='Cancel').click()
                        page.wait_for_function("() => [...document.querySelectorAll('#task-list article')].some(a=>a.innerText.includes('held objective')&&a.innerText.includes('Cancelled by owner'))")
                        row('failed').get_by_role('button', name='Continue').click()
                        page.wait_for_function("() => [...document.querySelectorAll('#task-list article')].some(a=>a.innerText.includes('failed objective')&&a.innerText.includes('Repair rounds 5 of 5')&&a.querySelector('.task-controls').innerText.includes('Hold'))")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors, errors
                    page.close()
                browser.close()
            print(json.dumps({'status': 'passed', 'viewports': [393, 1440]}))
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

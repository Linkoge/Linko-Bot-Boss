"""Disposable browser acceptance for deployment status/admission/cancellation."""
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from ai_manager.db import Database
from ai_manager.deployment import Handoff, atomic


def main():
    with tempfile.TemporaryDirectory(prefix='linko-deploy-browser-') as temporary:
        root = Path(temporary)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        program = """import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.web import create_app
from ai_manager.config import Settings
s=Settings();app=create_app(s,FakeServer(s))
# Unfinished recovery prevents idle acknowledgement without running inference.
with app.state.db.transaction() as c:
 c.execute("INSERT INTO executions(id,agent_id,status,started_at,retry_at,recovery_reason) VALUES('blocked','manager','recovering',1,9999999999,'usage')")
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error')
""".replace('PORT', str(port))
        env = {**os.environ, 'AI_MANAGER_DATA': str(root/'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root/'projects'),
               'AI_MANAGER_SECURE_COOKIE': '0'}
        (root/'projects').mkdir()
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    if httpx.get(base+'/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Fixture did not start')
            token = (root/'data/setup-token').read_text()
            assert httpx.post(base+'/api/setup', json={'username': 'owner', 'password': 'long-private-browser-password',
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
                    page.locator('#deployment-check').click()
                    page.wait_for_function("()=>document.querySelector('#deployment-status').textContent==='No pending deployment.' || document.querySelector('#deployment-status').textContent.includes('cancelled')")
                    handoff = Handoff(root/'data')
                    release = 'browser-'+str(width)
                    atomic(handoff.root/'releases'/release/'manifest.json', {'id': release})
                    handoff.request(release)
                    page.locator('#deployment-check').click()
                    page.wait_for_function("()=>document.querySelector('#deployment-status').textContent.includes('waiting idle')")
                    assert page.locator('#deployment-cancel').is_visible()
                    result = page.evaluate("async()=>{const a=await (await fetch('/api/auth-status')).json();return (await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':a.csrf},body:JSON.stringify({content:'must not run'})})).status}")
                    assert result == 503
                    page.locator('#deployment-cancel').click()
                    page.wait_for_function("()=>document.querySelector('#deployment-status').textContent.includes('cancelled')")
                    assert not page.locator('#deployment-cancel').is_visible()
                    assert not handoff.gated()
                    # Disposable durable unused intent; backend source/routing is
                    # exercised by test_deployment_intent, this covers owner UI.
                    db = Database(root/'data/manager.sqlite3')
                    identifier = str(width).zfill(32)
                    db.set_setting('deployment_intent:'+identifier, {
                        'id': identifier, 'project_id': 'ui-fixture', 'conversation_id': 'ui-fixture',
                        'source_message_id': 'ui-fixture', 'checked_owner_message': 'ui-fixture',
                        'scope': {'release_id': release}, 'candidate': {'release_id': release},
                        'task_ids': [], 'status': 'recorded'})
                    page.locator('#deployment-check').click()
                    page.locator('#deployment-intents button').wait_for()
                    assert 'recorded' in page.locator('#deployment-intents').inner_text()
                    page.locator('#deployment-intents button').click()
                    page.wait_for_function("()=>document.querySelector('#deployment-intents').children.length===0")
                    assert db.setting('deployment_intent:'+identifier)['status'] == 'revoked'
                    assert not handoff.gated()
                    page.close()
                browser.close()
            print('Deployment browser checks passed: desktop/mobile status, admission, pending-request and unused-intent cancellation')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

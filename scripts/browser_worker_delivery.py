"""Owner-facing current/history/copy/reload projection against disposable durable transport."""
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from browser_chat_reliability import PROGRAM
from playwright.sync_api import sync_playwright

SEED = '''
import time
from ai_manager.tasks import Tasks
c=app.state.chat.conversation()
p=app.state.registry.list()[0]
t=Tasks(app.state.db).add(p['id'],'INTERNAL_OBJECTIVE',c['id'])
e=Tasks(app.state.db).claim(2)
with app.state.db.transaction() as conn:
    conn.execute("UPDATE tasks SET lifecycle='completed',lease_owner=NULL WHERE id=?",(t['id'],))
    conn.execute("UPDATE executions SET finished_at=?,status='completed',delivery='delivered' WHERE id=?",(time.time(),e['lease_owner']))
    for ident,role,body in [('result:'+e['lease_owner'],'worker','Codex result: INTERNAL_OBJECTIVE'),('literal','user','Codex result: {"objective":"OWNER_LITERAL_JSON"}'),('reply','assistant','Ready; activation pending.')]:
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,NULL,?)',(ident,c['id'],role,body,'completed',time.time()))
'''


def main():
    with tempfile.TemporaryDirectory(prefix='linko-worker-ui-') as directory:
        root = Path(directory)
        for name in ('Alpha', 'Beta'):
            (root / 'projects' / name).mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
        program = PROGRAM.replace('uvicorn.run(app,', SEED + '\nuvicorn.run(app,').replace('PORT', str(port))
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(url + '/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.locator('#app').wait_for(state='visible')
                page.evaluate('selectProject(null)')
                page.wait_for_function("() => document.querySelector('#messages').textContent.includes('OWNER_LITERAL_JSON')")
                for _ in range(2):
                    text = page.locator('#messages').inner_text()
                    assert 'INTERNAL_OBJECTIVE' not in text and 'OWNER_LITERAL_JSON' in text
                    assert 'activation pending' in text
                    assert page.evaluate("state.history.messages.filter(m=>m.role==='worker').length") == 0
                    page.reload()
                    page.locator('#app').wait_for(state='visible')
                    page.evaluate('selectProject(null)')
                    page.wait_for_function("() => document.querySelector('#messages').textContent.includes('OWNER_LITERAL_JSON')")
                # Saved-history API shares the projection used by history rendering/copy.
                result = page.evaluate("async()=>{const c=state.history.conversation;return await api('/api/chat-history/'+c.id)}")
                assert 'INTERNAL_OBJECTIVE' not in str(result) and 'OWNER_LITERAL_JSON' in str(result)
                browser.close()
                print('Worker transport hidden; literal owner JSON, natural status, reload and saved history preserved.')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

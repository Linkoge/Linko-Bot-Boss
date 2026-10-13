"""Exercise global/project recency, alphabetical menu and responsive navigation."""

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix='linko-recency-') as temporary:
        root = Path(temporary)
        projects = root / 'projects'
        for name in ('Aardvark', 'Alpha', 'Beta', *[f'Project {i:02}' for i in range(16)]):
            (projects / name).mkdir(parents=True)
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(projects), 'AI_MANAGER_SECURE_COOKIE': '0'}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        program = f"""import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
if not app.state.registry.list():
    for path in sorted(s.project_roots[0].iterdir()):app.state.registry.add(path)
    app.state.db.set_setting('global_last_used_at',100)
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE projects SET last_used_at=200 WHERE name='Alpha'")
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')
"""

        def start():
            process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError('Fixture failed to start')

        process = start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(has_touch=True)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('fixture-private-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.locator('#app').wait_for(state='visible')
                page.wait_for_function('() => state.history')
                assert page.evaluate('state.project') == page.evaluate("state.portfolio.projects.find(p=>p.name==='Alpha').id")
                page.evaluate('selectProject(null)')
                page.wait_for_function('() => state.history?.conversation.project_id === null')
                ids = page.evaluate("Object.fromEntries(state.portfolio.projects.map(p=>[p.name,p.id]))")
                def order():
                    return page.locator('#projects button').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.project)')
                assert order()[:3] == [ids['Alpha'], '', ids['Aardvark']]
                snapshot = page.evaluate('JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])')
                # Opening, polling, and draft switches leave recency alone.
                page.locator('#message-input').fill('Global draft')
                page.locator('#projects').get_by_role('button', name='Alpha', exact=True).click()
                page.locator('#message-input').fill('Project draft')
                page.locator('#all-projects').click()
                assert page.locator('#message-input').input_value() == 'Global draft'
                page.evaluate('refresh()')
                assert snapshot == page.evaluate('JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])')
                page.locator('#project-menu-button').click()
                menu_ids = page.locator('#project-menu [role=menuitemradio]').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.project)')
                assert menu_ids[:4] == [ids['Aardvark'], '', ids['Alpha'], ids['Beta']]
                page.keyboard.press('Home')
                assert page.evaluate('document.activeElement.dataset.project') == ids['Aardvark']
                page.keyboard.press('ArrowDown')
                assert page.evaluate('document.activeElement.dataset.project') == ''
                page.evaluate('refresh()')
                assert page.evaluate('document.activeElement.dataset.project') == ''
                page.keyboard.press('Escape')
                assert page.locator('#project-menu').is_hidden()
                page.locator('#all-projects').focus()
                page.evaluate('refresh()')
                assert page.evaluate('document.activeElement.id') == 'all-projects'
                page.locator('#message-input').fill('Global meaningful submission')
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function("() => state.history?.messages.some(m=>m.content==='Saved streamed answer') && !state.execution")
                page.evaluate('refresh()')
                assert order()[0] == ''
                assert page.evaluate('state.history.conversation.project_id') is None
                global_id = page.evaluate('state.history.conversation.id')
                global_recency = page.evaluate('state.portfolio.global_last_used_at')
                page.locator('#projects').get_by_role('button', name='Alpha', exact=True).click()
                assert page.locator('#message-input').input_value() == 'Project draft'
                page.wait_for_function('() => state.history?.conversation.project_id === state.project')
                assert page.evaluate('state.history.conversation.id') != global_id
                page.locator('#message-input').fill('Project meaningful submission')
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function("() => state.history?.messages.some(m=>m.content==='Saved streamed answer') && !state.execution")
                page.evaluate('refresh()')
                assert order()[:2] == [ids['Alpha'], '']
                assert page.evaluate('state.portfolio.global_last_used_at') == global_recency
                # Existing response guard must ignore delayed history from a prior selection.
                page.evaluate("""window.originalApi=api;window.releaseHistory=null;
                    api=async(path,options)=>{const result=await originalApi(path,options);
                    if(path.startsWith('/api/history?'))return new Promise(resolve=>{window.releaseHistory=()=>resolve(result);});
                    return result;};window.oldHistory=loadHistory();null;""")
                page.wait_for_function('() => !!window.releaseHistory')
                page.evaluate('api=originalApi;selectProject(null)')
                page.wait_for_function('() => state.history?.conversation.project_id === null')
                page.evaluate('releaseHistory();oldHistory')
                assert page.evaluate('state.history.conversation.id') == global_id
                assert page.evaluate('state.project') is None
                # All Projects may move off screen; the fixed menu remains accessible.
                for width, height in [(1440, 1000), (393, 851), (393, 420), (320, 700)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    nav = page.locator('.navigation').bounding_box()
                    assert nav['height'] <= 100
                    page.wait_for_function("() => $('send').getBoundingClientRect().bottom <= innerHeight")
                    composer = page.locator('#send').bounding_box()
                    assert composer['y'] + composer['height'] <= height
                    page.evaluate("$('project-tabs').scrollLeft=10000")
                    page.locator('#project-menu-button').tap()
                    page.locator('#project-menu [data-project=""]').tap()
                    assert page.locator('#project-menu').is_hidden()
                    assert page.evaluate('state.project') is None
                    page.screenshot(path=f'/tmp/linko-global-recency-{width}x{height}.png')
                before = page.evaluate('JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])')
                process.terminate()
                process.wait(timeout=10)
                process = start()
                page.reload()
                page.locator('#app').wait_for(state='visible')
                page.wait_for_function('() => state.history')
                assert order()[:2] == [ids['Alpha'], '']
                assert before == page.evaluate('JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])')
                assert page.evaluate('state.project') == ids['Alpha']
                page.evaluate('selectProject(null)')
                page.wait_for_function('() => state.history?.conversation.project_id === null')
                assert page.evaluate('state.history.conversation.id') == global_id
                assert not errors, errors
                browser.close()
                print('PASS: recency, null identity, drafts, alphabetical keyboard menu, selection guard, four viewports, server restart')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

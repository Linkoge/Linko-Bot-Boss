"""Disposable stash persistence, recording guards and responsive navigation checks."""
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_STASH_EVIDENCE', '/tmp/linko-stash-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linko-stash-') as temporary:
        root = Path(temporary)
        projects = root / 'projects'
        for name in ['Alpha', 'Beta']:
            (projects / name).mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(projects / name)], check=True)
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(projects), 'AI_MANAGER_SECURE_COOKIE': '0'}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        program = f'''import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
app.state.db.set_setting('scheduler_enabled',False)
if not app.state.registry.list():
 for name in ['Alpha','Beta']:
  p=app.state.registry.add(s.project_roots[0]/name)
  if name=='Beta':p=app.state.registry.update(p['id'],p['revision'],{{'status':'paused'}})
  app.state.chat.conversation(p['id'])
  app.state.scheduler.tasks.add(p['id'],name+' retained task')
 app.state.chat.conversation(None)
 app.state.db.set_setting('global_last_used_at',100)
 with app.state.db.transaction() as conn:conn.execute("UPDATE projects SET last_used_at=500 WHERE name='Alpha'")
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')'''

        def start():
            process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            raise RuntimeError('Fixture startup failed')

        process = start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('disposable-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history')
                ids = page.evaluate('Object.fromEntries(state.portfolio.projects.map(p=>[p.name,p.id]))')
                alpha, beta = ids['Alpha'], ids['Beta']
                assert page.evaluate('state.project') == alpha
                origin = page.evaluate('state.history.conversation.id')
                page.locator('#message-input').fill('Alpha retained draft')
                page.locator('#attachment-input').set_input_files({'name': 'draft.txt', 'mimeType': 'text/plain', 'buffer': b'retain this upload'})
                page.wait_for_function('()=>attachmentDraft(state.project).length===1&&!attachmentState.uploads.size')
                attachment = page.evaluate('attachmentDraft(state.project)[0].id')

                def open_menu():
                    if page.locator('#project-menu').is_hidden():
                        page.locator('#project-menu-button').click()

                def checkbox(project):
                    return page.locator(f'#project-menu [data-visibility="{project}"]')

                open_menu()
                # Simulate only capture phase for UI guard; browser_audio covers real capture.
                page.evaluate("audioState.session={project:state.project,phase:'recording',label:'Alpha',message:'00:01'};renderAudio()")
                assert checkbox(alpha).is_disabled()
                assert checkbox(beta).is_disabled()
                assert page.locator('#all-projects').is_disabled()
                page.evaluate('(id)=>selectProject(id)', beta)
                assert page.evaluate('state.project') == alpha
                page.evaluate("audioState.session=null;renderAudio()")
                checkbox(alpha).uncheck()
                page.wait_for_function('(id)=>state.project===null&&state.history&&state.portfolio.projects.find(p=>p.id===id).visible===false', arg=alpha)
                assert page.locator(f'#projects [data-project="{alpha}"]').count() == 0
                assert page.evaluate('(id)=>state.drafts.get(id)', alpha) == 'Alpha retained draft'
                page.locator('#message-input').fill('Global retained draft')
                open_menu()
                page.locator('#project-menu summary').click()
                checkbox(alpha).check()
                page.wait_for_function('(id)=>state.portfolio.projects.find(p=>p.id===id).visible===true', arg=alpha)
                assert page.evaluate('state.project') is None  # restore does not navigate
                page.locator(f'#project-menu [data-project="{alpha}"]').click()
                page.wait_for_function('(id)=>state.project===id&&state.history', arg=alpha)
                assert page.evaluate('state.history.conversation.id') == origin
                assert page.locator('#message-input').input_value() == 'Alpha retained draft'
                assert page.evaluate('attachmentDraft(state.project)[0].id') == attachment
                # Paused stays paused. Stash survives reload and service restart; startup skips hidden recency.
                open_menu()
                checkbox(alpha).uncheck()
                page.wait_for_function('()=>state.project===null&&state.history')
                open_menu()
                checkbox(beta).uncheck()
                page.wait_for_function('()=>state.portfolio.projects.every(p=>p.visible===false)')
                page.reload()
                page.wait_for_function('()=>state.ready&&state.history')
                assert page.evaluate('state.project') is None
                assert page.locator('#message-input').input_value() == 'Global retained draft'
                assert page.locator('#projects button').count() == 1
                assert page.evaluate('state.portfolio.tasks.length') == 2  # stashed tasks still global
                assert page.evaluate('(id)=>state.portfolio.projects.find(p=>p.id===id).status', beta) == 'paused'
                # Empty Git discovery and explicit Add operate only on the disposable root.
                (projects / 'Empty').mkdir()
                subprocess.run(['git', 'init', '-q', str(projects / 'Empty')], check=True)
                page.evaluate("async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();}")
                assert page.evaluate('(id)=>state.portfolio.projects.find(p=>p.id===id).visible', alpha) is False
                assert page.evaluate("state.portfolio.projects.find(p=>p.name==='Empty').visible") is True
                (projects / 'Added').mkdir()
                page.evaluate("async(path)=>{await api('/api/projects',{method:'POST',body:JSON.stringify({path})});await refresh();}", str(projects / 'Added'))
                assert page.evaluate("state.portfolio.projects.find(p=>p.name==='Added').visible") is True
                process.terminate()
                process.wait(timeout=10)
                process = start()
                page.reload()
                page.wait_for_function('()=>state.ready&&state.history')
                assert page.evaluate('state.project') is None
                assert page.evaluate('(id)=>state.portfolio.projects.find(p=>p.id===id).visible', alpha) is False
                for width, height in [(1440, 1000), (393, 851), (320, 420)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    open_menu()
                    summary = page.locator('#project-menu summary')
                    if not summary.evaluate('e=>e.parentElement.open'):
                        summary.click()
                    assert checkbox(beta).is_visible()
                    assert checkbox(beta).evaluate('(e)=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(evidence / f'stash-{width}x{height}.png'))
                    page.keyboard.press('Escape')
                open_menu()
                if not page.locator('#project-menu summary').evaluate('e=>e.parentElement.open'):
                    page.locator('#project-menu summary').click()
                checkbox(beta).focus()
                page.keyboard.press('Space')
                page.wait_for_function('(id)=>state.portfolio.projects.find(p=>p.id===id).visible===true', arg=beta)
                assert page.evaluate('(id)=>state.portfolio.projects.find(p=>p.id===id).status', beta) == 'paused'
                assert (projects / 'Alpha' / '.git').exists()
                with sqlite3.connect(root / 'data/manager.sqlite3') as conn:
                    assert conn.execute('pragma integrity_check').fetchone()[0] == 'ok'
                    assert not conn.execute('pragma foreign_key_check').fetchall()
                assert not errors, errors
                (evidence / 'result.json').write_text(json.dumps({'passed': True, 'viewports': [1440, 393, 320], 'draft': True, 'attachments': True, 'chat_id': origin, 'reload_restart_rescan': True, 'paused_status': True, 'empty_and_explicit_add': True, 'recording_guard_simulated': True, 'page_errors': errors}, indent=2))
                browser.close()
                print('PASS: stash/restore, drafts/uploads/chat identity, all-stashed startup, reload/restart/rescan, empty/Add, paused/tasks, capture guard, keyboard and three viewports')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

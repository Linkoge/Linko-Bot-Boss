"""Authenticated Tasks/Agents lifecycle and narrow viewport acceptance on disposable processes."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_AGENT_EVIDENCE', '/tmp/linko-agent-controls-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    receipts = []
    with tempfile.TemporaryDirectory(prefix='linko-controls-ui-') as temporary:
        root = Path(temporary)
        projects = root/'projects'
        (projects/'Alpha').mkdir(parents=True)
        env = {**os.environ, 'AI_MANAGER_DATA': str(root/'data'), 'AI_MANAGER_PROJECT_ROOTS': str(projects), 'AI_MANAGER_SECURE_COOKIE': '0'}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        program = f'''import sys
sys.path.insert(0,'tests')
import uvicorn,asyncio
from fastapi import Depends
from fake_codex import FakeServer
from test_agent_controls import Controls
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
require_owner=next(r for r in app.routes if getattr(r,'path','')=='/api/portfolio').dependant.dependencies[0].call
app.state.scheduler.start=lambda: None
p=app.state.registry.add(s.project_roots[0]/'Alpha')
controls=Controls(hold_cleanup=True);app.state.workers.factory=controls.factory
@app.post('/fixture/start',dependencies=[Depends(require_owner)])
async def start():
 controls.allow_close.clear()
 controls.outcomes=['hold']
 asyncio.get_running_loop().call_later(8,controls.allow_close.set)
 t=app.state.scheduler.tasks.add(p['id'],'UI fixture '+str(len(app.state.scheduler.tasks.list())))
 claim=app.state.scheduler.tasks.claim(2)
 app.state.workers.launch(claim)
 return claim
@app.post('/fixture/finish',dependencies=[Depends(require_owner)])
async def finish():
 controls.allow_close.set()
 return {{'ok':True}}
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')
'''
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base+'/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            assert httpx.patch(base+'/api/tasks/unknown',json={'action':'cancel','revision':0}).status_code == 401
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('fixture-private-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('() => state.history')
                page.locator('#projects').get_by_role('button',name='Alpha',exact=True).click()
                page.wait_for_function('() => state.project && state.history')
                for width in (1440,393,320):
                    page.set_viewport_size({'width':width,'height':900})
                    page.evaluate("async()=>{window.fixtureTask=await api('/fixture/start',{method:'POST'});}")
                    page.wait_for_function('async()=>{await refresh();return state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.lease_owner&&t.status==="running");}')
                    page.locator('[data-view=tasks]').click()
                    task = page.locator(f'#task-list [data-task-id="{page.evaluate("fixtureTask.id")}"]')
                    assert task.get_by_role('button',name='Pause',exact=True).is_visible()
                    assert task.get_by_role('button',name='Cancel',exact=True).is_visible()
                    task.get_by_role('button',name='Pause',exact=True).click()
                    page.wait_for_function('() => state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="pause_requested")')
                    assert 'Pause requested' in task.inner_text()
                    assert task.get_by_role('button',name='Resume',exact=True).count() == 0
                    page.locator('[data-view=agents]').click()
                    agent = page.locator(f'#agent-list [data-execution-id="{page.evaluate("fixtureTask.lease_owner")}"]')
                    expect(agent).to_contain_text('Pause requested')
                    assert agent.get_by_role('button',name='Cancel',exact=True).is_visible()
                    page.screenshot(path=str(evidence/f'pause-requested-{width}.png'))
                    page.evaluate("api('/fixture/finish',{method:'POST'})")
                    page.wait_for_function('async()=>{await refresh();return state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="paused"&&!t.lease_owner);}')
                    expect(agent).to_contain_text('Paused')
                    page.locator('[data-view=tasks]').click()
                    assert task.get_by_role('button',name='Resume',exact=True).is_visible()
                    task.get_by_role('button',name='Resume',exact=True).click()
                    page.wait_for_function('() => state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="queued")')
                    # Dispatch the same queued assignment via the fixture, preserving its ID.
                    page.evaluate("async()=>{window.fixtureTask=await api('/fixture/start',{method:'POST'});}")
                    page.wait_for_function('async()=>{await refresh();return state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="running");}')
                    page.locator('[data-view=agents]').click()
                    agent = page.locator(f'#agent-list [data-execution-id="{page.evaluate("fixtureTask.lease_owner")}"]')
                    agent.get_by_role('button',name='Cancel',exact=True).click()
                    page.wait_for_function('() => state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="cancel_requested")')
                    expect(agent).to_contain_text('Cancel requested')
                    page.screenshot(path=str(evidence/f'cancel-requested-{width}.png'))
                    page.evaluate("api('/fixture/finish',{method:'POST'})")
                    page.wait_for_function('async()=>{await refresh();return state.portfolio.tasks.some(t=>t.id===fixtureTask.id&&t.status==="cancelled"&&!t.lease_owner);}')
                    page.locator('[data-view=tasks]').click()
                    cancelled = page.locator('#task-list .panel').filter(has_text='Cancelled · partial work retained')
                    assert cancelled.get_by_role('button',name='Resume',exact=True).count() == 0
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    receipts.append({'width':width,'pause_requested_confirmed_resume_cancel_requested_confirmed':True,'no_overflow':True})
                    # Cancel the extra queued fixture objective to leave only historical records.
                    page.evaluate('''async()=>{for(const t of state.portfolio.tasks.filter(t=>t.status==='queued'))await api(`/api/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({action:'cancel',revision:t.revision})});await refresh();}''')
                assert not errors, errors
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    (evidence/'browser-controls.json').write_text(json.dumps(receipts,indent=2))
    print(json.dumps(receipts,indent=2))


if __name__ == '__main__':
    main()

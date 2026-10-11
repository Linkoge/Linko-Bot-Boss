"""Disposable authenticated project/race and viewport checks; no physical keyboard claim."""

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    receipts = []
    with tempfile.TemporaryDirectory(prefix='linko-project-ui-') as temporary:
        root = Path(temporary)
        projects = root / 'projects'
        for name in ('Alpha', 'Beta'):
            (projects / name).mkdir(parents=True)
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(projects), 'AI_MANAGER_SECURE_COOKIE': '0'}
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
for name in ('Alpha','Beta'):
 p=app.state.registry.add(s.project_roots[0]/name)
 t=app.state.scheduler.tasks.add(p['id'],name+' scoped objective')
 with app.state.db.transaction() as conn:
  conn.execute("UPDATE tasks SET lifecycle='waiting',created_at=1700000000 WHERE id=?",(t['id'],))
  conn.execute("INSERT INTO executions(id,project_id,agent_id,status,started_at,finished_at,turn_id) VALUES(?,?,'execution','completed',1700000000,1700000060,'turn')",(name,p['id']))
  app.state.db.event(conn,'worker.started',{{}},p['id'],execution_id=name)
  conn.execute("UPDATE events SET created_at=1700000040 WHERE execution_id=?",(name,))
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(viewport={'width':393,'height':851}, timezone_id='Asia/Tbilisi', locale='en-GB')
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('fixture-private-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('() => state.history && state.portfolio.tasks.length===2')
                # Observe the untouched baseline with a keyboard overlay simulation.
                baseline = page.context.new_page()
                for filename in ('app.js', 'style.css'):
                    original = subprocess.check_output(['git','show','bd757456ecc81d495270c5619b5f8da3fa76abc8:ai_manager/static/'+filename],text=True)
                    baseline.route('**/static/'+filename,lambda route,request,body=original:route.fulfill(body=body,content_type='text/javascript' if route.request.url.endswith('.js') else 'text/css'))
                baseline.goto(base)
                baseline.wait_for_function('() => state.history')
                baseline.locator('#message-input').focus()
                baseline.evaluate("Object.defineProperty(window,'visualViewport',{configurable:true,value:{height:420,offsetTop:0,scale:1}})")
                old_bottom = baseline.locator('.composer').bounding_box()['y'] + baseline.locator('.composer').bounding_box()['height']
                assert old_bottom > 420
                receipts.append({'baseline_overlay':{'visible_bottom':420,'composer_bottom':old_bottom,
                    'observation':'Composer falls below overlay keyboard; unavailable owner screenshot not reproduced'}})
                baseline.screenshot(path='/tmp/linko-project-ui-baseline-overlay.png')
                baseline.close()
                page.locator('[data-view=tasks]').click()
                assert page.locator('#task-list .panel').count() == 2
                page.locator('#projects').get_by_role('button', name='Alpha', exact=True).click()
                page.wait_for_function('() => state.portfolio.scope_project_id===state.project && state.portfolio.tasks.length===1')
                assert page.locator('#task-list').inner_text().find('Beta scoped objective') == -1
                assert page.locator('#task-list time').inner_text() == 'Created 15/11/2023, 02:13 · Tbilisi'
                assert page.locator('#task-count').inner_text() == '1'
                page.locator('[data-view=agents]').click()
                assert page.locator('#agent-list time').first.inner_text() == 'Work started 15/11/2023, 02:14 · Tbilisi'
                assert page.locator('#agent-list time').nth(1).inner_text() == 'Queued 15/11/2023, 02:13 · Tbilisi'
                assert page.locator('#agent-count').is_hidden()
                # Delay Alpha's portfolio response, then switch to Beta while it is in flight.
                page.evaluate('''() => {
                  window.originalFetch=window.fetch;
                  window.delayed=[];
                  window.fetch=async (...args)=>{
                    const result=await originalFetch(...args);
                    if(String(args[0]).includes('/api/portfolio?project_id='+state.portfolio.projects.find(p=>p.name==='Alpha').id))
                      await new Promise(resolve=>delayed.push(resolve));
                    return result;
                  };
                  window.oldRefresh=refresh();
                }''')
                page.wait_for_function('() => delayed.length>0')
                page.locator('#projects').get_by_role('button',name='Beta',exact=True).click()
                page.wait_for_function('() => state.portfolio.scope_project_id===state.project && state.portfolio.tasks[0]?.description.startsWith("Beta")')
                page.evaluate('async()=>{delayed.forEach(resolve=>resolve());await oldRefresh;window.fetch=originalFetch;}')
                assert page.locator('#agent-list .panel').count() == 1
                assert 'Beta' in page.locator('#agent-list').inner_text()
                page.locator('[data-view=tasks]').click()
                assert 'Alpha scoped objective' not in page.locator('#task-list').inner_text()
                # Same-scope reversed polls must not overwrite a newer snapshot.
                page.evaluate('''async()=>{
                  let calls=0,release;
                  window.fetch=async(...args)=>{
                    if(!String(args[0]).includes('/api/portfolio'))return originalFetch(...args);
                    const response=await originalFetch(...args), data=await response.json();
                    const ordinal=++calls;data.active_agents=ordinal;
                    if(ordinal===1)await new Promise(resolve=>release=resolve);
                    return new Response(JSON.stringify(data),{status:200});
                  };
                  const old=refresh();
                  while(!release)await new Promise(resolve=>setTimeout(resolve,5));
                  await refresh();release();await old;window.fetch=originalFetch;
                }''')
                assert page.locator('#agent-count').inner_text() == '2'
                assert 'Beta' in page.locator('#agent-count').get_attribute('title')
                page.evaluate('refresh()')
                assert page.locator('#agent-count').is_hidden()
                page.locator('#all-projects').click()
                page.wait_for_function('() => state.portfolio.tasks.length===2')
                assert page.locator('#task-list .panel').count() == 2
                receipts.append({'scope':'Alpha/Beta/All Projects, delayed switch and reversed polls passed',
                                 'timestamps':'Tbilisi creation/confirmed-start differ by minute',
                                 'indicator':'0→2→0; title identifies selected scope'})
                page.locator('[data-view=chat]').click()
                page.locator('#message-input').fill('Retain draft during keyboard transitions')
                for width,height in ((1440,1000),(393,851),(393,420),(320,350)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.locator('#message-input').focus()
                    page.wait_for_function('() => Math.abs(document.querySelector("#app").getBoundingClientRect().height-visualViewport.height)<1')
                    geometry = page.evaluate('''() => {
                      const c=document.querySelector('.composer').getBoundingClientRect();
                      return {bottom:c.bottom,gap:visualViewport.height-c.bottom,
                        overflow:document.documentElement.scrollWidth-innerWidth,
                        messages:document.querySelector('#messages').getBoundingClientRect().height};
                    }''')
                    assert geometry['overflow'] == 0
                    assert 0 <= geometry['gap'] <= (32 if width>600 else 8), geometry
                    assert geometry['messages'] > 0
                    receipts.append({'viewport':[width,height],**geometry})
                # Simulate an overlay keyboard: layout viewport remains 851px,
                # visible viewport becomes 420px. Also simulate OS viewport pan.
                page.set_viewport_size({'width':393,'height':851})
                page.evaluate('''() => {
                    window.mockViewport=new EventTarget();
                    Object.assign(mockViewport,{height:420,offsetTop:0,scale:1});
                    Object.defineProperty(window,'visualViewport',{configurable:true,value:mockViewport});
                    mockViewport.addEventListener('resize',updateViewport);
                    mockViewport.addEventListener('scroll',updateViewport);
                    mockViewport.dispatchEvent(new Event('resize'));
                }''')
                for offset in (0,35):
                    page.evaluate('(top)=>{mockViewport.offsetTop=top;mockViewport.dispatchEvent(new Event("scroll"));}',offset)
                    geometry = page.evaluate('''() => {
                      const c=document.querySelector('.composer').getBoundingClientRect();
                      return {bottom:c.bottom,visibleBottom:visualViewport.height+visualViewport.offsetTop};
                    }''')
                    assert 0 <= geometry['visibleBottom']-geometry['bottom'] <= 8, geometry
                    receipts.append({'simulated_overlay_offset':offset,**geometry})
                assert page.locator('#message-input').input_value() == 'Retain draft during keyboard transitions'
                page.screenshot(path='/tmp/linko-project-ui-overlay.png')
                # Simulate CSS safe-area values, which Chromium desktop normally reports as zero.
                page.evaluate('() => {document.querySelector(".navigation").style.paddingTop="20px";document.querySelector(".composer-area").style.paddingBottom="28px";}')
                safe_gap=page.evaluate('visualViewport.height+visualViewport.offsetTop-document.querySelector(".composer").getBoundingClientRect().bottom')
                assert safe_gap==28
                receipts.append({'simulated_safe_area':{'top':20,'bottom':28,'composer_gap':safe_gap}})
                assert not errors, errors
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    print(json.dumps(receipts,indent=2))


if __name__ == '__main__':
    main()

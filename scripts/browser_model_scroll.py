"""Real Chromium, disposable authenticated backend and explicit protocol fixture."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PROGRAM = '''
import asyncio,json,sys,time
sys.path.insert(0,'tests')
import uvicorn
from test_claude_provider import BothServer
from ai_manager.config import Settings
from ai_manager.web import create_app
class Server(BothServer):
    async def request(self,method,params,timeout=30):
        if method!='turn/start': return {}
        queue=self.queues[params['threadId']]
        async def emit():
            for index in range(8):
                await asyncio.sleep(.15)
                queue.put_nowait({'method':'item/agentMessage/delta','params':{'turnId':'stream','delta':('streaming line '+str(index)+'\\n')*12}})
            queue.put_nowait({'method':'turn/completed','params':{'turn':{'id':'stream','status':'completed'}}})
        asyncio.create_task(emit())
        return {'turn':{'id':'stream'}}
s=Settings();app=create_app(s,Server(s),Server)
conversation=app.state.chat.conversation()
with app.state.db.transaction() as conn:
    for index in range(35):
        conn.execute("INSERT INTO messages(id,conversation_id,role,content,status,created_at) VALUES(?,?,'assistant',?,'completed',?)",(str(index),conversation['id'],('Reading message '+str(index)+'\\n')*12,time.time()))
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error')
'''


def main():
    evidence = Path(os.environ.get('LINKO_MODEL_EVIDENCE', '/tmp/linko-model-scroll-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='linko-model-scroll-') as directory:
        root = Path(directory)
        (root / 'projects').mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
        process = subprocess.Popen([os.sys.executable, '-c', PROGRAM.replace('PORT', str(port))], env=env)
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
                page = browser.new_page(viewport={'width': 1200, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history?.messages.length===35')
                assert page.locator('#messages').evaluate('(n)=>Math.abs(n.scrollTop+n.clientHeight-n.scrollHeight)<=1')
                assert page.locator('#messages').evaluate('(n)=>getComputedStyle(n).overflowAnchor') == 'none'
                assert page.locator('#model-settings-button').count() == 1
                page.locator('#model-settings-button').click()
                page.locator('#model-dialog').wait_for(state='visible')
                assert page.locator('#manager-choices > div').count() == 4
                assert page.locator('#model-dialog svg').count() == 0
                assert page.locator('.favorite-row').count() == 2
                def choice(key, error=None):
                    page.locator(f'[data-choice="{key}"]').click()
                    page.wait_for_function('()=>!modelSaving')
                    message = page.locator('#model-error').inner_text()
                    assert (error in message) if error else message == '', message
                def settings():
                    return page.evaluate('()=>modelPanel.providers[modelPanel.provider].settings')
                def bot_snapshot():
                    return page.evaluate('()=>Object.fromEntries(Object.entries(modelPanel.providers).map(([p,v])=>[p,v.settings.bot]))')
                def pressed(key):
                    return page.locator(f'[data-choice="{key}"]').get_attribute('aria-pressed') == 'true'
                choice('manager-Astra')
                choice('manager-codex-high')
                choice('favorite-insert-0')
                choice('bot-model-Luna')
                choice('bot-manual-high')
                manual = settings()['bot']['manual']
                choice('bot-automatic')
                choice('policy-Luna-low')
                choice('policy-Luna-medium')
                choice('bot-model-Sol')
                choice('bot-model-Astra')
                choice('policy-Astra-medium')
                choice('policy-Astra-high')
                config = settings()
                assert config['bot']['policy']['gpt-6-luna']['levels'] == {'low': False, 'medium': False, 'high': True}
                assert config['bot']['policy']['gpt-6-astra']['levels'] == {'low': True, 'medium': False, 'high': False}
                choice('bot-model-Astra')
                choice('bot-model-Luna', 'at least one')
                assert pressed('bot-model-Luna')
                choice('policy-Luna-high', 'at least one')
                assert pressed('policy-Luna-high')
                choice('bot-model-Sol')
                assert pressed('bot-model-Sol')
                choice('bot-model-Sol')
                assert not pressed('bot-model-Sol')
                # The saved API policy is the one runtime delegation validates.
                policy = page.evaluate("async()=> (await api('/api/model-settings')).settings.bot.policy")
                assert policy == settings()['bot']['policy']
                choice('bot-manual')
                assert settings()['bot']['manual'] == manual
                assert pressed('bot-model-Luna') and pressed('bot-manual-high')
                bots = bot_snapshot()
                choice('manager-Sonnet')
                choice('manager-claude-medium')
                choice('favorite-insert-1')
                choice('favorite-recall-0')
                assert pressed('manager-Astra') and pressed('manager-codex-high')
                assert bot_snapshot() == bots
                choice('favorite-recall-1')
                assert pressed('manager-Sonnet') and pressed('manager-claude-medium')
                assert bot_snapshot() == bots
                choice('manager-Haiku')
                choice('favorite-insert-1')
                assert page.locator('[data-choice="favorite-recall-1"]').text_content() == 'MediumHaiku'
                page.keyboard.press('Escape')
                assert page.locator('#model-settings-button').evaluate('(n)=>n===document.activeElement')
                page.reload()
                page.wait_for_function('()=>state.ready&&state.history')
                page.locator('#model-settings-button').click()
                choice('favorite-recall-1')
                assert pressed('manager-Haiku') and pressed('manager-claude-medium')
                choice('favorite-recall-0')
                assert pressed('manager-Astra') and pressed('manager-codex-high')
                assert bot_snapshot() == bots
                choice('bot-automatic')
                for width, height in [(1200, 900), (393, 851), (320, 568)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    geometry = page.locator('#model-dialog').evaluate('(n)=>({width:n.clientWidth,scrollWidth:n.scrollWidth,height:n.clientHeight,scrollHeight:n.scrollHeight})')
                    assert geometry['scrollWidth'] <= geometry['width']
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    # Compare exactly the selected and nonselected effort surfaces, including hover.
                    style = page.evaluate("""() => {
                        const surface=b=>{const s=getComputedStyle(b);return [s.backgroundColor,s.backgroundImage,s.borderColor,s.boxShadow,s.fontWeight]};
                        const selected=document.querySelector('[data-choice="manager-codex-high"]');
                        const other=document.querySelector('[data-choice="manager-codex-low"]');
                        const h=document.querySelector('#manager-heading').getBoundingClientRect();
                        const x=document.querySelector('#model-close').getBoundingClientRect();
                        const bh=document.querySelector('#bot-heading').getBoundingClientRect();
                        const mode=document.querySelector('#bot-mode').getBoundingClientRect();
                        return {selected:surface(selected),other:surface(other),color:getComputedStyle(selected).color,
                          otherColor:getComputedStyle(other).color,headerAligned:Math.abs(h.y+h.height/2-x.y-x.height/2)<2,
                          botAligned:Math.abs(bh.y+bh.height/2-mode.y-mode.height/2)<2,
                          labels:[...document.querySelectorAll('#model-dialog h2')].map(n=>getComputedStyle(n).color)};
                    }""")
                    assert style['selected'] == style['other']
                    assert style['color'] != style['otherColor']
                    assert style['headerAligned'] and style['botAligned']
                    assert len(set(style['labels'])) == 1
                    assert page.locator('.policy-cell').count() == 0
                    assert page.locator('#model-dialog select').count() == 0
                    for row in page.locator('.favorite-row').all():
                        assert row.locator('button').first.inner_text() == 'Insert'
                    page.screenshot(path=str(evidence / f'panel-{width}.png'))
                    page.locator('#model-close').focus()
                    page.keyboard.press('Shift+Tab')
                    assert page.locator('#model-dialog').evaluate('(n)=>n.contains(document.activeElement)')
                    page.keyboard.press('Tab')
                    assert page.locator('#model-close').evaluate('(n)=>n===document.activeElement')
                    results.append({'width': width, 'panel': geometry, 'style': style})
                page.locator('[data-choice="manager-Luna"]').focus()
                page.keyboard.press('ArrowRight')
                assert page.locator('[data-choice="manager-Sol"]').evaluate('(n)=>n===document.activeElement')
                assert page.locator('[data-choice="manager-Sol"]').evaluate('(n)=>getComputedStyle(n).outlineStyle') == 'solid'
                page.keyboard.press('Enter')
                page.wait_for_function('()=>!modelSaving')
                assert pressed('manager-Sol')
                page.keyboard.press('Escape')
                # At top, middle, and old bottom: sends, streams and reconciliation cannot follow.
                for width,height,position in [(w,h,p) for w,h in [(1200,900),(393,851),(320,568)] for p in ['top','middle','bottom']]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.locator('#messages').evaluate('(n,p)=>n.scrollTop=p==="top"?0:p==="middle"?800:n.scrollHeight', position)
                    before = page.locator('#messages').evaluate('(n)=>n.scrollTop')
                    page.locator('#message-input').fill('Viewport ' + position)
                    page.locator('#message-input').focus()
                    page.locator('#send').click()
                    page.wait_for_function('()=>!state.pending.size&&state.execution')
                    samples = []
                    for _ in range(12):
                        samples.append(page.locator('#messages').evaluate('(n)=>n.scrollTop'))
                        page.wait_for_timeout(150)
                    page.wait_for_function('()=>!state.execution')
                    page.evaluate('async()=>{await loadHistory(state.selection);await refresh();}')
                    samples.append(page.locator('#messages').evaluate('(n)=>n.scrollTop'))
                    assert all(abs(value-before) <= 1 for value in samples), (position, before, samples)
                    results.append({'viewport':[width,height],'position': position, 'before': before, 'samples': samples})
                # The API tail window dropping old rows is not a deletion request.
                snapshot=page.evaluate('state.history')
                snapshot['messages']=snapshot['messages'][5:]
                count=page.locator('#messages [data-message]').count()
                page.route('**/api/history?*',lambda route:route.fulfill(json=snapshot))
                page.evaluate('()=>loadHistory(state.selection)')
                assert page.locator('#messages [data-message]').count()==count
                page.unroute('**/api/history?*')
                results.append({'rolling_tail_retained_rows':count})
                # A full Markdown rebuild near the bottom used to clamp the offset.
                page.locator('#messages').evaluate('(n)=>n.scrollTop=n.scrollHeight')
                before=page.locator('#messages').evaluate('(n)=>n.scrollTop')
                page.evaluate("()=>{const n=document.querySelector('.message-content');updateMessageContent(n,n._messageSource+' extra','assistant');}")
                assert abs(page.locator('#messages').evaluate('(n)=>n.scrollTop')-before)<=1
                page.evaluate("()=>{showView('settings');showView('chat');}")
                assert abs(page.locator('#messages').evaluate('(n)=>n.scrollTop')-before)<=1
                results.append({'markdown_rebuild_and_view_return':before})
                # User scrolling continues normally; mutations above the viewport don't anchor.
                page.locator('#messages').evaluate('(n)=>n.scrollTop=800')
                page.locator('#messages').hover()
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(200)
                assert page.locator('#messages').evaluate('(n)=>n.scrollTop') > 800
                before = page.locator('#messages').evaluate('(n)=>n.scrollTop')
                page.evaluate('()=>updateMessageContent(document.querySelector(".message-content"),"Expanded above viewport\\n".repeat(100),"assistant")')
                assert page.locator('#messages').evaluate('(n)=>n.scrollTop') == before
                assert not errors, errors
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    (evidence / 'browser-results.json').write_text(json.dumps(results, indent=2))
    print('Settings persistence/policy/mobile/keyboard and manual scroll during sends/streams/polling passed')


if __name__ == '__main__':
    main()

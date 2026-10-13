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
from test_model_settings import CatalogServer
from ai_manager.config import Settings
from ai_manager.web import create_app
class Server(CatalogServer):
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
                assert page.locator('#messages').evaluate('(n)=>n.scrollTop') == 0
                assert page.locator('#messages').evaluate('(n)=>getComputedStyle(n).overflowAnchor') == 'none'
                assert page.locator('#model-settings-button').count() == 1
                page.locator('#model-settings-button').click()
                page.locator('#model-dialog').wait_for(state='visible')
                assert page.locator('#manager-choices > div').count() == 2
                assert page.locator('.bot-model-section').count() == 3
                assert page.locator('.policy-cell').count() == 12
                def choice(key):
                    page.locator(f'[data-choice="{key}"]').click()
                    page.wait_for_function('()=>!modelSaving')
                    assert page.locator('#model-error').inner_text() == ''
                choice('manager-Astra')
                choice('manager-high')
                choice('manual-Luna-high')
                choice('bot-automatic')
                assert page.locator('[data-choice="manual-Luna-high"]').is_disabled()
                assert page.locator('[data-choice="manual-Luna-high"]').get_attribute('aria-pressed') == 'true'
                choice('policy-Luna-low-Deny')
                choice('policy-Luna-medium-Deny')
                choice('policy-Sol-model-Deny')
                choice('policy-Astra-model-Allow')
                choice('policy-Astra-medium-Deny')
                choice('policy-Astra-high-Deny')
                settings = page.evaluate('()=>modelPanel.settings')
                assert settings['bot']['policy']['gpt-6-luna']['levels'] == {'low': False, 'medium': False, 'high': True}
                assert settings['bot']['policy']['gpt-6-astra']['levels'] == {'low': True, 'medium': False, 'high': False}
                page.screenshot(path=str(evidence / 'desktop-automatic.png'))
                choice('policy-Astra-model-Deny')
                page.locator('[data-choice="policy-Luna-high-Deny"]').click()
                page.wait_for_function('()=>!modelSaving')
                assert 'at least one' in page.locator('#model-error').inner_text()
                assert page.locator('[data-choice="policy-Luna-high-Allow"]').get_attribute('aria-pressed') == 'true'
                choice('bot-manual')
                assert page.locator('[data-choice="manual-Luna-high"]').is_enabled()
                assert page.locator('[data-choice="manual-Luna-high"]').get_attribute('aria-pressed') == 'true'
                page.keyboard.press('Escape')
                assert page.locator('#model-settings-button').evaluate('(n)=>n===document.activeElement')
                page.reload()
                page.wait_for_function('()=>state.ready&&state.history')
                page.locator('#model-settings-button').click()
                assert page.locator('[data-choice="manager-Astra"]').get_attribute('aria-pressed') == 'true'
                assert page.locator('[data-choice="manual-Luna-high"]').get_attribute('aria-pressed') == 'true'
                choice('bot-automatic')
                for width, height in [(1200, 900), (393, 851), (320, 568)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    geometry = page.locator('#model-dialog').evaluate('(n)=>({width:n.clientWidth,scrollWidth:n.scrollWidth,height:n.clientHeight,scrollHeight:n.scrollHeight})')
                    assert geometry['scrollWidth'] <= geometry['width']
                    assert geometry['scrollHeight'] <= geometry['height']  # All controls visible at once.
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    style = page.evaluate("""() => ({families:[...document.querySelectorAll('.model-family')].map(b=>({
                        image:getComputedStyle(b).backgroundImage,pressed:b.getAttribute('aria-pressed')})),
                        efforts:[...document.querySelectorAll('[data-choice="manager-low"],[data-choice="manual-Sol-low"]')].map(b=>getComputedStyle(b).backgroundImage)})""")
                    assert len(style['families']) == 6
                    assert all('linear-gradient' in b['image'] for b in style['families'])
                    assert all(image=='none' for image in style['efforts'])
                    results.append({'family_button_style':style,'viewport':width})
                    page.screenshot(path=str(evidence / f'panel-{width}.png'))
                    # Native modal focus containment, plus explicit first/last wrapping.
                    page.locator('#model-close').focus()
                    page.keyboard.press('Shift+Tab')
                    assert page.locator('#model-dialog').evaluate('(n)=>n.contains(document.activeElement)')
                    page.keyboard.press('Tab')
                    assert page.locator('#model-close').evaluate('(n)=>n===document.activeElement')
                    results.append({'width': width, 'panel': geometry})
                page.locator('[data-choice="manager-Luna"]').focus()
                page.keyboard.press('ArrowRight')
                assert page.locator('[data-choice="manager-Sol"]').evaluate('(n)=>n===document.activeElement')
                page.keyboard.press('Escape')
                # At top, middle, and old bottom: sends, streams and reconciliation cannot follow.
                for position in ['top', 'middle', 'bottom']:
                    page.locator('#messages').evaluate('(n,p)=>n.scrollTop=p==="top"?0:p==="middle"?800:n.scrollHeight', position)
                    before = page.locator('#messages').evaluate('(n)=>n.scrollTop')
                    page.locator('#message-input').fill('Viewport ' + position)
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
                    results.append({'position': position, 'before': before, 'samples': samples})
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

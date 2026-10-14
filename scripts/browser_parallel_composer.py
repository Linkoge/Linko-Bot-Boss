"""Authenticated Chromium permission/layout/recording flow with fixture STT only."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from browser_audio import PROGRAM
from playwright.sync_api import sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_EVIDENCE_DIR', '/tmp/linko-parallel-composer-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linko-parallel-browser-') as directory:
        root = Path(directory)
        for name in ('Alpha', 'Beta'):
            (root/'projects'/name).mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'AI_MANAGER_DATA': str(root/'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root/'projects'),
               'AI_MANAGER_SECURE_COOKIE': '0'}
        fixture = """from ai_manager.tasks import Tasks
tasks=Tasks(app.state.db)
project=next(p for p in app.state.registry.list() if p['name']=='Alpha')
task=tasks.add(project['id'],'Retained unfinished coding work')
tasks.control(task['id'],'pause',task['revision'])
"""
        program = PROGRAM.replace('PORT', str(port)).replace('uvicorn.run(', fixture+'uvicorn.run(').replace("return 'spoken transcript'", "import asyncio\n        await asyncio.sleep(.3)\n        return 'spoken transcript'")
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(url+'/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
                page = browser.new_page(viewport={'width':393,'height':851}, is_mobile=True, has_touch=True)
                errors, requests, geometry = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: requests.append(req.post_data_json) if req.url.endswith('/api/chat') else None)
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history&&!audioState.loading')
                page.evaluate("()=>selectProject(state.portfolio.projects.find(p=>p.name==='Alpha').id)")
                page.wait_for_function("()=>projectName(state.project)==='Alpha'&&state.history&&state.portfolio.tasks.length")
                field = page.locator('#message-input')
                assert page.locator('#parallel-send').is_hidden()
                field.fill('Visible draft with keyboard dismissed')
                page.locator('#chat-menu-button').focus()
                page.wait_for_function("()=>!$('send').hidden&&!$('parallel-send').hidden")
                assert field.input_value() == 'Visible draft with keyboard dismissed'
                # Keyboard closure with retained focused draft; then genuine blur.
                page.evaluate("window.keyboardViewport=new EventTarget();Object.assign(keyboardViewport,{height:851,scale:1,offsetTop:0});Object.defineProperty(window,'visualViewport',{configurable:true,value:keyboardViewport});keyboardViewport.addEventListener('resize',updateViewport);updateViewport()")
                field.focus()
                page.evaluate("keyboardViewport.height=420;keyboardViewport.dispatchEvent(new Event('resize'))")
                page.evaluate("keyboardViewport.height=851;keyboardViewport.dispatchEvent(new Event('resize'))")
                assert page.locator('#send').is_visible() and page.locator('#parallel-send').is_visible()
                page.locator('#chat-menu-button').focus()
                page.wait_for_function("()=>$('chat-form').classList.contains('composer-active')")
                assert field.input_value() == 'Visible draft with keyboard dismissed'
                page.evaluate('delete window.visualViewport;updateViewport()')
                for width, height in ((1440,1000),(393,851),(320,420)):
                    page.set_viewport_size({'width':width,'height':height})
                    field.fill('long line\n'*40)
                    page.locator('#microphone').click()
                    page.wait_for_function("()=>audioState.session?.phase==='recording'")
                    boxes = {name:page.locator('#'+name).bounding_box() for name in ('attach','microphone','send','parallel-send','audio-status','audio-upload','audio-cancel','message-input','chat-form')}
                    for name in ('attach','microphone','send','parallel-send','audio-upload','audio-cancel'):
                        assert boxes[name]['height'] >= 44 and boxes[name]['width'] >= 44
                        assert page.evaluate("id=>{const n=$(id),b=n.getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)===n}",name)
                    assert boxes['attach']['x'] == boxes['microphone']['x'] == boxes['send']['x']
                    assert boxes['attach']['y'] < boxes['microphone']['y'] < boxes['send']['y']
                    assert abs(boxes['send']['y'] - boxes['parallel-send']['y']) < 1
                    assert boxes['parallel-send']['x']+boxes['parallel-send']['width'] <= boxes['send']['x']
                    assert boxes['audio-status']['x'] < boxes['audio-upload']['x'] < boxes['audio-cancel']['x'] < boxes['parallel-send']['x']
                    assert abs(boxes['audio-upload']['y'] - boxes['send']['y']) < 1
                    assert boxes['message-input']['y']+boxes['message-input']['height'] <= boxes['audio-upload']['y']
                    assert boxes['message-input']['height'] >= 80  # 140px narrow/short stack minus one 44px row
                    assert boxes['chat-form']['y']+boxes['chat-form']['height'] <= height+1
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(evidence/f'recording-parallel-{width}.png'))
                    geometry.append({'viewport':[width,height],'boxes':boxes})
                    page.locator('#audio-cancel').click()
                    assert page.locator('#send').is_visible()  # nonempty blurred draft survives discard
                page.set_viewport_size({'width':393,'height':851})
                field.fill('First opt in')
                page.locator('#parallel-send').click()
                page.wait_for_function('()=>!state.pending.size&&!submissions.size&&!state.execution')
                assert len(requests) == 1 and requests[-1]['parallel'] is True
                field.fill('Next ordinary request')
                page.locator('#send').click()
                page.wait_for_function('()=>!state.pending.size&&!submissions.size&&!state.execution')
                assert len(requests) == 2 and requests[-1]['parallel'] is False
                field.fill('Audio opt in')
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='recording'")
                page.wait_for_timeout(650)
                page.locator('#parallel-send').click()
                assert page.locator('#parallel-send').is_disabled()
                page.wait_for_function('()=>!audioState.session&&!state.pending.size&&!submissions.size&&!state.execution',timeout=15000)
                assert len(requests) == 3 and requests[-1]['parallel'] is True
                assert requests[-1]['content'] == 'Audio opt in\n\nspoken transcript'
                provider = json.loads((root/'data/audio-provider-last.json').read_text())
                assert provider['data']  # full captured Blob reached fixture provider
                # Interrupted/recovered recording retains retry/discard/download controls and no Send permission.
                field.fill('Keep draft')
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='recording'")
                page.wait_for_timeout(450)
                page.evaluate("interruptAudio(audioState.session,'Synthetic capture interruption')")
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert page.locator('#audio-cancel').is_visible() and page.locator('#audio-download').is_visible()
                assert page.locator('#send').is_visible() and page.locator('#parallel-send').is_disabled()
                page.locator('#audio-cancel').click()
                # No persistent toggle and no control in unrelated/empty-idle/read-only contexts.
                field.fill('')
                page.locator('#chat-menu-button').focus()
                page.wait_for_function("()=>$('send').hidden&&$('parallel-send').hidden")
                page.evaluate("()=>selectProject(state.portfolio.projects.find(p=>p.name==='Beta').id)")
                page.wait_for_function("()=>projectName(state.project)==='Beta'&&state.history")
                field.fill('Other project draft')
                assert page.locator('#parallel-send').is_hidden() and page.locator('#send').is_visible()
                assert not errors, errors
                browser.close()
                (evidence/'browser-receipt.json').write_text(json.dumps({'geometry':geometry,'permission_requests':[{'parallel':r['parallel'],'request_id':r['request_id']} for r in requests],'errors':errors,'provider':'fixture STT, fake microphone; no paid inference or physical keyboard'},indent=2))
                print('Parallel composer: draft/blur/keyboard, 1440/393/320px layout and hit targets, per-request default/opt-in, whole-Blob audio single submission passed')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

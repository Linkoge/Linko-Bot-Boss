"""Actual Chromium capture/probe with an explicit fake STT provider, disposable data only."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PROGRAM = """
import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
class AudioProvider:
    async def transcribe(self,data,mime):
        return 'spoken transcript'
s=Settings(xai_api_key='disposable-test-key');app=create_app(s,FakeServer(s))
app.state.audio.provider=AudioProvider()
for name in ('Alpha','Beta'): app.state.registry.add(s.project_roots[0]/name)
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error')
"""


def main():
    with tempfile.TemporaryDirectory(prefix='linko-audio-browser-') as directory:
        root = Path(directory)
        for name in ('Alpha', 'Beta'):
            (root / 'projects' / name).mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'AI_MANAGER_DATA': str(root/'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root/'projects'),
               'AI_MANAGER_SECURE_COOKIE': '0'}
        process = subprocess.Popen([os.sys.executable, '-c', PROGRAM.replace('PORT', str(port))], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(url+'/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                errors, chats, uploads = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: chats.append(request.post_data_json) if request.url.endswith('/api/chat') else None)
                page.on('request', lambda request: uploads.append(request.headers.get('content-type')) if request.url.endswith('/api/audio/transcribe') else None)
                def delayed_audio(route):
                    time.sleep(0.4)
                    route.continue_()
                page.route('**/static/audio.js', delayed_audio)
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.history && state.ready')
                ids = page.evaluate('state.portfolio.projects.map(p=>p.id)')
                alpha, beta = ids

                def switch(project):
                    page.evaluate('(id)=>selectProject(id)', project)
                    page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=project)

                def start():
                    page.locator('#microphone').click()
                    page.wait_for_function("()=>audioState.session?.phase==='recording'")
                    # Capture actual browser audio, not a synthetic MediaRecorder implementation.
                    page.wait_for_function('()=>audioState.session.bytes>0')

                def finish_stop():
                    page.locator('#microphone').click()
                    page.wait_for_function('()=>!audioState.session')

                switch(alpha)
                page.locator('#message-input').fill('typed draft')
                start()
                assert page.locator('#microphone').inner_text() == 'Stop'
                # Enter must not send a recording accidentally.
                page.locator('#message-input').press('Enter')
                assert len(chats) == 0
                finish_stop()
                assert page.locator('#message-input').input_value() == 'typed draft spoken transcript'
                assert len(chats) == 0 and uploads[0].startswith('audio/webm')
                assert page.evaluate("$('microphone').nextElementSibling.id") == 'send'

                # Blank text field still permits explicit recording Send, in the original mode.
                page.locator('#message-input').fill('')
                page.locator('#chat-mode').select_option('conversation')
                start()
                page.locator('#chat-mode').select_option('auto')
                page.locator('#send').click()
                page.wait_for_function("()=>!audioState.session&&!state.pending.size&&!state.execution&&$('message-input').value===''")
                assert len(chats) == 1 and chats[-1]['content'] == 'spoken transcript'
                assert chats[-1]['project_id'] == alpha and chats[-1]['mode'] == 'conversation'
                assert len(chats[-1]['request_id']) >= 16

                # Switch while transcribing: result writes the original draft and never sends.
                page.locator('#message-input').fill('Alpha draft')
                start()
                held = []
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                page.locator('#send').click()
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                switch(beta)
                page.locator('#message-input').fill('Beta draft')
                assert held
                held.pop().fulfill(status=200, json={'text': 'late Alpha text'})
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'Beta draft'
                assert len(chats) == 1
                switch(alpha)
                assert page.locator('#message-input').input_value() == 'Alpha draft late Alpha text'
                page.unroute('**/api/audio/transcribe')

                # Switching while recording ends capture and inserts, with no send.
                start()
                switch(beta)
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'Beta draft'
                switch(alpha)
                assert page.locator('#message-input').input_value().endswith('late Alpha text spoken transcript')
                assert len(chats) == 1

                # Failed explicit Send retains recording. Retry inserts only, without duplicate appends.
                page.route('**/api/audio/transcribe', lambda route: route.fulfill(status=502, json={'detail': 'Provider unavailable'}))
                page.locator('#message-input').fill('preserved')
                start()
                page.locator('#send').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert page.locator('#message-input').input_value() == 'preserved'
                assert page.evaluate('audioState.session.blob.size>0')
                assert page.locator('#send').is_disabled()
                assert page.locator('#audio-download').is_visible()
                page.unroute('**/api/audio/transcribe')
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'preserved spoken transcript'
                assert len(chats) == 1

                # Discard while awaiting a late transcript suppresses all effects.
                start()
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                page.locator('#audio-cancel').click()
                assert held
                held.pop().fulfill(status=200, json={'text': 'discarded text'})
                page.unroute('**/api/audio/transcribe')
                page.wait_for_timeout(200)
                assert page.locator('#message-input').input_value() == 'preserved spoken transcript'
                assert len(chats) == 1

                # Editing after pressing recording Send cancels automatic submission.
                start()
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                page.locator('#send').click()
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                page.locator('#message-input').fill('edited while waiting')
                held.pop().fulfill(status=200, json={'text': 'editable result'})
                page.wait_for_function('()=>!audioState.session')
                page.unroute('**/api/audio/transcribe')
                assert page.locator('#message-input').input_value() == 'edited while waiting editable result'
                assert len(chats) == 1

                # An overlong draft preserves the transcript; shortening and retrying needs no second STT call.
                page.locator('#message-input').fill('x'*23995)
                start()
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert 'text limit' in page.locator('#audio-status').inner_text()
                assert page.evaluate("audioState.session.transcript==='spoken transcript'")
                count=len(uploads)
                page.locator('#message-input').fill('shortened')
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert len(uploads)==count
                assert page.locator('#message-input').input_value()=='shortened spoken transcript'

                # An accepted chat with a lost response retries the exact same request ID.
                dropped=[]
                def lose_receipt(route):
                    response=route.fetch()
                    assert response.status==200
                    dropped.append(route.request.post_data_json)
                    route.abort('failed')
                page.route('**/api/chat', lose_receipt)
                page.locator('#message-input').fill('')
                page.locator('#chat-mode').select_option('conversation')
                start()
                page.locator('#send').click()
                page.wait_for_function('()=>!audioState.session&&!state.pending.size&&submissions.size===1')
                assert dropped and len(chats)==2
                page.unroute('**/api/chat')
                page.evaluate('()=>sendSubmission()')
                page.wait_for_function('()=>!submissions.size&&!state.pending.size&&!state.execution')
                assert len(chats)==3 and chats[-1]['request_id']==chats[-2]['request_id']
                history=page.evaluate('()=>api("/api/history?project_id="+state.project)')
                assert sum(m['role']=='user' for m in history['messages'])==2
                page.locator('#message-input').fill('preserved spoken transcript')

                # Media permission denial is compact and retryable, no draft loss.
                page.evaluate("()=>{window.savedGetUserMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('denied','NotAllowedError')}}")
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert 'permission denied' in page.locator('#audio-status').inner_text()
                page.evaluate('()=>{navigator.mediaDevices.getUserMedia=window.savedGetUserMedia}')
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='recording'")
                page.locator('#audio-cancel').click()
                assert page.locator('#message-input').input_value() == 'preserved spoken transcript'

                # Cancellation while browser permission is pending releases any late granted track.
                page.evaluate("()=>{navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.resolveMic=resolve)}")
                page.locator('#microphone').click()
                page.wait_for_function('()=>!!window.resolveMic')
                page.locator('#audio-cancel').click()
                page.evaluate("async()=>{window.lateStream=await window.savedGetUserMedia({audio:true});window.resolveMic(window.lateStream)}")
                page.wait_for_function("()=>window.lateStream.getTracks().every(track=>track.readyState==='ended')")
                page.evaluate('()=>{navigator.mediaDevices.getUserMedia=window.savedGetUserMedia}')

                # Missing server setup fails before requesting a microphone.
                page.route('**/api/audio', lambda route: route.fulfill(json={'available': False, 'reason': 'Audio needs XAI_API_KEY in the server environment.'}))
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert 'XAI_API_KEY' in page.locator('#audio-status').inner_text()
                assert not page.evaluate('!!audioState.session.stream')
                page.locator('#audio-cancel').click()
                page.unroute('**/api/audio')

                # Three-minute boundary always stops for editing, never implicitly sends.
                start()
                page.evaluate('audioState.session.started=performance.now()-180000')
                page.wait_for_function('()=>!audioState.session')
                assert len(chats) == 3

                for width, height in [(1440, 1000), (393, 851), (320, 420)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    start()
                    bounds = page.locator('#send').bounding_box()
                    assert bounds['y']+bounds['height'] <= height
                    assert bounds['x']+bounds['width'] <= width
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=f'/tmp/linko-audio-{width}.png')
                    page.locator('#audio-cancel').click()
                # Unsupported browser and insecure mobile origins use dismissible compact notices.
                page.evaluate("()=>{window.savedRecorder=window.MediaRecorder;window.MediaRecorder=undefined}")
                page.locator('#microphone').click()
                assert 'does not support' in page.locator('#notice-text').inner_text()
                assert page.locator('#audio-panel').is_hidden()
                page.evaluate('()=>{window.MediaRecorder=window.savedRecorder;Object.defineProperty(window,"isSecureContext",{value:false,configurable:true})}')
                page.locator('#microphone').click()
                assert 'HTTPS' in page.locator('#notice-text').inner_text()
                assert not errors, errors
                print(json.dumps({'result': 'passed', 'real_capture': True, 'provider': 'fixture only', 'uploads': len(uploads), 'chat_submissions': len(chats), 'viewports': [1440, 393, 320]}))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

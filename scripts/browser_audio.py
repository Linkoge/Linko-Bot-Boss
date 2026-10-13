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
    async def transcribe(self,data,mime,filename=None):
        import json
        (s.data_dir/'audio-provider-last.json').write_text(json.dumps({'data':data.hex(),'mime':mime,'filename':filename}))
        return 'spoken transcript'
class Server(FakeServer):
    async def request(self,method,params,timeout=30):
        import asyncio, json
        if method=='turn/start' and json.loads(params['input'][0]['text'])['message']=='Busy recording':
            queue=self.queues[params['threadId']]
            async def finish():
                await asyncio.sleep(5)
                queue.put_nowait({'method':'turn/completed','params':{'turn':{'id':'busy','status':'completed'}}})
            asyncio.create_task(finish())
            return {'turn':{'id':'busy'}}
        return await super().request(method,params,timeout)
s=Settings(xai_api_key='disposable-test-key');app=create_app(s,Server(s))
app.state.audio.provider=AudioProvider()
for name in ('Alpha','Beta'): app.state.registry.add(s.project_roots[0]/name)
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error')
"""


def main():
    evidence = Path(os.environ.get('LINKO_AUDIO_EVIDENCE', '/tmp/linko-audio-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
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
                errors, chats, uploads, interrupts = [], [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: interrupts.append(request.url) if request.url.endswith('/interrupt') else None)
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
                    assert page.locator('#audio-status').inner_text() == '00:00'
                    page.wait_for_function("()=>$('audio-status').textContent==='00:01'")
                    # Capture actual browser audio, not a synthetic MediaRecorder implementation.
                    page.wait_for_function('()=>audioState.session.bytes>0')

                def finish_stop():
                    page.locator('#microphone').click()
                    page.wait_for_function('()=>!audioState.session')

                switch(alpha)
                page.locator('#message-input').fill('typed draft')
                start()
                assert page.locator('#microphone').inner_text() == '■'
                # Enter must not send a recording accidentally.
                page.locator('#message-input').press('Enter')
                assert len(chats) == 0
                finish_stop()
                assert page.locator('#message-input').input_value() == 'typed draft\nspoken transcript'
                assert len(chats) == 0 and uploads[0].startswith('audio/webm')
                assert page.evaluate("$('send').nextElementSibling.id") == 'microphone'

                # Capture and explicit whole-file Send while a real fixture manager turn is active.
                page.locator('#message-input').fill('Busy recording')
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>!!state.execution&&!state.pending.size')
                assert page.locator('#microphone').is_enabled()
                start()
                assert page.evaluate('!!state.execution')
                assert page.locator('#audio-stop').count() == 0
                before_chats = len(chats)
                with page.expect_file_chooser():
                    page.locator('#audio-upload').click()
                page.locator('#audio-file-input').dispatch_event('cancel')
                page.wait_for_timeout(100)
                assert len(chats) == before_chats and not interrupts
                assert page.evaluate("audioState.session.phase==='recording'&&!!state.execution")
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>!audioState.session&&!state.pending.size')
                assert len(chats) == 2 and chats[-1]['content'] == 'spoken transcript'
                page.wait_for_function("()=>state.history.messages.at(-1).status==='completed'&&!state.execution")
                # Keep the original scenario's independent request-count assertions.
                del chats[-2:]

                # Blank text field still permits explicit recording Send, in the original mode.
                page.locator('#message-input').fill('')
                page.locator('#chat-menu-button').click()
                page.locator('[data-mode=conversation]').click()
                start()
                page.locator('#chat-menu-button').click()
                page.locator('[data-mode=auto]').click()
                page.locator('#message-input').focus()
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
                page.locator('#message-input').focus()
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
                assert page.locator('#message-input').input_value() == 'Alpha draft\n\nlate Alpha text'
                page.unroute('**/api/audio/transcribe')

                # Switching while recording ends capture and inserts, with no send.
                start()
                switch(beta)
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'Beta draft'
                switch(alpha)
                assert page.locator('#message-input').input_value().endswith('late Alpha text\n\nspoken transcript')
                assert len(chats) == 1

                # Failed explicit Send retains recording. Retry inserts only, without duplicate appends.
                page.route('**/api/audio/transcribe', lambda route: route.fulfill(status=502, json={'detail': 'Provider unavailable'}))
                page.locator('#message-input').fill('preserved')
                start()
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert page.locator('#message-input').input_value() == 'preserved'
                assert page.evaluate('audioState.session.blob.size>0')
                assert page.locator('#send').is_disabled()
                assert page.locator('#audio-download').is_visible()
                page.unroute('**/api/audio/transcribe')
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'preserved\n\nspoken transcript'
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
                assert page.locator('#message-input').input_value() == 'preserved\n\nspoken transcript'
                assert len(chats) == 1

                # Editing after pressing recording Send cancels automatic submission.
                start()
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                page.locator('#message-input').fill('edited while waiting')
                held.pop().fulfill(status=200, json={'text': 'editable result'})
                page.wait_for_function('()=>!audioState.session')
                page.unroute('**/api/audio/transcribe')
                assert page.locator('#message-input').input_value() == 'edited while waiting\n\neditable result'
                assert len(chats) == 1

                # An overlong draft preserves the transcript; shortening and retrying needs no second STT call.
                page.locator('#message-input').fill('x'*(page.locator('#message-input').evaluate('(el)=>el.maxLength')-5))
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
                assert page.locator('#message-input').input_value()=='shortened\n\nspoken transcript'

                # An accepted chat with a lost response retries the exact same request ID.
                dropped=[]
                def lose_receipt(route):
                    response=route.fetch()
                    assert response.status==200
                    dropped.append(route.request.post_data_json)
                    route.abort('failed')
                page.route('**/api/chat', lose_receipt)
                page.locator('#message-input').fill('')
                page.locator('#chat-menu-button').click()
                page.locator('[data-mode=conversation]').click()
                start()
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>!audioState.session&&!state.pending.size&&submissions.size===1')
                assert dropped and len(chats)==2
                page.unroute('**/api/chat')
                page.evaluate('()=>sendSubmission()')
                page.wait_for_function('()=>!submissions.size&&!state.pending.size&&!state.execution')
                assert len(chats)==3 and chats[-1]['request_id']==chats[-2]['request_id']
                history=page.evaluate('()=>api("/api/history?project_id="+state.project)')
                assert sum(m['role']=='user' for m in history['messages'])==4
                page.locator('#message-input').fill('preserved\n\nspoken transcript')

                # Native Upload activation, cancellation and replacement of real capture.
                page.locator('#message-input').fill('replacement draft')
                page.locator('#attachment-input').set_input_files({'name': 'keep.txt', 'mimeType': 'text/plain', 'buffer': b'keep attachment'})
                page.wait_for_function('()=>attachmentDraft(state.project).length===1')
                attachment_ids = page.evaluate('attachmentDraft(state.project).map(f=>f.id)')
                start()
                page.locator('#message-input').focus()
                page.locator('#message-input').evaluate('(el)=>el.setSelectionRange(3,8)')
                original = page.evaluate('audioState.session.id')
                before = len(uploads)
                with page.expect_file_chooser() as chooser:
                    page.locator('#audio-upload').click()
                assert page.evaluate('audioState.session.id') == original
                # Native cancel event and empty change both retain the exact capture.
                page.locator('#audio-file-input').dispatch_event('cancel')
                assert page.evaluate("audioState.session.phase==='recording'&&document.activeElement===$('message-input')")
                assert page.evaluate("$('message-input').selectionStart===3&&$('message-input').selectionEnd===8")
                chooser.value.set_files([])
                assert page.evaluate('audioState.session.id') == original
                assert len(uploads) == before
                # Save the actual raw WebM chunks and all queued recorder callbacks.
                raw = bytes(page.evaluate("async()=>{window.replaced=audioState.session;window.lateData=replaced.recorder.ondataavailable;window.lateStop=replaced.recorder.onstop;window.lateMute=replaced.stream.getTracks()[0].onmute;return Array.from(new Uint8Array(await new Blob(replaced.chunks,{type:replaced.recorder.mimeType}).arrayBuffer()))}"))
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                with page.expect_file_chooser() as chooser:
                    page.locator('#audio-upload').click()
                chooser.value.set_files({'name': 'recording.webm', 'mimeType': '', 'buffer': raw})
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                assert page.evaluate("audioState.session.id!==window.replaced.id&&window.replaced.recorder.state==='inactive'&&window.replaced.stream.getTracks().every(t=>t.readyState==='ended')")
                assert len(held) == 1 and len(uploads) == before + 1
                assert held[0].request.post_data_buffer == raw
                assert held[0].request.headers['content-type'] == page.evaluate("audioState.session.blob.type||'application/octet-stream'")
                assert held[0].request.headers['x-audio-filename'] == 'recording.webm'
                page.evaluate("()=>{lateData({data:new Blob(['stale bytes'])});lateStop();lateMute();}")
                assert page.evaluate('audioState.session.blob.size') == len(raw)
                held.pop().fulfill(status=200, json={'text': 'selected file transcript'})
                page.wait_for_function('()=>!audioState.session')
                page.unroute('**/api/audio/transcribe')
                assert len(uploads) == before + 1
                assert page.locator('#message-input').input_value() == 'replacement draft\n\nselected file transcript'
                assert page.evaluate('attachmentDraft(state.project).map(f=>f.id)') == attachment_ids
                assert len(chats) == 3
                # A subsequent recording appends once after the uploaded transcript.
                start()
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value() == 'replacement draft\n\nselected file transcript\n\nspoken transcript'
                # Selection while a picker-open recording is finishing fences late STT.
                start()
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                with page.expect_file_chooser() as chooser:
                    page.locator('#audio-upload').click()
                page.evaluate('finishAudio(false)')
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                assert len(held) == 1
                chooser.value.set_files({'name': 'saved.webm', 'mimeType': 'audio/webm', 'buffer': raw})
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'&&audioState.session.blob.size===%d" % len(raw))
                page.wait_for_timeout(100)
                assert len(held) == 2
                held.pop(0).fulfill(status=200, json={'text': 'stale microphone transcript'})
                held.pop(0).fulfill(status=200, json={'text': 'second file transcript'})
                page.wait_for_function('()=>!audioState.session')
                page.unroute('**/api/audio/transcribe')
                assert page.locator('#message-input').input_value() == 'replacement draft\n\nselected file transcript\n\nspoken transcript\n\nsecond file transcript'
                # A changed session while picker is open must not discard its replacement.
                start()
                with page.expect_file_chooser() as chooser:
                    page.locator('#audio-upload').click()
                page.locator('#audio-cancel').click()
                start()
                newer = page.evaluate('audioState.session.id')
                before = len(uploads)
                chooser.value.set_files({'name': 'saved.webm', 'mimeType': 'audio/webm', 'buffer': raw})
                assert page.evaluate('audioState.session.id') == newer
                assert len(uploads) == before
                page.locator('#audio-cancel').click()

                # Picker has no format filter. Unknown/mismatched MIME and opaque
                # bytes reach the backend fixture; every transcript remains a draft.
                assert page.locator('#audio-file-input').get_attribute('accept') is None
                for name, mime in [('clip.xyz', ''), ('clip.dat', 'application/octet-stream'), ('clip.wav', 'text/plain'), ('clip.flac', 'audio/flac')]:
                    start()
                    before = len(uploads)
                    with page.expect_file_chooser() as chooser:
                        page.locator('#audio-upload').click()
                    page.evaluate('([name,mime])=>{const transfer=new DataTransfer();transfer.items.add(new File(["opaque uploaded bytes"],name,{type:mime}));$("audio-file-input").files=transfer.files;$("audio-file-input").dispatchEvent(new Event("change"));}', [name,mime])
                    page.wait_for_function('()=>!audioState.session')
                    assert len(uploads) == before + 1
                    assert uploads[-1] == (mime or 'application/octet-stream')
                    received=json.loads((root/'data/audio-provider-last.json').read_text())
                    assert received == {'data': b'opaque uploaded bytes'.hex(), 'mime': mime or 'application/octet-stream', 'filename': name}
                    assert page.locator('#message-input').input_value().endswith('spoken transcript')

                # Provider rejection retains original file/name for retry/download;
                # retry succeeds without an implicit chat submission.
                start()
                before_chats = len(chats)
                page.route('**/api/audio/transcribe', lambda route: route.fulfill(status=502, json={'detail': 'xAI rejected the audio file (HTTP 415). Its format or contents may not be supported.'}))
                with page.expect_file_chooser() as chooser:
                    page.locator('#audio-upload').click()
                chooser.value.set_files({'name': 'rejected.xyz', 'mimeType': 'text/plain', 'buffer': b'provider rejected bytes'})
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert 'xAI rejected the audio file' in page.locator('#audio-status').inner_text()
                assert page.evaluate('audioState.session.filename') == 'rejected.xyz'
                assert bytes(page.evaluate('async()=>Array.from(new Uint8Array(await audioState.session.blob.arrayBuffer()))')) == b'provider rejected bytes'
                assert page.locator('#audio-download').is_visible()
                page.unroute('**/api/audio/transcribe')
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert len(chats) == before_chats

                # Empty/oversized selection still replaces capture and refuses locally.
                for payload in [b'', b'x' * (32 * 1024 * 1024 + 1)]:
                    start()
                    before = len(uploads)
                    with page.expect_file_chooser() as chooser:
                        page.locator('#audio-upload').click()
                    chooser.value.set_files({'name': 'clip.xyz', 'mimeType': 'text/plain', 'buffer': payload})
                    assert page.evaluate('!audioState.session')
                    assert 'must contain data' in page.locator('#notice-text').inner_text()
                    assert len(uploads) == before
                page.evaluate('clearSentAttachments(state.project,attachmentDraft(state.project).map(f=>f.id))')
                page.locator('#message-input').fill('preserved\n\nspoken transcript')

                # Media permission denial is compact and retryable, no draft loss.
                page.evaluate("()=>{window.savedGetUserMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('denied','NotAllowedError')}}")
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='error'")
                assert 'permission denied' in page.locator('#audio-status').inner_text()
                page.evaluate('()=>{navigator.mediaDevices.getUserMedia=window.savedGetUserMedia}')
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='recording'")
                page.locator('#audio-cancel').click()
                assert page.locator('#message-input').input_value() == 'preserved\n\nspoken transcript'

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

                # Twenty-minute boundary always stops for editing, never implicitly sends.
                start()
                assert page.evaluate('audioState.session.config.max_seconds') == 1200
                assert page.locator('#microphone').get_attribute('title') == 'Record microphone'
                before_uploads = len(uploads)
                page.evaluate('audioState.session.started=performance.now()-1199000')
                page.wait_for_function("()=>audioState.session?.message==='19:59'")
                assert len(uploads) == before_uploads
                assert page.locator('#audio-status').inner_text() == '19:59'
                complete = []
                def check_full_upload(route):
                    # Actual final MediaRecorder chunk is included, one whole Blob after stop.
                    data = bytes(page.evaluate('async()=>Array.from(new Uint8Array(await audioState.session.blob.arrayBuffer()))'))
                    assert route.request.post_data_buffer == data
                    complete.append(len(data))
                    route.continue_()
                page.route('**/api/audio/transcribe', check_full_upload)
                page.evaluate('audioState.session.started=performance.now()-1200000')
                page.wait_for_function('()=>!audioState.session')
                page.unroute('**/api/audio/transcribe')
                assert complete and len(uploads) == before_uploads + 1
                assert page.locator('#message-input').input_value() == 'preserved\n\nspoken transcript\n\nspoken transcript'
                assert len(chats) == 3

                page.locator('#notice-dismiss').click()
                for width, height in [(1440, 1000), (393, 851), (320, 420)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    start()
                    bounds = page.locator('#send').bounding_box()
                    assert bounds['y']+bounds['height'] <= height
                    assert bounds['x']+bounds['width'] <= width
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('#message-input').get_attribute('placeholder') is None
                    assert page.locator('#context-hint').count() == 0
                    assert page.locator('.composer-note').count() == 0
                    assert page.locator('#audio-status').inner_text() == '00:01'
                    for control in ['microphone', 'audio-cancel', 'audio-upload', 'send']:
                        assert page.locator('#'+control).is_visible()
                    assert page.locator('#messages').inner_text().strip()
                    boxes = {control: page.locator('#'+control).bounding_box() for control in ['project-menu-button', 'model-settings-button', 'chat-menu-button', 'usage-button']}
                    for box in boxes.values():
                        assert box['width'] == 44 and box['height'] == 44
                    assert boxes['project-menu-button']['x'] == boxes['chat-menu-button']['x']
                    assert boxes['model-settings-button']['x'] == boxes['usage-button']['x']
                    assert boxes['project-menu-button']['y'] == boxes['model-settings-button']['y']
                    assert boxes['chat-menu-button']['y'] == boxes['usage-button']['y']
                    action_boxes = [page.locator('#'+control).bounding_box() for control in ['audio-cancel', 'audio-upload']]
                    assert len({box['y'] for box in action_boxes}) == 1
                    assert all(box['y']+box['height']<=height and box['x']+box['width']<=width for box in action_boxes)
                    page.screenshot(path=str(evidence / f'recording-{width}.png'))
                    page.locator('#audio-cancel').click()
                # Unsupported browser and insecure mobile origins use dismissible compact notices.
                page.evaluate("()=>{window.savedRecorder=window.MediaRecorder;window.MediaRecorder=undefined}")
                page.locator('#microphone').click()
                assert 'does not support' in page.locator('#notice-text').inner_text()
                assert page.locator('#audio-panel').is_hidden()
                page.evaluate('()=>{window.MediaRecorder=window.savedRecorder;Object.defineProperty(window,"isSecureContext",{value:false,configurable:true})}')
                page.locator('#microphone').click()
                assert 'HTTPS' in page.locator('#notice-text').inner_text()
                assert not interrupts, interrupts
                assert page.locator("#audio-stop").count() == 0
                assert not errors, errors
                print(json.dumps({'result': 'passed', 'real_capture': True, 'provider': 'fixture only', 'uploads': len(uploads), 'chat_submissions': len(chats), 'viewports': [1440, 393, 320]}))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

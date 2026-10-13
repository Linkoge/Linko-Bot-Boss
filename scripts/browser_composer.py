"""Real upload/capture; disposable backend and fixture transcription, no paid inference."""
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
    with tempfile.TemporaryDirectory(prefix='linko-composer-browser-') as directory:
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
                time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                errors, chats = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: chats.append(request.post_data_json) if request.url.endswith('/api/chat') else None)
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history&&!audioState.loading')
                # Empty conversation is genuinely empty. Idle/active/overflow geometry.
                assert page.locator('#messages').inner_text() == ''
                assert page.locator('.welcome,.suggestions').count() == 0
                field = page.locator('#message-input')
                assert page.locator('#send').is_hidden()
                assert page.locator('.composer-input').bounding_box()['height'] == 92
                assert field.evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgba(0, 0, 0, 0)'
                page.screenshot(path='/tmp/linko-expanding-idle.png')
                gray = page.locator('#chat-form').evaluate('(n)=>getComputedStyle(n).borderColor')
                field.focus()
                assert page.locator('#send').is_visible()
                assert page.locator('.composer-input').bounding_box()['height'] == 140
                assert field.evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgb(119, 128, 142)'
                assert page.locator('#chat-form').evaluate('(n)=>getComputedStyle(n).borderColor') == gray
                def appearance(selector):
                    return page.locator(selector).evaluate('(n)=>{const s=getComputedStyle(n);return {background:s.backgroundColor,border:s.borderColor,color:s.color,opacity:s.opacity}}')
                page.mouse.move(0, 0)
                assert appearance('#send') == appearance('#attach')
                assert page.locator('#send').inner_text() == '↑'
                page.locator('#send').hover()
                send_hover = appearance('#send')
                page.locator('#attach').hover()
                assert appearance('#attach') == send_hover
                page.mouse.move(0, 0)
                page.evaluate("$('send').disabled=true;$('attach').disabled=true")
                assert appearance('#send') == appearance('#attach')
                page.evaluate("$('send').disabled=false;$('attach').disabled=false")
                page.locator('#send').focus()
                page.keyboard.press('Tab')
                assert page.locator('#microphone').evaluate('(n)=>n===document.activeElement')
                assert page.locator('#microphone').evaluate('(n)=>getComputedStyle(n).outlineStyle') != 'none'
                field.focus()
                page.screenshot(path='/tmp/linko-expanding-active.png')
                field.fill('line\n' * 40)
                assert page.locator('.composer-input').bounding_box()['height'] == 188
                assert field.evaluate('(n)=>n.scrollHeight>n.clientHeight')
                field.evaluate('(n)=>n.scrollTop=200')
                page.evaluate('updateComposer()')
                assert field.evaluate('(n)=>n.scrollTop') == 200
                page.screenshot(path='/tmp/linko-expanding-overflow.png')
                field.fill('short')
                assert page.locator('.composer-input').bounding_box()['height'] == 140
                page.locator('#chat-menu-button').focus()
                page.wait_for_function("()=>$('send').hidden")
                assert page.locator('#send').is_hidden()
                assert page.locator('.composer-input').bounding_box()['height'] == 92
                field.fill('')
                page.locator('#chat-menu-button').focus()
                # Every workspace category shares text-only selection at desktop/mobile widths.
                for width in (1440, 393, 320):
                    page.set_viewport_size({'width': width, 'height': 851})
                    for view in ('chat', 'tasks', 'agents', 'todo', 'memory', 'settings', 'search'):
                        tab = page.locator(f'.tabs [data-view={view}]')
                        tab.click()
                        assert tab.get_attribute('aria-current') == 'page'
                        assert tab.evaluate('(n)=>getComputedStyle(n).color') == 'rgb(255, 130, 150)'
                        assert tab.evaluate('(n)=>getComputedStyle(n).borderBottomWidth') == '0px'
                        assert page.locator('#view-' + view).is_visible()
                        tab.focus()
                        page.keyboard.press('Tab')
                        assert page.evaluate('document.activeElement!==document.body')
                    page.locator('.tabs [data-view=chat]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.set_viewport_size({'width':393,'height':851})
                # Fresh authentication/history must enable + without a prior upload or Send.
                assert page.locator('#attach').is_enabled()
                with page.expect_file_chooser() as idle_picker:
                    page.locator('#attach').click()
                idle_picker.value.set_files([])
                assert page.locator('#send').is_hidden()
                picker_checks = []
                touch = browser.new_page(storage_state=page.context.storage_state(), viewport={'width':393,'height':851}, is_mobile=True, has_touch=True)
                touch.goto(url)
                touch.wait_for_function('()=>state.ready&&state.history&&!audioState.loading')
                # Synthetic keyboard geometry; no physical keyboard is claimed.
                touch.evaluate("window.keyboardViewport=new EventTarget();Object.assign(keyboardViewport,{height:851,scale:1,offsetTop:0});Object.defineProperty(window,'visualViewport',{configurable:true,value:keyboardViewport});keyboardViewport.addEventListener('resize',updateViewport);updateViewport()")
                touch.locator('#message-input').focus()
                assert touch.locator('#send').is_visible()
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgba(0, 0, 0, 0)'
                touch.evaluate("keyboardViewport.height=420;keyboardViewport.dispatchEvent(new Event('resize'))")
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgb(119, 128, 142)'
                touch.evaluate("keyboardViewport.height=851;keyboardViewport.dispatchEvent(new Event('resize'))")
                assert touch.locator('#message-input').evaluate('(n)=>n===document.activeElement')
                assert touch.locator('#send').is_hidden()
                assert touch.locator('.composer-input').bounding_box()['height'] == 92
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgba(0, 0, 0, 0)'
                touch.locator('#message-input').tap()
                assert touch.locator('#send').is_visible()
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgba(0, 0, 0, 0)'
                touch.evaluate("keyboardViewport.scale=2;keyboardViewport.height=420;keyboardViewport.dispatchEvent(new Event('resize'))")
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgba(0, 0, 0, 0)'
                touch.evaluate("keyboardViewport.scale=1;keyboardViewport.dispatchEvent(new Event('resize'))")
                assert touch.locator('#message-input').evaluate('(n)=>getComputedStyle(n).caretColor') == 'rgb(119, 128, 142)'
                touch.evaluate("keyboardViewport.height=851;keyboardViewport.dispatchEvent(new Event('resize'))")
                touch.locator('#chat-menu-button').focus()
                touch.evaluate("delete window.visualViewport;updateViewport()")
                for device, target in [('desktop', page), ('touch', touch)]:
                    target.bring_to_front()
                    def activate(selector):
                        if device == 'touch':
                            target.locator(selector).tap()
                        else:
                            target.locator(selector).click()
                    target.locator('#message-input').fill('focused draft')
                    target.evaluate("window.draftBlurs=0;window.controlClicks=[];$('message-input').addEventListener('blur',()=>draftBlurs++);$('chat-form').addEventListener('click',event=>controlClicks.push({id:event.target.id,trusted:event.isTrusted,active:navigator.userActivation.isActive}))")
                    if device == 'touch':
                        # Reduced viewport models available space, not a real Android keyboard.
                        target.set_viewport_size({'width':393,'height':420})
                    target.locator('#message-input').evaluate('(input)=>input.setSelectionRange(2,7)')
                    activate('#microphone')
                    target.wait_for_function("()=>audioState.session?.phase==='recording'&&audioState.session.bytes>0")
                    assert target.evaluate("document.activeElement===$('message-input')&&draftBlurs===0")
                    with target.expect_file_chooser() as audio_cancel:
                        activate('#audio-upload')
                    assert not audio_cancel.value.is_multiple()
                    assert target.evaluate("document.activeElement===$('message-input')&&draftBlurs===0&&audioState.session.phase==='recording'")
                    audio_cancel.value.set_files([])
                    assert target.evaluate("audioState.session.phase==='recording'&&document.activeElement===$('message-input')")
                    activate('#audio-cancel')
                    assert target.evaluate("document.activeElement===$('message-input')&&draftBlurs===0&&!audioState.session")
                    if device == 'touch':
                        target.set_viewport_size({'width':393,'height':851})
                    assert target.locator('#attach').is_enabled()
                    assert target.evaluate("()=>{const b=$('attach').getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)===$('attach')}")
                    assert target.locator('#attachment-input').get_attribute('accept') is None
                    with target.expect_file_chooser() as selected:
                        activate('#attach')
                    assert selected.value.is_multiple()
                    assert target.evaluate("document.activeElement===$('message-input')&&draftBlurs===0")
                    # Real bytes selected via the chooser, not set_input_files bypassing the + path.
                    selected.value.set_files({'name':device+'.txt','mimeType':'text/plain','buffer':b'direct picker bytes'})
                    target.wait_for_function('()=>attachmentDraft(state.project).length===1&&!attachmentState.uploads.size')
                    assert target.evaluate("document.activeElement===$('message-input')&&$('message-input').selectionStart===2&&$('message-input').selectionEnd===7")
                    target.locator('#attachment-list button').click()
                    target.locator('#message-input').focus()
                    with target.expect_file_chooser() as cancelled:
                        activate('#attach')
                    cancelled.value.set_files([])
                    assert target.evaluate("document.activeElement===$('message-input')")
                    # Controlled OS blur/cancel fixture: restore selection on return, never steal another field.
                    with target.expect_file_chooser() as os_cancel:
                        activate('#attach')
                    target.evaluate("$('message-input').blur();$('attachment-input').dispatchEvent(new Event('cancel'))")
                    os_cancel.value.set_files([])
                    assert target.evaluate("document.activeElement===$('message-input')")
                    with target.expect_file_chooser() as other_field:
                        activate('#attach')
                    target.locator('#chat-menu-button').focus()
                    target.evaluate("$('attachment-input').dispatchEvent(new Event('cancel'))")
                    other_field.value.set_files([])
                    assert target.evaluate("document.activeElement===$('chat-menu-button')")
                    # Native keyboard button activation remains accessible and trusted.
                    target.locator('#attach').focus()
                    assert target.locator('#attach').is_enabled()
                    assert target.evaluate("document.activeElement===$('attach')")
                    with target.expect_file_chooser() as keyboard:
                        target.locator('#attach').press('Enter')
                    keyboard.value.set_files([])
                    assert target.evaluate("document.activeElement===$('attach')")
                    if device == 'touch':
                        origin=target.evaluate('state.project')
                        destination=target.evaluate('state.portfolio.projects[0].id')
                        target.locator('#message-input').focus()
                        with target.expect_file_chooser() as switched:
                            activate('#attach')
                        target.evaluate('(id)=>selectProject(id)',destination)
                        target.wait_for_function('(id)=>state.project===id&&!!state.history',arg=destination)
                        target.locator('#chat-menu-button').focus()
                        switched.value.set_files({'name':'origin.txt','mimeType':'text/plain','buffer':b'origin context'})
                        target.wait_for_function('(id)=>attachmentDraft(id).length===1&&!attachmentState.uploads.size',arg=origin)
                        assert target.evaluate('attachmentDraft(state.project).length')==0
                        assert target.evaluate("document.activeElement===$('chat-menu-button')")
                        assert target.locator('#attach').is_enabled()
                    assert target.evaluate("controlClicks.filter(event=>event.id==='attachment-input').every(event=>event.active)")
                    assert target.evaluate("controlClicks.filter(event=>event.id==='attach').every(event=>event.trusted)")
                    picker_checks.append({'device':device,'direct_picker':True,'record_no_blur':True,'clicks':target.evaluate('controlClicks')})
                    target.locator('#message-input').fill('')
                touch.close()
                page.bring_to_front()
                alpha, beta = page.evaluate('state.portfolio.projects.map(p=>p.id)')
                page.evaluate('(id)=>selectProject(id)', alpha)
                page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=alpha)

                def start():
                    page.locator('#message-input').focus()
                    page.locator('#microphone').click()
                    page.wait_for_function("()=>audioState.session?.phase==='recording'&&audioState.session.bytes>0")

                def stop():
                    page.locator('#microphone').click()
                    page.wait_for_function('()=>!audioState.session')

                page.locator('#message-input').fill('typed')
                # Each session's distinct transcript appends to the latest editing, never replaces it.
                number = [0]
                def distinct(route):
                    number[0] += 1
                    route.fulfill(json={'text': f'recording {number[0]}'})
                page.route('**/api/audio/transcribe', distinct)
                start()
                stop()
                start()
                stop()
                assert page.locator('#message-input').input_value() == 'typed\n\nrecording 1\n\nrecording 2'
                assert not chats
                # Background visibility does not force Stop. Suspension does preserve recovery audio.
                start()
                page.evaluate("Object.defineProperty(document,'hidden',{get:()=>true,configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_timeout(500)
                assert page.evaluate("audioState.session.phase==='recording'")
                page.evaluate("Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});audioState.session.lastTick=Date.now()-10000;document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_function("()=>audioState.session?.phase==='error'&&audioState.session.blob.size>0")
                assert 'uncertain' in page.locator('#audio-status').inner_text()
                assert not chats
                page.evaluate('()=>audioStorageQueue')
                page.reload()
                page.wait_for_function("()=>state.ready&&state.history&&!audioState.loading&&audioState.session?.phase==='error'")
                page.evaluate('(id)=>selectProject(id)', alpha)
                page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=alpha)
                assert page.locator('#message-input').input_value() == 'typed\n\nrecording 1\n\nrecording 2'
                assert 'recovered' in page.locator('#audio-status').inner_text()
                page.locator('#microphone').click()
                page.wait_for_function('()=>!audioState.session')
                assert page.locator('#message-input').input_value().endswith('\n\nrecording 3')
                # Discard cancels an older held result while a new recording gets its own transcript.
                held = []
                page.unroute('**/api/audio/transcribe')
                page.route('**/api/audio/transcribe', lambda route: held.append(route))
                start()
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='transcribing'")
                page.locator('#audio-cancel').click()
                start()
                held.pop().fulfill(json={'text': 'STALE MUST NEVER APPEAR'})
                page.unroute('**/api/audio/transcribe')
                page.route('**/api/audio/transcribe', distinct)
                stop()
                assert 'STALE' not in page.locator('#message-input').input_value()
                assert page.locator('#message-input').input_value().endswith('\n\nrecording 4')
                # SVG/unknown MIME and audio use the general picker during recording;
                # they remain attachments and never trigger STT or execute script.
                page.locator('#microphone').click()
                page.wait_for_function("()=>audioState.session?.phase==='recording'")
                stt = []
                page.on('request', lambda request: stt.append(request.url) if '/api/audio/transcribe' in request.url else None)
                for name, mime, data in [('active.svg','image/svg+xml',b'<svg><script>window.attachmentExecuted=true</script></svg>'),
                                         ('owner.unknown','application/x-unknown',b'\x00\xffdata'),
                                         ('clip.mp3','audio/mpeg',b'ID3audio')]:
                    with page.expect_file_chooser() as generic:
                        page.locator('#attach').click()
                    generic.value.set_files({'name': name, 'mimeType': mime, 'buffer': data})
                    page.wait_for_function('()=>attachmentDraft(state.project).length===1&&!attachmentState.uploads.size')
                    generic_id=page.evaluate('attachmentDraft(state.project)[0].id')
                    receipt=page.request.get(url+'/api/attachments/'+generic_id)
                    assert receipt.body()==data and receipt.headers['content-type']=='application/octet-stream'
                    assert receipt.headers['content-disposition'].startswith('attachment;')
                    assert not page.evaluate('Boolean(window.attachmentExecuted)')
                    assert page.evaluate("audioState.session.phase")=='recording' and not stt
                    page.locator('#attachment-list button').click()
                page.locator('#audio-cancel').click()
                page.wait_for_function('()=>!audioState.session')
                # Monochrome icon geometry and accessible states on mobile and desktop.
                for width in [1440,393,320]:
                    page.set_viewport_size({'width':width,'height':900})
                    page.locator('#message-input').focus()
                    page.wait_for_function("()=>!$('send').hidden")
                    icon=page.locator('#microphone svg')
                    assert icon.get_attribute('viewBox')=='-3 0 19 19'
                    assert icon.get_attribute('aria-hidden')=='true'
                    assert page.locator('#microphone').get_attribute('aria-label')=='Record microphone'
                    assert icon.evaluate('(e)=>getComputedStyle(e).fill===getComputedStyle(e.parentElement).color')
                    assert icon.bounding_box()['width']==24 and icon.bounding_box()['height']==24
                    page.screenshot(path=str(Path(os.environ.get('LINKO_EVIDENCE_DIR', str(root))) / f'microphone-{width}.png'))
                page.set_viewport_size({'width':393,'height':851})
                # Native upload bytes survive Send/lost receipt/reload and remain downloadable in history.
                assert page.locator('#attach').is_enabled()
                with page.expect_file_chooser() as upload:
                    page.locator('#attach').click()
                upload.value.set_files({'name': 'notes.txt', 'mimeType': 'text/plain', 'buffer': b'real file contents'})
                page.wait_for_function('()=>attachmentDraft(state.project).length===1&&!attachmentState.uploads.size')
                identifier = page.evaluate('attachmentDraft(state.project)[0].id')
                assert page.request.get(url+'/api/attachments/'+identifier).body() == b'real file contents'
                page.locator('#attachment-input').set_input_files({'name': 'wrong.heic', 'mimeType': 'image/heic', 'buffer': b'not supported'})
                page.wait_for_function('()=>!attachmentState.uploads.size')
                assert page.evaluate('attachmentDraft(state.project).length') == 2
                page.locator('#attachment-list button').nth(1).click()
                assert page.evaluate('attachmentDraft(state.project).length') == 1
                def drop(route):
                    assert route.fetch().status == 200
                    route.abort('failed')
                page.route('**/api/chat', drop)
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>!state.pending.size&&submissions.size===1')
                request_id = chats[-1]['request_id']
                page.unroute('**/api/chat')
                page.reload()
                page.wait_for_function('()=>state.ready&&!submissions.size&&!state.pending.size&&!state.execution')
                assert chats[-1]['request_id'] == request_id
                owner = page.evaluate("state.history.messages.filter(m=>m.role==='user')")
                assert len(owner) == 1 and owner[0]['attachments'][0]['id'] == identifier
                assert page.locator('.message-attachments a').inner_text() == 'notes.txt'
                assert page.evaluate('attachmentDraft(state.project).length') == 0
                # Attachment-only Send works with a blank textarea; metadata remains scoped on project switching.
                page.locator('#attachment-input').set_input_files({'name': 'binary.dat', 'mimeType': 'application/octet-stream', 'buffer': b'\x00\x01data'})
                page.wait_for_function('()=>attachmentDraft(state.project).length===1&&!attachmentState.uploads.size')
                page.evaluate('(id)=>selectProject(id)', beta)
                page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=beta)
                assert page.locator('#attachment-list').is_hidden()
                page.evaluate('(id)=>selectProject(id)', alpha)
                page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=alpha)
                assert page.locator('#attachment-list').is_visible()
                page.locator('#message-input').fill('')
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>!state.pending.size&&!state.execution&&!submissions.size')
                assert chats[-1]['content'] == '' and len(chats[-1]['attachment_ids']) == 1
                page.locator('#message-input').fill('Busy recording')
                page.locator('#message-input').focus()
                page.locator('#send').click()
                page.wait_for_function('()=>state.execution&&!state.pending.size')
                geometry = []
                for width, height in [(1440, 1000), (393, 851), (320, 420)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    start()
                    boxes = [page.locator('#'+id).bounding_box() for id in ('attach','send','microphone')]
                    assert all(box['width'] == box['height'] == 44 for box in boxes)
                    assert boxes[0]['x'] == boxes[1]['x'] == boxes[2]['x']
                    assert boxes[0]['y'] < boxes[1]['y'] < boxes[2]['y']
                    composer = page.locator('#chat-form').bounding_box()
                    assert composer['height'] <= 202 and composer['y']+composer['height'] <= height
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('#audio-stop').count() == 0 and page.locator('#audio-cancel').is_visible()
                    assert page.locator('#microphone').get_attribute('aria-label') == 'Stop recording and transcribe'
                    recording_controls=[page.locator('#'+id).bounding_box() for id in ('audio-cancel','audio-upload')]
                    input_box=page.locator('.composer-input').bounding_box()
                    assert all(box['height']>=44 and box['width']>=64 for box in recording_controls)
                    assert abs(recording_controls[-1]['x']+recording_controls[-1]['width']-(input_box['x']+input_box['width']))<1
                    assert recording_controls[-1]['x']>=input_box['x']+input_box['width']/2
                    assert all(page.evaluate('(id)=>{const b=$(id).getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)===$(id)}',id) for id in ('audio-cancel','audio-upload','attach','send','microphone'))
                    page.screenshot(path=f'/tmp/linko-compact-composer-{width}.png')
                    geometry.append({'viewport': [width,height], 'composer': composer, 'controls': boxes, 'recording_controls': recording_controls})
                    page.locator('#audio-cancel').click()
                assert not errors, errors
                print(json.dumps({'status': 'passed', 'geometry': geometry, 'page_errors': errors, 'chat_requests': len(chats), 'picker_checks': picker_checks}))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

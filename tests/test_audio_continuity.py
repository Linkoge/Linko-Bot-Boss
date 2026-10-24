"""Real Chromium lifecycle regressions; fake microphone/provider, disposable server data."""
import json
import os
import runpy
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope='module')
def audio_server(tmp_path_factory):
    root = tmp_path_factory.mktemp('audio-continuity')
    for name in ('Alpha', 'Beta'):
        (root / 'projects' / name).mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    program = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/browser_audio.py'))['PROGRAM']
    env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
           'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
    process = subprocess.Popen([sys.executable, '-c', program.replace('PORT', str(port))], env=env)
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/api/auth-status', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail('Disposable audio server did not start')
        yield url, root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope='module')
def audio_auth(audio_server):
    url, root = audio_server
    credentials = {'username': 'owner', 'password': secrets.token_urlsafe(24)}
    with httpx.Client(base_url=url, timeout=5) as client:
        response = client.post('/api/setup', json={**credentials, 'token': (root / 'data/setup-token').read_text()})
        assert response.status_code == 200
        response = client.post('/api/login', json=credentials)
        assert response.status_code == 200
        return [{'name': name, 'value': value, 'url': url} for name, value in client.cookies.items()]


@pytest.fixture
def audio_page(audio_server, audio_auth):
    url, root = audio_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('CHROME_PATH'), args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        context = browser.new_context(viewport={'width': 393, 'height': 851})
        context.add_cookies(audio_auth)
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('''
          window.wakeRequests=0;window.wakeReleases=0;window.captureRequests=0;
          window.wakeMode='supported';window.locks=[];
          Object.defineProperty(navigator,'wakeLock',{configurable:true,value:{request:async()=>{
            wakeRequests++;
            if(wakeMode==='denied')throw new DOMException('Denied','NotAllowedError');
            const lock=new EventTarget();lock.released=false;
            lock.release=async()=>{if(!lock.released){lock.released=true;wakeReleases++;lock.dispatchEvent(new Event('release'));}};
            locks.push(lock);return lock;
          }}});
          const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia=(...args)=>{captureRequests++;return capture(...args);};
        ''')
        page.goto(url)
        page.wait_for_function('()=>state.ready && state.history && !audioState.loading')
        try:
            yield page
            assert errors == []
        finally:
            browser.close()


def start(page):
    page.locator('#microphone').click()
    page.wait_for_function("()=>audioState.session?.phase==='recording' && audioState.session.bytes>0")


def assert_progress_fits_beside_audio(page):
    page.evaluate('''()=>{
      state.portfolio.progress={active:true,phase:'deploy',label:'Deploying: running tests'};
      renderProgress();
    }''')
    progress = page.locator('#progress-chip').bounding_box()
    actions = page.locator('.audio-actions').bounding_box()
    assert progress and actions
    assert progress['x'] + progress['width'] <= actions['x']


def test_navigation_background_single_whole_upload(audio_page, audio_server):
    page = audio_page
    uploads = []
    page.on('request', lambda request: uploads.append(request) if request.url.endswith('/api/audio/transcribe') else None)
    start(page)
    origin = page.evaluate('({project:audioState.session.project,conversation:audioState.session.conversation,id:audioState.session.id})')
    page.evaluate("window.originalRecorder=audioState.session.recorder;window.originalStream=audioState.session.stream")
    page.locator('[data-view=tasks]').click()
    assert page.locator('#audio-panel').is_hidden()
    assert page.evaluate("audioState.session.phase==='recording'")
    page.evaluate("notice('Test notification');const tall=document.createElement('div');tall.style.height='1600px';$('view-tasks').append(tall)")
    page.evaluate('window.scrollTo(0, 800)')
    page.wait_for_timeout(100)

    other = page.evaluate('state.portfolio.projects.find(p=>p.id!==state.project).id')
    page.evaluate('(id)=>selectProject(id)', other)
    page.wait_for_function('(id)=>state.project===id && state.history', arg=other)
    # Permission/startup and capture are independent of transient view and project selection.
    assert page.evaluate('audioState.session.recorder===originalRecorder && captureRequests===1')
    assert page.evaluate('audioState.session.id') == origin['id']
    assert uploads == []
    # Exercise an actual second browser tab as well as deterministic visibility events.
    before = page.evaluate('audioState.session.bytes')
    second = page.context.new_page()
    second.goto('about:blank')
    second.bring_to_front()
    page.wait_for_timeout(1200)
    page.bring_to_front()
    second.close()
    assert page.evaluate('audioState.session.bytes') > before
    # Visibility events and timer throttling do not prove microphone failure.
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function('()=>wakeReleases===1')
    page.wait_for_timeout(5500)
    assert page.evaluate("audioState.session.phase==='recording' && originalRecorder.state==='recording'")
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function('()=>wakeRequests===2 && !!audioState.session.wakeLock')
    assert uploads == []
    page.locator('[data-view=chat]').click()
    page.locator('#microphone').click()
    page.wait_for_function('()=>!audioState.session')
    assert len(uploads) == 1
    received = json.loads((audio_server[1] / 'data/audio-provider-last.json').read_text())
    assert len(bytes.fromhex(received['data'])) > 0 and received['mime'].startswith('audio/')
    assert page.evaluate('(id)=>state.drafts.get(id)', origin['project']) == 'spoken transcript'
    assert page.evaluate("$('message-input').value") == ''
    assert page.evaluate("originalStream.getTracks().every(t=>t.readyState==='ended') && wakeReleases===2")
    assert page.locator('#audio-panel').is_hidden()


@pytest.mark.parametrize('capability', ['denied', 'unsupported'])
def test_wake_failure_cancel_and_late_permission(audio_page, capability):
    page = audio_page
    page.evaluate("(mode)=>{wakeMode=mode;if(mode==='unsupported')Object.defineProperty(navigator,'wakeLock',{value:undefined});}", capability)
    start(page)
    assert page.evaluate('!audioState.session.wakeLock')
    page.evaluate('window.stream=audioState.session.stream')
    page.locator('#audio-cancel').click()
    assert page.evaluate("!audioState.session && stream.getTracks().every(t=>t.readyState==='ended' && t.onended===null)")
    # A pending permission result is allowed to arrive after internal navigation.
    page.evaluate('''()=>{
      const capture=navigator.mediaDevices.getUserMedia;
      navigator.mediaDevices.getUserMedia=(...args)=>new Promise(resolve=>{window.allowCapture=()=>capture(...args).then(resolve);});
    }''')
    page.locator('#microphone').click()
    page.locator('[data-view=tasks]').click()
    page.evaluate('allowCapture()')
    page.wait_for_function("()=>audioState.session?.phase==='recording'")
    page.locator('[data-view=chat]').click()
    page.locator('#audio-cancel').click()
    assert page.evaluate('!audioState.session')


@pytest.mark.parametrize('interruption', ['ended', 'stop', 'error', 'mute', 'freeze'])
def test_interruption_retains_whole_audio_truthful_ui(audio_page, interruption):
    page = audio_page
    uploads = []
    page.on('request', lambda request: uploads.append(request.url) if request.url.endswith('/api/audio/transcribe') else None)
    start(page)
    page.evaluate('window.stream=audioState.session.stream')
    page.evaluate('''kind=>{
      const session=audioState.session,track=session.stream.getAudioTracks()[0];
      if(kind==='ended')track.stop();
      if(kind==='stop'){session.recorder.stop();document.dispatchEvent(new Event('visibilitychange'));}
      if(kind==='error')session.recorder.dispatchEvent(new Event('error'));
      if(kind==='freeze')document.dispatchEvent(new Event('freeze'));
      if(kind==='mute')track.dispatchEvent(new Event('mute'));
    }''', interruption)
    if interruption == 'mute':
        assert 'Microphone was paused' in page.locator('#audio-panel').get_attribute('title')
        assert page.locator('#audio-status').inner_text().count(':') == 1
        assert page.evaluate("audioState.session.phase==='recording'")
        before = page.evaluate('audioState.session.bytes')
        page.evaluate("audioState.session.stream.getAudioTracks()[0].dispatchEvent(new Event('unmute'))")
        page.wait_for_function('(before)=>audioState.session.bytes>before', arg=before)
        assert page.evaluate("audioState.session.phase==='recording'")
        page.locator('#microphone').click()
    page.wait_for_function("()=>audioState.session?.phase==='error' && audioState.session.blob?.size>0")
    assert uploads == []
    assert page.evaluate('audioState.session.blob.size===audioState.session.bytes')
    assert page.locator('#audio-stop').count() == 0
    assert page.locator('#audio-download').is_visible()
    assert page.locator('#audio-status').inner_text() == page.evaluate('audioState.session.message') != ''
    assert page.locator('#audio-retry').is_visible()
    assert page.evaluate("stream.getTracks().every(t=>t.readyState==='ended') && !audioState.session.wakeLock")
    page.locator('#audio-retry').click()
    page.wait_for_function('()=>!audioState.session')
    assert len(uploads) == 1


def test_late_wake_lock_is_released_and_start_is_single(audio_page):
    page = audio_page
    page.evaluate('''()=>{
      const request=navigator.wakeLock.request;
      navigator.wakeLock.request=()=>new Promise(resolve=>{window.grantWake=()=>request().then(resolve);});
    }''')
    start(page)
    page.evaluate('startAudio();startAudio()')
    assert page.evaluate('captureRequests') == 1
    page.locator('#audio-cancel').click()
    page.evaluate('grantWake()')
    page.wait_for_function('()=>wakeReleases===1')
    assert page.evaluate('!audioState.session')


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640)])
def test_starting_microphone_shows_starting_text(audio_page, width, height):
    page = audio_page
    page.set_viewport_size({'width': width, 'height': height})
    page.evaluate('''()=>{
      const capture=navigator.mediaDevices.getUserMedia;
      navigator.mediaDevices.getUserMedia=(...args)=>new Promise(resolve=>{
        window.allowCapture=()=>capture(...args).then(resolve);
      });
    }''')
    page.locator('#microphone').click()
    assert page.evaluate("audioState.session.phase") == 'starting'
    assert page.locator('#audio-status').text_content() == 'Starting microphone…'
    assert page.locator('#microphone').is_disabled()
    page.evaluate('allowCapture()')
    page.wait_for_function("()=>audioState.session?.phase==='recording'")
    assert page.locator('#audio-status').inner_text() == '00:00'
    page.locator('#audio-cancel').click()


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640)])
@pytest.mark.parametrize('failure', ['no-audio', 'no-speech', 'provider-error'])
def test_failure_shows_text_keeps_retry_and_microphone_starts_fresh(audio_page, width, height, failure):
    page = audio_page
    page.set_viewport_size({'width': width, 'height': height})
    microphone = page.locator('#microphone')
    idle_icon = microphone.inner_html()
    idle_color = microphone.evaluate('n=>getComputedStyle(n).color')
    uploads = []

    def fail(route):
        uploads.append(route.request.post_data_buffer)
        route.fulfill(status=502 if failure == 'provider-error' else 200,
                      json={'detail': 'Transcription unavailable'} if failure == 'provider-error' else {'text': ''})

    page.route('**/api/audio/transcribe', fail)
    start(page)
    previous_id = page.evaluate('audioState.session.id')
    if failure == 'no-audio':
        page.evaluate('''()=>{
          const session=audioState.session;
          session.chunks=[];session.bytes=0;session.recorder.ondataavailable=()=>{};
        }''')
    microphone.click()
    page.wait_for_function("()=>audioState.session?.phase==='error'")
    assert page.locator('#audio-status').text_content() == page.evaluate('audioState.session.message') != ''
    retry = page.locator('#audio-retry')
    assert retry.is_visible() and retry.is_enabled() and retry.inner_text() == 'Retry'
    assert microphone.inner_html() == idle_icon
    assert microphone.inner_text() == ''
    assert microphone.get_attribute('aria-label') == 'Record microphone'
    assert microphone.get_attribute('title') == 'Record microphone'
    assert microphone.evaluate('n=>getComputedStyle(n).color') == idle_color
    assert microphone.is_enabled()
    assert_progress_fits_beside_audio(page)
    upload_count = len(uploads)
    microphone.click()
    page.wait_for_function("()=>audioState.session?.phase==='recording'")
    assert page.evaluate('audioState.session.id') != previous_id
    assert page.evaluate('captureRequests') == 2
    assert page.locator('#audio-status').inner_text() == '00:00'
    assert page.locator('#audio-retry').is_hidden()
    assert len(uploads) == upload_count  # the microphone never retranscribes retained audio
    assert_progress_fits_beside_audio(page)
    page.locator('#audio-cancel').click()


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640)])
def test_separate_retry_still_transcribes_the_retained_recording(audio_page, width, height):
    page = audio_page
    page.set_viewport_size({'width': width, 'height': height})
    uploads = []

    def transcribe(route):
        uploads.append(route.request.post_data_buffer)
        if len(uploads) == 1:
            route.fulfill(status=502, json={'detail': 'Transcription unavailable'})
        else:
            route.fulfill(json={'text': 'Recovered transcript'})

    page.route('**/api/audio/transcribe', transcribe)
    start(page)
    page.locator('#microphone').click()
    page.wait_for_function("()=>audioState.session?.phase==='error'")
    assert page.locator('#audio-status').text_content() == page.evaluate('audioState.session.message') != ''
    page.locator('#audio-retry').click()
    page.wait_for_function('()=>!audioState.session')
    assert len(uploads) == 2 and uploads[0] == uploads[1]
    assert page.evaluate('captureRequests') == 1
    assert page.locator('#message-input').input_value() == 'Recovered transcript'


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640), (320, 420)])
@pytest.mark.parametrize('draft', [
    pytest.param('', id='empty'),
    pytest.param('Retained draft', id='short'),
    pytest.param('\n'.join(['Long draft'] * 40), id='overflow'),
])
def test_recording_bottom_row_timer_and_toggle(audio_page, width, height, draft):
    import re

    page = audio_page
    page.set_viewport_size({'width': width, 'height': height})
    page.locator('#message-input').fill(draft)
    page.locator('#message-input').focus()
    controls = ('#attach', '#microphone', '#send')
    before = {selector: page.locator(selector).bounding_box() for selector in controls}
    uploads = []
    page.on('request', lambda r: uploads.append(r) if r.url.endswith('/api/audio/transcribe') else None)
    start(page)
    for selector in controls:
        button = page.locator(selector)
        assert button.is_visible()
        assert button.bounding_box() == before[selector]
        assert before[selector]['width'] == before[selector]['height'] == 44
        assert button.evaluate('''n=>{const b=n.getBoundingClientRect();
          return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)===n;}''')
    # The general file picker remains usable while the microphone is capturing.
    with page.expect_file_chooser():
        page.locator('#attach').click()
    assert page.evaluate("audioState.session.phase==='recording'")
    page.wait_for_function("()=>$('audio-status').textContent!=='00:00'")
    assert re.fullmatch(r'\d+:\d{2}', page.locator('#audio-status').inner_text())
    assert page.locator('#audio-stop').count() == 0
    assert page.locator('#audio-panel').evaluate("n=>n.parentElement.classList.contains('composer-footer')")
    boxes = [page.locator(selector).bounding_box() for selector in
             ('#audio-status', '#audio-upload', '#audio-cancel', '#send')]
    for box in boxes:
        assert 0 <= box['x'] and box['x'] + box['width'] <= width
        assert box['y'] + box['height'] <= height
        assert abs(box['y'] + box['height']/2 - boxes[-1]['y'] - boxes[-1]['height']/2) <= 1
    assert all(a['x'] + a['width'] <= b['x'] + 1 for a, b in zip(boxes, boxes[1:]))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('#audio-retry').is_hidden() and page.locator('#audio-download').is_hidden()
    page.locator('#microphone').click()
    page.wait_for_function('()=>!audioState.session')
    assert len(uploads) == 1
    assert page.locator('#message-input').input_value() == (draft + '\n\n' if draft else '') + 'spoken transcript'


def test_startup_failures_show_reason_and_release_microphone(audio_page):
    page = audio_page
    status = page.locator('#audio-status')
    reason = 'Audio needs XAI_API_KEY in the server environment.'
    page.route('**/api/audio', lambda route: route.fulfill(json={'available': False, 'reason': reason}))
    page.locator('#microphone').click()
    page.wait_for_function("()=>audioState.session?.phase==='error'")
    assert status.inner_text() == reason
    assert reason in page.locator('#audio-panel').get_attribute('title')
    page.wait_for_function("()=>!audioState.session.stream")
    page.locator('#audio-cancel').click()
    page.unroute('**/api/audio')
    page.evaluate("()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('denied','NotAllowedError')}}")
    page.locator('#microphone').click()
    page.wait_for_function("()=>audioState.session?.phase==='error'")
    assert 'permission denied' in status.inner_text()

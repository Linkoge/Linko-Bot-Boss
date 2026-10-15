"""Local Chromium startup measurements; fake device, not physical mobile performance."""
import argparse
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

INSTRUMENT = '''
window.captureProbe={events:[],streams:[]};
const mark=(name)=>captureProbe.events.push({name,at:performance.now(),gesture:navigator.userActivation.isActive});
const network=window.fetch;window.fetch=(...a)=>network(...a).then(r=>{if(String(a[0])==='/api/audio')mark('config-response');return r;});
const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia=(...args)=>{mark('permission-request');return original(...args).then(stream=>{captureProbe.streams.push(stream);mark('stream-ready');return stream;});};
const start=MediaRecorder.prototype.start;
MediaRecorder.prototype.start=function(...args){mark('recorder-start-call');this.addEventListener('start',()=>mark('recorder-start-event'),{once:true});this.addEventListener('dataavailable',()=>mark('first-data'),{once:true});return start.apply(this,args);};
document.addEventListener('click',event=>{if(event.target.closest('#microphone'))mark('tap');},true);
new MutationObserver(()=>{const n=document.querySelector('#audio-status');if(n&&!n.closest('[hidden]')){const phase=typeof audioState==='undefined'?null:audioState.session?.phase;if(phase&&!captureProbe.events.some(e=>e.name==='ui-'+phase))mark('ui-'+phase);}}).observe(document,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden']});
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='linko-start-probe-') as directory:
        root = Path(directory)
        for name in ('Alpha', 'Beta'):
            (root / 'projects' / name).mkdir(parents=True)
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
            results = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                page.add_init_script(INSTRUMENT)
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history&&!audioState.loading')
                for delay in (0, 600):
                    page.route('**/api/audio', lambda route: (time.sleep(delay / 1000), route.continue_()))
                    page.evaluate('captureProbe.events=[]')
                    page.locator('#microphone').click()
                    page.wait_for_function("()=>audioState.session?.phase==='recording'&&audioState.session.bytes>0")
                    events = page.evaluate('captureProbe.events')
                    if args.verify:
                        times = {e['name']: e['at'] for e in events}
                        assert times['permission-request'] - times['tap'] < 200
                        assert times['ui-starting'] - times['tap'] < 200
                        assert times['ui-recording'] >= times['recorder-start-event']
                        assert next(e['gesture'] for e in events if e['name'] == 'permission-request')
                        if delay:
                            assert times['recorder-start-event'] < times['config-response']
                            assert times['first-data'] < times['config-response']
                    tap = next(e['at'] for e in events if e['name'] == 'tap')
                    results.append({'injected_config_delay_ms': delay,
                                    'events': [{**event, 'after_tap_ms': round(event['at'] - tap, 2)} for event in events]})
                    page.locator('#audio-cancel').click()
                    page.wait_for_function('()=>!audioState.session')
                    assert page.evaluate('captureProbe.streams.every(s=>s.getTracks().every(t=>t.readyState==="ended"))')
                    page.unroute('**/api/audio')
                if args.verify:
                    # No prewarming: only the two deliberate taps above requested a device.
                    assert page.evaluate('captureProbe.streams.length') == 2
                    page.evaluate("window.originalCapture=navigator.mediaDevices.getUserMedia;navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.grantLater=resolve)")
                    page.locator('#microphone').click()
                    assert page.evaluate("audioState.session.phase") == 'starting'
                    assert 'Starting microphone' in page.locator('#audio-status').inner_text()
                    page.evaluate("document.querySelector('#microphone').click()")
                    page.evaluate("window.dispatchEvent(new Event('pagehide'))")
                    assert page.evaluate('audioState.session') is None
                    page.evaluate("async()=>{const stream=await originalCapture({audio:true});grantLater(stream)}")
                    page.wait_for_function('()=>captureProbe.streams.every(s=>s.getTracks().every(t=>t.readyState==="ended"))')
                    page.evaluate('navigator.mediaDevices.getUserMedia=originalCapture')
                    assert page.evaluate('audioState.session') is None
                    results.append({'pending_pagehide': 'late tracks stopped', 'rapid_tap': 'no second session', 'unsolicited_capture': False})
                browser.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2))
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

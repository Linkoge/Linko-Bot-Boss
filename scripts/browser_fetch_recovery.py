"""Injected browser transport failures against an isolated authenticated manager."""
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix='linko-fetch-ui-') as tmp:
        root = Path(tmp)
        (root / 'projects').mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
        program = f"""import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')
"""
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Fixture did not start')
            assert httpx.post(base + '/api/setup', json={
                'username': 'owner', 'password': 'long-private-browser-password',
                'token': (root / 'data/setup-token').read_text()}).status_code == 200
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width, height in ((1440, 1000), (393, 851), (320, 420)):
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base)
                    page.locator('#username').fill('owner')
                    page.locator('#password').fill('long-private-browser-password')
                    page.locator('#auth-form button').click()
                    page.wait_for_function('()=>state.ready')
                    page.evaluate('()=>clearTimeout(pollTimer)')
                    initial = page.locator('#messages').bounding_box()
                    # Network failure has compact ongoing status, no permanent generic notice.
                    page.route('**/api/portfolio*', lambda route: route.abort('failed'))
                    page.evaluate('()=>pollUpdates()')
                    assert page.locator('#connection-status').is_visible()
                    assert not page.locator('#notice').is_visible()
                    assert page.locator('#messages').bounding_box() == initial
                    page.evaluate('()=>pollUpdates()')
                    assert page.locator('#connection-status').is_visible()
                    page.unroute('**/api/portfolio*')
                    page.evaluate("()=>window.dispatchEvent(new Event('online'))")
                    page.wait_for_function("()=>document.querySelector('#connection-status').hidden")
                    # History failure must remain unhealthy even if portfolio itself succeeds.
                    page.route('**/api/history*', lambda route: route.abort('failed'))
                    page.evaluate('()=>pollUpdates()')
                    assert page.locator('#connection-status').is_visible()
                    page.unroute('**/api/history*')
                    page.evaluate('()=>pollUpdates()')
                    assert not page.locator('#connection-status').is_visible()
                    # SSE reconciliation uses the same recovery channel, not a permanent banner.
                    page.route('**/api/history*', lambda route: route.abort('failed'))
                    page.evaluate('''()=>{
                      clearTimeout(pollTimer);window.originalEventSource=window.EventSource;
                      window.EventSource=class {close(){} addEventListener(){}};
                      attachStream('fixture-stream','fixture-message');state.source.onerror();
                    }''')
                    page.wait_for_function("()=>!document.querySelector('#connection-status').hidden")
                    page.unroute('**/api/history*')
                    page.evaluate('()=>{window.EventSource=window.originalEventSource;return pollUpdates();}')
                    assert not page.locator('#connection-status').is_visible()
                    # A successful history read cannot hide a continuing portfolio failure.
                    page.route('**/api/portfolio*', lambda route: route.abort('failed'))
                    page.evaluate('()=>pollUpdates()')
                    page.evaluate('''()=>{
                      window.originalEventSource=window.EventSource;
                      window.EventSource=class {close(){} addEventListener(type,fn){if(type==='stream.end')this.end=fn;}};
                      attachStream('fixture-stream','fixture-message');state.source.end();
                    }''')
                    page.wait_for_function('()=>!state.source')
                    page.wait_for_timeout(150)
                    assert page.locator('#connection-status').is_visible()
                    page.evaluate('()=>{window.EventSource=window.originalEventSource;}')
                    page.unroute('**/api/portfolio*')
                    page.evaluate('()=>pollUpdates()')
                    assert not page.locator('#connection-status').is_visible()
                    # HTTP errors, including proxy HTML, retain actionable status.
                    page.route('**/api/portfolio*', lambda route: route.fulfill(
                        status=503, content_type='text/html', body='<h1>Unavailable</h1>'))
                    page.evaluate('()=>pollUpdates()')
                    assert '503' in page.locator('#connection-status').get_attribute('title')
                    page.unroute('**/api/portfolio*')
                    # Unrelated recovery cannot clear an unconfirmed mutation notification.
                    page.evaluate("()=>notice('Acceptance is unconfirmed. Your saved request will be checked on return or Send.')")
                    page.evaluate('()=>pollUpdates()')
                    assert page.locator('#notice').is_visible()
                    assert page.locator('#messages').bounding_box() == initial
                    page.locator('#notice-dismiss').click()
                    assert not page.locator('#notice').is_visible()
                    # Offline Send retains content and exactly the same idempotency key for retry.
                    page.route('**/api/chat', lambda route: route.abort('failed'))
                    page.locator('#message-input').fill('Transport retry fixture')
                    page.locator('#send').click()
                    page.wait_for_function('()=>submissions.size===1&&!state.pending.size')
                    saved = page.evaluate('()=>submissions.get(null).request_id')
                    assert 'Acceptance is unconfirmed' in page.locator('#notice-text').inner_text()
                    assert page.locator('#message-input').input_value() == 'Transport retry fixture'
                    page.unroute('**/api/chat')
                    seen = []

                    def capture(route):
                        seen.append(route.request.post_data_json['request_id'])
                        route.continue_()

                    page.route('**/api/chat', capture)
                    page.locator('#send').click()
                    page.wait_for_function('()=>submissions.size===0&&!state.pending.size')
                    assert seen == [saved]
                    page.unroute('**/api/chat')
                    page.locator('#notice-dismiss').click()
                    # An accepted POST followed by a failed history read is never labelled unconfirmed.
                    page.wait_for_function('()=>!state.execution')
                    page.route('**/api/history*', lambda route: route.abort('failed'))
                    page.locator('#message-input').fill('Accepted before history failure')
                    page.locator('#send').click()
                    page.wait_for_function('()=>!state.pending.size')
                    assert 'Message accepted.' in page.locator('#notice-text').inner_text()
                    assert page.evaluate('submissions.size') == 0
                    page.unroute('**/api/history*')
                    page.evaluate('()=>pollUpdates()')
                    page.locator('#notice-dismiss').click()
                    # No overlapping polls while a read is in flight.
                    page.evaluate("""async()=>{
                      clearTimeout(pollTimer);const original=window.fetch;let active=0,peak=0;
                      window.fetch=async(...args)=>{active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,100));try{return await original(...args);}finally{active--;}};
                      try{await Promise.all([pollUpdates(),pollUpdates(),pollUpdates()]);window.pollPeak=peak;}finally{window.fetch=original;}
                    }""")
                    assert page.evaluate('pollPeak') == 1
                    # Genuine session expiry still returns to authentication, even for HTML responses.
                    page.route('**/api/portfolio*', lambda route: route.fulfill(
                        status=401, content_type='text/html', body='Expired'))
                    page.evaluate('()=>pollUpdates()')
                    assert page.locator('#auth-screen').is_visible()
                    assert not page.locator('#connection-status').is_visible()
                    assert not errors, errors
                    assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                    context.close()
                browser.close()
            print('Fetch recovery checks passed: network/history/503 recovery, unchanged layout, dismiss, saved Send retry, serial polls, 401; desktop/mobile/narrow')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

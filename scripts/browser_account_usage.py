"""Disposable authenticated navigation/usage interaction and viewport acceptance."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from ai_manager.account_usage import AccountUsage


def main():
    evidence = Path(os.environ.get('LINKO_USAGE_EVIDENCE', '/tmp/linko-usage-browser'))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linko-usage-') as tmp:
        root = Path(tmp)
        projects = root / 'projects'
        projects.mkdir()
        for name in ['Alpha', 'Beta'] + [f'Long project name {i}' for i in range(12)]:
            (projects / name).mkdir()
        env = {**os.environ, 'AI_MANAGER_DATA': str(root/'data'), 'AI_MANAGER_PROJECT_ROOTS': str(projects),
               'AI_MANAGER_SECURE_COOKIE': '0'}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        program = f'''import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
for path in s.project_roots[0].iterdir():app.state.registry.add(path)
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
            from types import SimpleNamespace
            usage = AccountUsage(SimpleNamespace())
            usage.update({'rateLimitsByLimitId': {'codex': {'planType':'plus', 'primary':{
                'usedPercent':12.5,'windowDurationMins':300,'resetsAt':time.time()+3600},'secondary':{
                'usedPercent':100,'windowDurationMins':10080,'resetsAt':time.time()+86400}}}})
            response = usage.view()
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(has_touch=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.route('**/api/account-usage', lambda route: route.fulfill(json=response))
                page.goto(base)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('usage-fixture-password')
                page.locator('#setup-token').fill((root/'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('() => state.history && usageData')
                assert page.locator('#usage-five').inner_text() == '88%'
                assert page.locator('#usage-week').inner_text() == '0%'
                selected = page.evaluate('state.project')
                receipts = []
                for width, height in [(1440,1000),(393,851),(320,420),(393,420)]:
                    page.set_viewport_size({'width':width,'height':height})
                    top = page.locator('#project-menu-button').bounding_box()
                    bottom = page.locator('#usage-button').bounding_box()
                    nav = page.locator('.navigation').bounding_box()
                    assert top['x'] == bottom['x'] and top['width'] == bottom['width'] == 44
                    assert top['height'] == bottom['height'] == 44
                    assert 0 < bottom['y']-top['y'] < 55 and nav['height'] <= 100
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    composer = page.locator('#message-input').bounding_box()
                    send = page.locator('#send').bounding_box()
                    assert send['y']+send['height'] <= height and composer['y'] > bottom['y']
                    # Workspace and projects scroll; fixed controls stay in place.
                    page.locator('.tabs').evaluate('(n)=>n.scrollLeft=n.scrollWidth')
                    page.locator('#project-tabs').evaluate('(n)=>n.scrollLeft=n.scrollWidth')
                    assert page.locator('#usage-button').bounding_box() == bottom
                    assert page.locator('#project-menu-button').bounding_box() == top
                    page.locator('#usage-button').tap()
                    assert page.locator('#usage-dialog').is_visible()
                    assert '87.5%' in page.locator('#usage-details').inner_text()
                    assert '12.5%' in page.locator('#usage-details').inner_text()
                    assert '300 minutes' in page.locator('#usage-details').inner_text()
                    assert 'Tbilisi' in page.locator('#usage-details').inner_text()
                    dialog = page.locator('#usage-dialog').bounding_box()
                    assert dialog['x'] >= 0 and dialog['x']+dialog['width'] <= width
                    assert dialog['height'] <= height
                    page.screenshot(path=str(evidence/f'{width}x{height}-dialog.png'))
                    # Native modal traps Tab/Shift+Tab and Escape returns focus.
                    for _ in range(5):
                        page.keyboard.press('Tab')
                        assert page.evaluate("document.activeElement.closest('#usage-dialog') !== null")
                    page.keyboard.press('Shift+Tab')
                    assert page.evaluate("document.activeElement.closest('#usage-dialog') !== null")
                    page.keyboard.press('Escape')
                    assert page.locator('#usage-dialog').is_hidden()
                    assert page.locator('#usage-button').evaluate('(n)=>n===document.activeElement')
                    assert page.evaluate('state.project') == selected
                    assert page.locator('#task-count').is_hidden()
                    page.screenshot(path=str(evidence/f'{width}x{height}-navigation.png'))
                    receipts.append({'width':width,'height':height,'top':top,'bottom':bottom,'navigation':nav,'send':send})
                page.locator('#usage-button').focus()
                page.keyboard.press('Enter')
                assert page.locator('#usage-dialog').is_visible()
                page.locator('#usage-close').click()
                # Actual duration determines compact slots; reversed slots retain meaning.
                page.evaluate("usageData.buckets[0].windows.reverse();renderUsage()")
                assert page.locator('#usage-five').inner_text() == '88%'
                page.evaluate("usageData.buckets[0].windows[1].duration_minutes=15;usageData.buckets[0].windows[1].label='15-minute allowance';renderUsage()")
                assert page.locator('#usage-five').inner_text() == '—'
                page.evaluate("$('usage-dialog').showModal();renderUsage()")
                assert '15-minute allowance' in page.locator('#usage-details').inner_text()
                page.keyboard.press('Escape')
                page.evaluate("usageData.buckets[0].windows[0].resets_at=usageNow()-1;renderUsage()")
                assert 'stale' in page.locator('#usage-button').get_attribute('aria-label')
                page.evaluate("$('usage-dialog').showModal();renderUsage()")
                assert 'Reset passed' in page.locator('#usage-details').inner_text()
                page.keyboard.press('Escape')
                page.unroute('**/api/account-usage')
                page.route('**/api/account-usage', lambda route: route.abort())
                page.evaluate('refreshUsage()')
                page.locator('#usage-button').click()
                assert 'Refresh failed' in page.locator('#usage-status').inner_text()
                page.keyboard.press('Escape')
                page.evaluate("usageData={buckets:[],server_time:usageNow(),last_attempt_at:null,error:null};renderUsage()")
                assert page.locator('#usage-five').inner_text() == '—' and page.locator('#usage-week').inner_text() == '—'
                page.locator('#usage-button').click()
                assert 'No account allowance reading' in page.locator('#usage-details').inner_text()
                page.keyboard.press('Escape')
                assert not errors, errors
                (evidence/'browser.json').write_text(json.dumps({'viewports':receipts,'page_errors':errors},indent=2))
                browser.close()
            print('Account usage desktop/mobile/narrow, exact values, unavailable/stale/error, scrolling, keyboard/touch passed')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

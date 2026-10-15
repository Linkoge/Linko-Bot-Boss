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
                'usedPercent':12.5,'windowDurationMins':300,'resetsAt':time.time()+22320},'secondary':{
                'usedPercent':100,'windowDurationMins':10080,'resetsAt':time.time()+476100}}}})
            response = usage.view()
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(has_touch=True, timezone_id="Asia/Tbilisi", locale="en-GB")
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
                    settings_button = page.locator('#model-settings-button').bounding_box()
                    chat_button = page.locator('#chat-menu-button').bounding_box()
                    assert top['x'] == chat_button['x']
                    assert bottom['x'] == settings_button['x']
                    assert top['x'] + 44 + 4 == settings_button['x']
                    assert top['y'] == settings_button['y']
                    assert bottom['y'] == chat_button['y']
                    assert top['width'] == bottom['width'] == settings_button['width'] == 44
                    assert top['height'] == bottom['height'] == 44
                    assert 0 < bottom['y']-top['y'] < 55 and nav['height'] <= 100
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.locator('#message-input').focus()
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
                    assert '12.5%' not in page.locator('#usage-details').inner_text()
                    assert page.locator('#usage-details h3').all_text_contents() == ['Codex', '5 hour', 'Weekly']
                    assert 'allowance' not in page.locator('#usage-dialog').inner_text()
                    assert page.locator('#usage-last-refresh').inner_text() == page.evaluate("'Last refresh '+localTime(usageData.last_successful_refresh_at)")
                    assert 'Tbilisi' not in page.locator('#usage-details').inner_text()
                    assert page.locator('.usage-reset').all_text_contents() == ['6h 12m', '5d 12h 15m']
                    assert page.locator('.usage-reset').first.evaluate('(n)=>getComputedStyle(n).font') == page.locator('.usage-remaining').first.evaluate('(n)=>getComputedStyle(n).font')
                    for duration in page.locator('.usage-reset').all():
                        assert duration.evaluate('(n)=>n.scrollWidth<=n.clientWidth')
                        assert duration.evaluate('(n)=>getComputedStyle(n).color') == page.locator('.usage-remaining').first.evaluate('(n)=>getComputedStyle(n).color')
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
                    # Real touch dispatch opens and closes the chat utilities.
                    page.locator('#chat-menu-button').tap()
                    assert page.locator('#chat-menu').is_visible()
                    page.locator('#chat-menu-button').tap()
                    assert page.locator('#chat-menu').is_hidden()
                    page.screenshot(path=str(evidence/f'{width}x{height}-navigation.png'))
                    receipts.append({'width':width,'height':height,'top':top,'bottom':bottom,'navigation':nav,'send':send})
                page.locator('#usage-button').focus()
                page.keyboard.press('Enter')
                assert page.locator('#usage-dialog').is_visible()
                page.locator('#usage-close').click()
                # Duration rather than primary/secondary slot determines both lean sections.
                page.evaluate("usageData.buckets[0].windows.reverse();renderUsage()")
                assert page.locator('#usage-five').inner_text() == '88%'
                page.locator('#usage-button').click()
                assert page.locator('.usage-remaining').all_text_contents() == ['87.5%', '0%']
                successful_footer = page.locator('#usage-last-refresh').inner_text()
                # HTTP success containing a backend refresh failure preserves last success.
                failed = json.loads(json.dumps(response))
                failed['last_attempt_at'] = response['server_time'] + 120
                failed['error'] = 'Account usage refresh failed.'
                failed['status'] = 'stale'
                for bucket in failed['buckets']:
                    for window in bucket['windows']:
                        window['stale'] = True
                page.unroute('**/api/account-usage')
                page.route('**/api/account-usage', lambda route: route.fulfill(json=failed))
                page.locator('#usage-refresh').click()
                page.wait_for_function('() => !usageRequest')
                assert page.locator('#usage-last-refresh').inner_text() == successful_footer
                assert page.locator('.usage-remaining.stale').count() == 2
                # Browser/network failure also retains success and explicitly marks stale.
                page.unroute('**/api/account-usage')
                page.route('**/api/account-usage', lambda route: route.abort())
                page.locator('#usage-refresh').click()
                page.wait_for_function('() => !usageRequest')
                assert page.locator('#usage-last-refresh').inner_text() == successful_footer
                assert page.locator('.usage-remaining.stale').count() == 2
                page.screenshot(path=str(evidence/'failed-refresh.png'))
                recovered = json.loads(json.dumps(response))
                recovered['last_successful_refresh_at'] += 600
                page.unroute('**/api/account-usage')
                page.route('**/api/account-usage', lambda route: route.fulfill(json=recovered))
                page.locator('#usage-refresh').click()
                page.wait_for_function('() => !usageRequest')
                assert page.locator('#usage-last-refresh').inner_text() != successful_footer
                assert page.locator('.usage-remaining.stale').count() == 0
                page.evaluate("usageData.buckets[0].windows[1].duration_minutes=15;usageData.buckets[0].windows[1].label='15-minute allowance';renderUsage()")
                assert page.locator('.usage-remaining').nth(0).inner_text() == '87.5%'
                assert page.locator('.usage-remaining').nth(1).inner_text() == 'Unavailable'
                page.evaluate("usageData.buckets[0].windows[0].resets_at=usageNow()-1;renderUsage()")
                assert 'stale' in page.locator('#usage-button').get_attribute('aria-label')
                assert page.locator('.usage-remaining.stale').count() == 1
                assert page.locator('.usage-reset').nth(0).inner_text() == '0m'
                page.evaluate('usageData.buckets[0].windows[0].resets_at=null;renderUsage()')
                assert page.locator('.usage-reset').nth(0).inner_text() == 'Unavailable'
                assert page.evaluate('usageDuration(usageNow()+22320)') == '6h 12m'
                assert page.evaluate('usageDuration(usageNow()+1)') == '1m'
                boundaries = page.evaluate('''() => {
                  const original=Date.now;Date.now=()=>1000000000;
                  try{return [-60,0,1,59,60,61,3599,3600,3601,86340,86341,86400,86401,
                    90000,172800,476100].map(seconds=>({seconds,text:usageDuration(usageNow()+seconds)}));}
                  finally{Date.now=original;}
                }''')
                assert [b['text'] for b in boundaries] == ['0m','0m','1m','1m','1m','2m',
                    '1h','1h','1h 1m','23h 59m','1d','1d','1d 1m','1d 1h','2d','5d 12h 15m']
                for invalid in ['null','undefined','NaN','Infinity','-Infinity',"'1000000'"]:
                    assert page.evaluate(f'usageDuration({invalid})') == 'Unavailable'
                (evidence/'format-boundaries.json').write_text(json.dumps(boundaries,indent=2))
                page.unroute('**/api/account-usage')
                page.route('**/api/account-usage', lambda route: route.abort())
                page.evaluate("usageData=null;renderUsage()")
                page.locator('#usage-refresh').click()
                page.wait_for_function('() => !usageRequest')
                assert page.locator('.usage-remaining').all_text_contents() == ['Unavailable', 'Unavailable']
                assert page.locator('#usage-last-refresh').inner_text() == 'Last refresh Never'
                page.screenshot(path=str(evidence/'never-refreshed.png'))
                page.keyboard.press('Escape')
                # Project menu must paint and receive input above the workspace row.
                for width,height in [(1440,1000),(393,851),(320,420),(393,420)]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.locator('#project-menu-button').tap()
                    if width == 393 and height == 851:
                        page.locator('#project-menu').evaluate("m=>m.style.zIndex='auto'")
                        baseline_hit = page.locator('#project-menu').evaluate('(m)=>{const r=m.getBoundingClientRect();return m.contains(document.elementFromPoint(r.x+20,r.y+20));}')
                        assert not baseline_hit
                        page.screenshot(path=str(evidence/'baseline-menu-overlap.png'))
                        page.locator('#project-menu').evaluate("m=>m.style.removeProperty('z-index')")
                    surface = page.locator('#project-menu').evaluate("""menu => {
                      const r=menu.getBoundingClientRect(),nav=document.querySelector('.workspace-row').getBoundingClientRect();
                      const x=Math.max(r.left,nav.left)+20,y=Math.max(r.top,nav.top)+20;
                      const css=getComputedStyle(menu);
                      return {background:css.backgroundColor,zIndex:css.zIndex,
                        hitInside:menu.contains(document.elementFromPoint(x,y)),
                        rect:{left:r.left,right:r.right,bottom:r.bottom,top:r.top},
                        scrollable:menu.scrollHeight>menu.clientHeight};
                    }""")
                    assert surface['background'] == 'rgb(23, 29, 41)' and surface['hitInside'], surface
                    assert surface['rect']['left'] >= 0 and surface['rect']['right'] <= width
                    assert surface['rect']['bottom'] <= height and surface['scrollable']
                    page.screenshot(path=str(evidence/f'{width}x{height}-project-menu.png'))
                    page.keyboard.press('End')
                    page.locator('#project-menu').evaluate('(m)=>m.scrollTop=m.scrollHeight')
                    assert page.locator('#project-menu button').last.evaluate('(b)=>{const r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+20,r.y+20));}')
                    page.screenshot(path=str(evidence/f'{width}x{height}-project-menu-scrolled.png'))
                    page.keyboard.press('Escape')
                    assert page.locator('#project-menu-button').evaluate('(b)=>b===document.activeElement')
                    page.locator('#project-menu-button').tap()
                    alpha_id = page.evaluate("state.portfolio.projects.find(p=>p.name==='Alpha').id")
                    page.locator(f'#project-menu [data-project="{alpha_id}"]').tap()
                    page.wait_for_function("() => state.history && state.portfolio.projects.find(p=>p.id===state.project)?.name==='Alpha'")
                    assert page.locator('#project-menu').is_hidden()
                    page.locator('#project-menu-button').tap()
                    page.locator('#project-menu').get_by_role('button',name='Rename selected project…').tap()
                    page.locator('#editor-fields input').fill('Alpha renamed')
                    page.locator('#editor-form').get_by_role('button',name='Save',exact=True).tap()
                    page.wait_for_function("() => state.portfolio.projects.some(p=>p.display_name==='Alpha renamed')")
                    page.locator('#project-menu-button').tap()
                    page.locator('#project-menu').get_by_role('button',name='Beta',exact=True).tap()
                    page.wait_for_function("() => state.history && state.portfolio.projects.find(p=>p.id===state.project)?.name==='Beta'")
                    page.locator('#project-menu-button').tap()
                    page.locator('#message-input').tap()
                    assert page.locator('#project-menu').is_hidden()
                    # Document scrolling retains sticky menu layering outside chat layout.
                    page.locator('[data-view=settings]').tap()
                    page.evaluate("document.querySelector('#view-settings').style.minHeight='2000px';scrollTo(0,200)")
                    page.locator('#project-menu-button').tap()
                    assert page.locator('.navigation').bounding_box()['y'] == 0
                    assert page.locator('#project-menu').evaluate('(m)=>{const r=m.getBoundingClientRect();return m.contains(document.elementFromPoint(r.x+20,r.y+20));}')
                    page.screenshot(path=str(evidence/f'{width}x{height}-sticky-menu.png'))
                    page.keyboard.press('Escape')
                    page.evaluate('scrollTo(0,0)')
                    page.locator('[data-view=chat]').tap()
                    receipts.append({'project_menu_viewport':[width,height],**surface,'selection_rename_dismissal':'passed','sticky_document_scroll':'passed'})
                assert not errors, errors
                (evidence/'browser.json').write_text(json.dumps({'viewports':receipts,'page_errors':errors,'refresh_checks':{'successful_footer':successful_footer,'backend_failure_preserved':True,'network_failure_preserved':True,'recovery_advanced':True,'never_placeholder':True},'baseline_overlap_reproduced':True},indent=2))
                browser.close()
            print('Account usage desktop/mobile/narrow, exact values, unavailable/stale/error, scrolling, keyboard/touch passed')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

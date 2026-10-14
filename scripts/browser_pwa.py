"""PWA manifest/cache/standalone checks against disposable loopback data only."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_PWA_EVIDENCE', '/tmp/linko-pwa-browser'))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linko-pwa-') as temporary:
        root = Path(temporary)
        (root / 'projects' / 'Alpha').mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
        program = f"""import sys
sys.path.insert(0,'tests')
import uvicorn
from test_model_settings import CatalogServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,CatalogServer(s));app.state.registry.add(s.project_roots[0]/'Alpha')
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')
"""
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                cdp = page.context.new_cdp_session(page)
                cdp.send('Page.enable')
                manifest = cdp.send('Page.getAppManifest')
                assert not manifest['errors'], manifest
                data = json.loads(manifest['data'])
                assert data['display'] == 'standalone'
                assert data['name'] == 'Linko Bot Boss' and data['short_name'] == 'Bot Boss'
                assert data['id'] == data['scope'] == data['start_url'] == '/'
                assert page.title() == 'Linko Bot Boss'
                assert page.locator('meta[name=application-name]').get_attribute('content') == 'Linko Bot Boss'
                assert page.locator('meta[name=apple-mobile-web-app-title]').get_attribute('content') == 'Bot Boss'
                assert page.locator('#message-input').get_attribute('aria-label') == 'Message Bot Boss'
                installability = cdp.send('Page.getInstallabilityErrors')
                assert not installability['installabilityErrors'], installability
                assert page.evaluate('isSecureContext')
                assert page.evaluate('navigator.serviceWorker.getRegistrations().then(r=>r.length)') == 0
                icon_results = page.evaluate('''async icons => {
                  const results=[];
                  for(const icon of icons){
                    const img=new Image(); img.src=icon.src; await img.decode();
                    const canvas=document.createElement('canvas');canvas.width=canvas.height=img.width;
                    const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);
                    const pixels=ctx.getImageData(0,0,img.width,img.height).data;
                    let outside=0,transparent=0;
                    for(let y=0;y<img.height;y++)for(let x=0;x<img.width;x++){
                      const i=(y*img.width+x)*4;
                      if(pixels[i+3]!==255)transparent++;
                      if(icon.purpose==='maskable' && Math.hypot(x+0.5-256,y+0.5-256)>204.8 &&
                         (pixels[i]!==16||pixels[i+1]!==20||pixels[i+2]!==30))outside++;
                    }
                    results.push({src:icon.src,width:img.width,height:img.height,transparent,outside});
                  }return results;
                }''', data['icons'])
                assert all(i['transparent'] == i['outside'] == 0 for i in icon_results), icon_results
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('fixture-private-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.locator('#app').wait_for(state='visible')
                page.wait_for_function('() => state.history?.conversation.id')
                page.locator('#projects button').filter(has_text='Alpha').click()
                page.wait_for_function('() => state.history?.conversation.project_id === state.project && state.project')
                page.evaluate("localStorage.setItem('pwa-fixture','retained')")
                page.locator('#message-input').fill('Unsent fixture draft')
                page.reload()
                page.locator('#app').wait_for(state='visible')
                page.wait_for_function('() => state.history?.conversation.id')
                page.locator('#projects button').filter(has_text='Alpha').click()
                page.wait_for_function('() => state.history?.conversation.project_id === state.project && state.project')
                assert page.evaluate("localStorage.getItem('pwa-fixture')") == 'retained'
                page.wait_for_function("() => document.querySelector('#message-input').value === 'Unsent fixture draft'")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(evidence / 'mobile-browser.png'))
                # Also exercise the real standalone media query in Chromium app mode.
                browser.close()
                app_browser = p.chromium.launch_persistent_context(
                    str(root / 'app-profile'), headless=False, args=[f'--app={base}'],
                    viewport={'width': 390, 'height': 844})
                app_page = app_browser.pages[0]
                app_page.wait_for_load_state()
                app_page.wait_for_function("() => matchMedia('(display-mode: standalone)').matches")
                assert app_page.locator('#auth-screen').is_visible()
                app_page.locator('#username').fill('fixture')
                app_page.locator('#password').fill('fixture-private-password')
                app_page.locator('#login-button').click()
                app_page.locator('#app').wait_for(state='visible')
                app_page.wait_for_function('() => state.history?.conversation.id')
                app_page.locator('[data-view="settings"]').click()
                app_page.locator('#view-settings').wait_for(state='visible')
                app_page.locator('[data-view="tasks"]').click()
                app_page.locator('#view-tasks').wait_for(state='visible')
                app_page.locator('[data-view="chat"]').click()
                app_page.locator('#message-input').fill('Standalone fixture draft')
                assert app_page.locator('#send').is_visible()
                app_page.screenshot(path=str(evidence / 'standalone.png'))
                assert app_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                app_browser.close()
                assert not errors, errors
                report = {'browser': p.chromium.executable_path, 'manifest': manifest,
                          'installability': installability, 'icons': icon_results,
                          'secure_loopback': True, 'standalone_app_mode': True,
                          'auth_reload_draft_local_storage': 'passed', 'page_errors': errors,
                          'limits': 'Desktop Chromium app mode and localhost only; no physical Android or candidate HTTPS deployment.'}
                (evidence / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n')
                print('PWA manifest, installability, icons, cache contract tests, state reload and Chromium standalone: passed')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

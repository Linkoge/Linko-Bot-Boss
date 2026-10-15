"""Immediate favorite dismissal, saved ordering and failures on a disposable HTTP fixture."""
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from browser_model_scroll import PROGRAM
from playwright.sync_api import sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_RECALL_EVIDENCE', '/tmp/linko-recall-browser'))
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='linko-recall-') as directory:
        root = Path(directory)
        (root / 'projects').mkdir()
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                errors, chats, recalls = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: chats.append(request.post_data_json) if request.url.endswith('/api/chat') else None)
                page.on('request', lambda request: recalls.append(request.post_data_json) if request.url.endswith('/0/recall') else None)
                page.goto(url)
                page.locator('#username').fill('owner')
                page.locator('#password').fill('disposable-browser-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&!!state.history')

                def open_panel():
                    page.locator('#model-settings-button').click()
                    page.wait_for_function('()=>modelDialog.open&&!modelOpening&&!modelSaving')

                def choose(key):
                    page.locator(f'[data-choice="{key}"]').click()
                    page.wait_for_function('()=>!modelSaving')
                    assert page.locator('#model-error').inner_text() == ''

                def recall_synchronously():
                    result = page.evaluate('''()=>{
                        const b=document.querySelector('[data-choice="favorite-recall-0"]');
                        const started=performance.now();b.click();b.click();
                        return {closed:!modelDialog.open,focused:document.activeElement.id,elapsed:performance.now()-started};
                    }''')
                    assert result['closed'] and result['focused'] == 'model-settings-button', result
                    return result

                def last_route():
                    with sqlite3.connect(root / 'data/manager.sqlite3') as connection:
                        return connection.execute("SELECT model,effort FROM executions WHERE task_id IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()

                open_panel()
                choose('manager-Luna')
                choose('manager-codex-high')
                choose('favorite-insert-0')
                assert page.locator('#model-dialog').is_visible()  # Insert never recalls or closes.
                choose('manager-Sol')
                choose('manager-codex-low')
                bots = page.evaluate('modelPanel.settings.worker')
                held = []
                page.route('**/api/manager-favorites/0/recall', lambda route: held.append(route))
                result = recall_synchronously()
                page.wait_for_function('()=>modelSaving')
                page.locator('#message-input').fill('Send immediately after recall')
                page.locator('#send').click()
                page.wait_for_function('()=>state.pending.size===1')
                assert len(chats) == 0 and len(held) == 1 and len(recalls) == 1
                # Reopen while both recall and Send are waiting. It must not fetch stale choices.
                page.locator('#model-settings-button').click()
                assert page.locator('#model-dialog').is_visible()
                assert page.locator('[data-choice="manager-Astra"]').is_disabled()
                held.pop().continue_()
                page.wait_for_function('()=>!modelOpening&&!modelSaving&&!state.pending.size')
                assert len(chats) == 1 and len(recalls) == 1
                assert page.locator('[data-choice="favorite-recall-0"]').get_attribute('aria-pressed') == 'true'
                choose('manager-Astra')
                page.wait_for_function('()=>!state.execution')
                assert last_route() == ('gpt-6-luna', 'high'), last_route()
                assert page.evaluate('modelPanel.settings.worker') == bots
                results.append({'immediate_close': result, 'fast_send_route': last_route(), 'recall_requests': len(recalls)})
                page.unroute('**/api/manager-favorites/0/recall')

                for kind in ('stale', 'unavailable', 'network'):
                    if kind == 'network':
                        page.route('**/api/manager-favorites/0/recall', lambda route: route.abort('failed'))
                    else:
                        page.route('**/api/manager-favorites/0/recall', lambda route: route.fulfill(status=409, json={'detail': 'Favorite '+kind}))
                    count = len(chats)
                    recall_synchronously()
                    page.locator('#message-input').fill('Keep this draft on '+kind)
                    page.locator('#send').click()
                    page.wait_for_function('()=>!modelSaving&&!state.pending.size')
                    assert len(chats) == count
                    assert page.locator('#notice').is_visible()
                    assert 'not sent' in page.locator('#notice-text').inner_text()
                    assert page.locator('#message-input').input_value() == 'Keep this draft on '+kind
                    assert page.evaluate('submissions.size') == 0
                    open_panel()
                    assert page.locator('[data-choice="manager-Astra"]').get_attribute('aria-pressed') == 'true'
                    page.unroute('**/api/manager-favorites/0/recall')
                    choose('manager-Astra')  # An explicit confirmed choice clears the Send barrier.
                    results.append({'failed_recall': kind, 'draft_preserved': True, 'chat_not_submitted': True})

                # A canceled delayed open cannot reopen or replace a later dialog's state.
                page.locator('#model-close').click()
                reads = []
                page.route('**/api/model-settings', lambda route: reads.append(route) if route.request.method == 'GET' else route.continue_())
                page.locator('#model-settings-button').click()
                page.wait_for_function('()=>modelOpening')
                page.locator('#model-close').click()
                assert reads
                reads.pop().continue_()
                page.wait_for_function('()=>!modelOpening')
                assert not page.locator('#model-dialog').is_visible()
                page.unroute('**/api/model-settings')
                open_panel()
                recall_synchronously()
                page.wait_for_function('()=>!modelSaving')
                page.locator('#send').click()
                page.wait_for_function('()=>!state.pending.size&&!state.execution')
                assert last_route() == ('gpt-6-luna', 'high')
                assert len(chats) == 2
                assert not errors, errors
                results.append({'recovered_send_route': last_route(), 'page_errors': errors})
                page.screenshot(path=str(evidence / 'closed-recall.png'))
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    (evidence / 'recall-results.json').write_text(json.dumps(results, indent=2))
    print('Immediate recall close/focus, duplicate suppression, fast Send ordering, reopen races, stale/unavailable/network reconciliation and draft preservation passed')


if __name__ == '__main__':
    main()

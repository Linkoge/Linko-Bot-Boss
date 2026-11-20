"""Real Chromium: Compact in the chat menu (keyboard reachable), proposal review, confirm, and the short chat after."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from test_chat_open_start_browser import seed_history
from test_model_picker_browser import ROOT, browser_context, page  # noqa: F401

PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from test_chat_compaction import CompactServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
uvicorn.run(create_app(s, CompactServer(s), CompactServer), host='127.0.0.1', port=PORT, log_level='error')
'''


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp('compaction-browser')
    (root / 'projects').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'), 'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'),
           'AI_MANAGER_SECURE_COOKIE': '0', 'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM.replace('PORT', str(port))], env=env, cwd=ROOT)
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
            pytest.fail('Disposable server did not start')
        yield url, root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_compact_from_the_chat_menu_by_keyboard_then_confirm(page, browser_context):
    seed_history(browser_context)
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===30')
    current = page.evaluate('state.history.conversation.id')
    page.locator('#chat-menu-button').focus()
    page.keyboard.press('ArrowDown')
    labels = page.evaluate("()=>chatMenuButtons().map(b=>b.textContent||b.getAttribute('aria-label'))")
    assert labels[:4] == ['Clear', 'Archive all', 'Archive part 10', 'Compact']
    assert page.evaluate('document.activeElement.id') == 'clear-chat'
    for _ in range(3):
        page.keyboard.press('ArrowDown')
    assert page.evaluate('document.activeElement.id') == 'compact-chat'
    page.keyboard.press('ArrowUp')
    assert page.evaluate('document.activeElement.id') == 'archive-part-chat'
    page.keyboard.press('End')
    last = page.evaluate('document.activeElement.id')
    page.keyboard.press('ArrowDown')
    assert page.evaluate('document.activeElement.id') == 'clear-chat'  # wraps around
    page.keyboard.press('ArrowUp')
    assert page.evaluate('document.activeElement.id') == last
    page.keyboard.press('Escape')
    assert page.locator('#chat-menu').is_hidden()
    assert page.evaluate('document.activeElement.id') == 'chat-menu-button'
    page.keyboard.press('ArrowDown')
    for _ in range(3):
        page.keyboard.press('ArrowDown')
    page.keyboard.press('Enter')
    page.wait_for_function("()=>!document.querySelector('#chat-compact-submit').disabled")
    proposal = page.locator('#chat-compact-proposal').inner_text()
    assert 'Long-term memory (kept) · 1' in proposal and 'Short-term memory' in proposal
    assert 'Waiting for the Windows 10 PC' in proposal
    assert page.evaluate('state.history.messages.length') == 30  # nothing changes before Apply
    page.locator('#chat-compact-submit').click()
    page.wait_for_function('()=>state.history?.messages.length===1')
    assert page.evaluate('state.history.conversation.id') == current
    assert page.locator('#messages').inner_text().count('Chat compacted: 2 items kept') == 1
    context, url, _ = browser_context
    chats = context.request.get(url + '/api/chat-history').json()['conversations']
    assert [c['message_count'] for c in chats if c['archived_at']] == [155]


def test_chat_memory_settings_panel_shows_what_is_saved_and_used(page):
    page.locator('.tabs [data-view=settings]').click()
    page.wait_for_function("()=>document.querySelector('#chat-memory-effective').textContent.startsWith('Used:')")
    assert "Used: test-model · low (the manager's choice)" in page.locator('#chat-memory-effective').inner_text()
    page.locator('#chat-memory-model').select_option('test-model')
    page.locator('#chat-memory-effort').fill('high')
    page.locator('#chat-memory-threshold').fill('20000')
    page.locator('#chat-memory-form button.primary').click()
    page.wait_for_function("()=>document.querySelector('#chat-memory-status').textContent==='Saved.'")
    assert page.locator('#chat-memory-effective').inner_text().startswith('Used: test-model · high ·')
    page.reload()
    page.wait_for_function('()=>state.ready')
    page.locator('.tabs [data-view=settings]').click()
    page.wait_for_function("()=>document.querySelector('#chat-memory-threshold').value==='20000'")
    assert page.locator('#chat-memory-model').input_value() == 'test-model'

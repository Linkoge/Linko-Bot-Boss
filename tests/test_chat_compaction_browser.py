"""Real Chromium: Compact in the chat menu (keyboard reachable) compacts in the background with no dialog, a message
sent meanwhile is kept, and the short chat follows."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from ai_manager.db import Database
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
           'AI_MANAGER_SECURE_COOKIE': '0', 'PYTHONPATH': str(ROOT), 'COMPACT_DELAY': '2'}
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


def test_compact_from_the_chat_menu_by_keyboard_compacts_in_the_background(page, browser_context):
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
    # No dialog: the click returns at once and a status line says it runs in the background.
    page.wait_for_function("()=>document.querySelector('#compaction-notice-text').textContent.startsWith('Compacting chat in the background')")
    assert page.evaluate("document.querySelectorAll('dialog[open]').length") == 0
    assert page.evaluate('state.history.messages.length') == 30
    # The owner keeps chatting while the compaction runs; the message is kept after the summary.
    page.locator('#message-input').fill('Sent during compaction')
    page.locator('#send').click()
    page.wait_for_function("()=>!state.pending.size&&!state.execution")
    page.wait_for_function("()=>document.querySelector('#compaction-notice-text').textContent.startsWith('Chat compacted')",
                           timeout=20000)
    page.wait_for_function("()=>state.history?.messages.length<=3")
    assert page.evaluate('state.history.conversation.id') == current
    contents = page.evaluate('state.history.messages.map(m=>m.content)')
    assert contents[0].startswith('Chat compacted: 2 items kept') and contents[1] == 'Sent during compaction'
    assert page.locator('#messages').inner_text().count('Chat compacted: 2 items kept') == 1
    context, url, root = browser_context
    chats = context.request.get(url + '/api/chat-history').json()['conversations']
    assert [c['message_count'] for c in chats if c['archived_at']] == [155]
    page.locator('#compaction-notice-dismiss').click()
    assert page.locator('#compaction-notice').is_hidden()


def test_a_failed_compaction_leaves_the_chat_unchanged_with_a_notice(page, browser_context):
    seed_history(browser_context)
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    conversation = db.one("SELECT id FROM conversations WHERE context='global'")['id']
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===30')
    page.locator('#chat-menu-button').click()
    page.locator('#compact-chat').click()
    page.wait_for_function("()=>document.querySelector('#compaction-notice-text').textContent.startsWith('Compacting chat')")
    seed_history(browser_context)  # the chat the summary covers is replaced before it can be applied
    page.wait_for_function("()=>document.querySelector('#compaction-notice-text').textContent.includes('the chat is unchanged')",
                           timeout=20000)
    assert db.one('SELECT count(*) AS n FROM messages WHERE conversation_id=?', (conversation,))['n'] == 155
    assert page.evaluate("document.querySelectorAll('dialog[open]').length") == 0


def test_chat_memory_settings_panel_shows_what_is_saved_and_used(page):
    page.locator('.tabs [data-view=settings]').click()
    page.wait_for_function("()=>document.querySelector('#chat-memory-effective').textContent.startsWith('Used:')")
    assert "Latest Haiku" in page.locator('#chat-memory-model').inner_text() and "Used: claude-haiku-5-10 · low (Latest Haiku)" in page.locator('#chat-memory-effective').inner_text()
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



def test_every_transient_notification_disappears_after_five_seconds(page, browser_context):
    seed_history(browser_context)
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===30')
    page.evaluate("()=>{window.__t0=performance.now();notice('Something happened');"
                  "showCompactionNotice(state.project,{text:'Chat compacted: test',state:'done'});"
                  "$('chat-notice').textContent='Copied';$('chat-notice').hidden=false;"
                  "state.chatNoticeTimer=setTimeout(()=>$('chat-notice').hidden=true,NOTICE_MS);}")
    assert page.locator('#notice').is_visible() and page.locator('#compaction-notice').is_visible()
    page.wait_for_function("()=>$('notice').hidden&&$('compaction-notice').hidden&&$('chat-notice').hidden", timeout=9000)
    assert page.evaluate('performance.now()-window.__t0') >= 4500
    assert page.evaluate('state.history.messages.length') == 30  # transcript untouched

"""Real Chromium: the office-mode chat list (menu button, + New, sidebar, pin, rename, delete, search) on desktop and
mobile, and Main without any of it."""
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
uvicorn.run(create_app(s, FakeServer(s)), host='127.0.0.1', port=s.port, log_level='error')
'''


@contextmanager
def served(tmp_path, office=True, extra_env=None):
    (tmp_path / 'work').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'AI_MANAGER_DATA': str(tmp_path / 'data'), 'AI_MANAGER_PORT': str(port),
           'AI_MANAGER_SECURE_COOKIE': '0', 'PYTHONPATH': str(ROOT)}
    if office:
        env.update(AI_MANAGER_RESTRICTED='1', AI_MANAGER_PROJECT=str(tmp_path / 'work'))
    else:
        env.update(AI_MANAGER_PROJECT_ROOTS=str(tmp_path / 'work'))
    env.update(extra_env or {})
    process = subprocess.Popen([sys.executable, '-c', PROGRAM], env=env, cwd=ROOT)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(200):
            try:
                if httpx.get(url + '/api/auth-status', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                yield browser, url
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def sign_in(page, url, tmp_path):
    page.goto(url)
    page.locator('#username').fill('owner')
    page.locator('#password').fill(secrets.token_urlsafe(16))
    page.locator('#setup-token').fill((tmp_path / 'data/setup-token').read_text())
    page.locator('#login-button').click()
    page.wait_for_function('()=>state.ready&&!!state.history')


def send(page, text):
    page.locator('#message-input').fill(text)
    page.locator('#send').click()
    page.wait_for_function('text=>[...document.querySelectorAll("#messages .message.assistant")].some('
                           'n=>n.textContent.includes("Saved streamed answer"))&&state.history.messages.some('
                           'm=>m.content===text)', arg=text)


def on_top(page, selector):
    """The control is visible and nothing covers its centre."""
    return page.evaluate('''selector=>{const node=document.querySelector(selector),box=node.getBoundingClientRect();
        const hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
        return box.width>0&&!!hit&&(hit===node||node.contains(hit));}''', selector)


def titles(page):
    return page.locator('#chat-list .chat-item .chat-title').all_inner_texts()


@pytest.mark.parametrize('width', [1200, 390])
def test_office_chat_list_desktop_and_mobile(tmp_path, width):
    with served(tmp_path) as (browser, url):
        page = browser.new_page(viewport={'width': width, 'height': 800})
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        mobile = width < 900
        assert on_top(page, '#chat-list-button') and on_top(page, '#new-chat')
        if mobile:  # narrow screens start with the list closed
            assert not page.locator('#chat-sidebar').is_visible()
            page.locator('#chat-list-button').click()
        page.wait_for_selector('#chat-sidebar #chat-list .chat-item')
        assert page.locator('#chat-list-button').get_attribute('aria-expanded') == 'true'
        if mobile:  # the overlay leaves both buttons usable on top
            assert page.locator('#chat-sidebar-backdrop').is_visible()
            assert on_top(page, '#chat-list-button') and on_top(page, '#new-chat')
            page.locator('#chat-list-button').click()
            assert not page.locator('#chat-sidebar').is_visible()
        else:  # desktop: the list stays open beside the chat
            sidebar, chat = page.locator('#chat-sidebar').bounding_box(), page.locator('#messages').bounding_box()
            assert sidebar['x'] + sidebar['width'] <= chat['x'] + 1
        send(page, 'Write the ACME invoice')
        first = page.evaluate('()=>state.history.conversation.id')
        # Sending must not scroll the chat under the open list or the buttons off screen.
        assert on_top(page, '#chat-list-button') and on_top(page, '#new-chat')
        if not mobile:
            sidebar, chat = page.locator('#chat-sidebar').bounding_box(), page.locator('#messages').bounding_box()
            assert sidebar['x'] + sidebar['width'] <= chat['x'] + 1
        # + New: one click, a fresh empty chat.
        page.locator('#new-chat').click()
        page.wait_for_function('id=>state.history&&state.history.conversation.id!==id', arg=first)
        assert page.locator('#messages .message').count() == 0
        send(page, 'Plan the May offsite')
        second = page.evaluate('()=>state.history.conversation.id')
        if mobile:
            page.locator('#chat-list-button').click()
        # Placeholder titles (the fake model has no Haiku), most recent first, current highlighted.
        page.wait_for_function('()=>[...document.querySelectorAll("#chat-list .chat-title")].map(n=>n.textContent)'
                               '.join("|")==="Plan the May offsite|Write the ACME invoice"')
        assert titles(page) == ['Plan the May offsite', 'Write the ACME invoice']
        assert page.locator('#chat-list .chat-item.current').get_attribute('data-chat') == second
        # Pick the older chat: its messages come back; mobile closes the overlay.
        page.locator(f'#chat-list .chat-item[data-chat="{first}"] .chat-open').click()
        page.wait_for_function('id=>state.history&&state.history.conversation.id===id', arg=first)
        assert 'Write the ACME invoice' in page.locator('#messages').inner_text()
        if mobile:
            page.wait_for_function('()=>document.querySelector("#chat-sidebar").hidden')
            page.locator('#chat-list-button').click()
        page.wait_for_selector(f'#chat-list .chat-item.current[data-chat="{first}"]')
        # Pinned chats come first: pin the older one and it moves above the more recently active one.
        page.locator(f'#chat-list .chat-item[data-chat="{first}"] .chat-pin').click()
        page.wait_for_selector(f'#chat-list .chat-item[data-chat="{first}"] .chat-pin-mark')
        assert page.locator('#chat-list .chat-item').first.get_attribute('data-chat') == first
        page.locator(f'#chat-list .chat-item[data-chat="{first}"] .chat-pin').click()
        page.wait_for_function('id=>!document.querySelector(`#chat-list .chat-item[data-chat="${id}"] .chat-pin-mark`)',
                               arg=first)
        assert page.locator('#chat-list .chat-item').first.get_attribute('data-chat') == second
        # Rename.
        page.locator(f'#chat-list .chat-item[data-chat="{first}"] .chat-rename').click()
        page.locator('#editor-fields input[name=title]').fill('ACME invoice')
        page.locator('#editor-form .primary').click()
        page.wait_for_function('()=>[...document.querySelectorAll("#chat-list .chat-title")].some(n=>n.textContent==="ACME invoice")')
        # Search filters server-side by title and message text.
        page.locator('#chat-search').fill('offsite')
        page.wait_for_function('()=>document.querySelectorAll("#chat-list .chat-item").length===1')
        assert titles(page) == ['Plan the May offsite']
        page.locator('#chat-search').fill('')
        page.wait_for_function('()=>document.querySelectorAll("#chat-list .chat-item").length===2')
        # Delete with a confirm: the open chat goes to the archive and the other chat opens.
        page.locator(f'#chat-list .chat-item[data-chat="{first}"] .chat-delete').click()
        assert page.locator('#office-confirm').is_visible()
        page.locator('#office-confirm-submit').click()
        # The switch closes the mobile list only after the new chat loaded: wait for it to finish before toggling.
        page.wait_for_function('id=>state.history&&state.history.conversation.id===id&&!officeChats.switching', arg=second)
        if mobile:
            page.wait_for_function('()=>document.querySelector("#chat-sidebar").hidden')
            page.locator('#chat-list-button').click()
        page.wait_for_function('()=>document.querySelectorAll("#chat-list .chat-item").length===1')
        # Chat controls, voice and attachments stay in every chat.
        for visible in ('#message-input', '#microphone', '#attach', '#humanizer-button'):
            assert page.locator(visible).is_visible() or mobile, visible
        if mobile:
            page.locator('#chat-list-button').click()
        for visible in ('#message-input', '#microphone', '#attach', '#humanizer-button'):
            assert page.locator(visible).is_visible(), visible
        # Settings in office mode: only Instructions and Sign out; chat memory numbers follow Main.
        if mobile:
            page.locator('#chat-list-button').click()
        page.locator('#office-settings').click()
        page.wait_for_function('()=>!$("view-settings").hidden')
        assert not page.locator('#chat-memory-panel').is_visible()
        assert not page.locator('#chat-memory-leave').is_visible()
        assert page.locator('#global-instructions').is_visible() and page.locator('#logout').is_visible()
        assert errors == []


def test_main_shows_no_office_chat_controls(tmp_path):
    with served(tmp_path, office=False) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 800})
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        for width in (1200, 390):
            page.set_viewport_size({'width': width, 'height': 800})
            page.evaluate("()=>showView('chat')")
            assert page.evaluate("()=>document.body.classList.contains('office')") is False
            for hidden in ('#chat-sidebar', '#office-bar', '#chat-list-button', '#new-chat', '#chat-sidebar-backdrop'):
                assert not page.locator(hidden).is_visible(), hidden
            # Main's own controls are where they were: the tabs and project row first, nothing pushed aside.
            for visible in ('.project-row', '.tabs', '#chat-menu-button', '#message-input'):
                assert page.locator(visible).is_visible(), visible
            row, tabs = page.locator('.workspace-row').bounding_box(), page.locator('.tabs').bounding_box()
            assert tabs['x'] - row['x'] < 20
            assert page.evaluate("()=>getComputedStyle(document.querySelector('main')).marginLeft") != '280px'
            page.evaluate("()=>showView('settings')")
            assert not page.locator('#chat-memory-leave').is_visible()
            assert page.locator('#chat-memory-threshold').is_visible()
            assert errors == []

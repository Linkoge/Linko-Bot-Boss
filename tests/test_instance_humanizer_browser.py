"""Real Chromium: instances get Humanizer (not Clear chat) in the chat menu and a Georgian UI; Main is unchanged."""
import pytest
from test_office_chats_browser import served, sign_in


def open_menu(page):
    page.locator('#chat-menu-button').click()
    page.wait_for_function("()=>!document.getElementById('chat-menu').hidden")


def test_instance_menu_has_humanizer_sheet_and_georgian(tmp_path):
    with served(tmp_path, office=True) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        open_menu(page)
        assert page.locator('#humanizer-chat').is_visible()
        assert not page.locator('#clear-chat').is_visible()
        icon = page.locator('#humanizer-chat svg')
        box = icon.bounding_box()
        assert box['width'] == box['height'] and icon.locator('circle').count() == 1
        page.locator('#humanizer-chat').click()
        page.wait_for_function("()=>document.getElementById('humanizer-dialog').open")
        assert page.locator('#writing-heading').inner_text() == 'Humanizer'
        assert page.locator('#writing-label-en').inner_text() == 'Style copy — English'
        assert page.locator('#writing-label-ka').inner_text() == 'Style copy — Georgian'
        page.locator('#humanizer-close').click()
        page.locator('#office-settings').click()
        assert page.locator('#language-select').is_visible()
        page.locator('#language-select').select_option('ka')
        page.wait_for_function("()=>document.querySelector('#language-heading').textContent==='ენა'")
        assert page.evaluate("()=>localStorage.getItem('linko-language')") == 'ka'
        assert page.locator('#session-panel h2').inner_text() == 'სესია'
        open_menu(page)
        assert page.locator('#humanizer-chat span:last-child').inner_text() == 'ჰიუმანაიზერი'
        page.locator('#humanizer-chat').click()
        page.wait_for_function("()=>document.getElementById('writing-label-ka').textContent==='სტილის ნიმუში — ქართული'")
        assert page.locator('#writing-heading').inner_text() == 'წერის სტილი' or page.locator('#writing-heading').inner_text()
        page.locator('#humanizer-close').click()
        page.reload()
        page.wait_for_function('()=>state.ready')
        page.wait_for_function("()=>document.querySelector('#new-chat').textContent==='+ ახალი'")
        page.evaluate("()=>{const s=document.getElementById('language-select');s.value='en';s.onchange();}")
        page.wait_for_function("()=>document.querySelector('#new-chat').textContent==='+ New'")
        assert not errors


def test_main_menu_is_unchanged_and_has_no_language(tmp_path):
    with served(tmp_path, office=False) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_default_timeout(10000)
        sign_in(page, url, tmp_path)
        open_menu(page)
        assert page.locator('#clear-chat').is_visible()
        assert not page.locator('#humanizer-chat').is_visible()
        page.keyboard.press('Escape')
        page.locator('.tabs [data-view=settings]').click()
        assert not page.locator('#language-panel').is_visible()
        assert page.locator('#writing-panel').is_visible()
        assert page.locator('#writing-heading').inner_text() == 'Writing'
        assert page.locator('html').get_attribute('lang') == 'en'

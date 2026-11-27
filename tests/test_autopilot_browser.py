"""Real Chromium: the compact Autopilot switch in the harness menu and the tiny Settings > Autopilot panel.
Not set up: the switch is disabled with "Not set up · Set up", the link opens the panel with its one copyable command.
Set up (config lock + a fake sudo that succeeds): the switch turns it off/on at once, persisted locally, with a dot on
the harness button. An instance shows neither."""
import os

import pytest
from test_office_chats_browser import served, sign_in


def machine_env(tmp_path, set_up):
    """A fake `sudo` first on PATH makes the OS lock deterministic; set_up adds the config lock too."""
    fake = tmp_path / 'fakebin'
    fake.mkdir()
    sudo = fake / 'sudo'
    sudo.write_text(f'#!/bin/sh\nexit {0 if set_up else 1}\n')
    sudo.chmod(0o755)
    return {'PATH': f"{fake}:{os.environ.get('PATH', '')}", 'AI_MANAGER_ENV_FILE': str(tmp_path / 'linko.env'),
           'AI_MANAGER_AUTOPILOT': 'full' if set_up else '', 'AI_MANAGER_MACHINE_ROLE': ''}


def open_menu(page):
    page.locator('#harness-button').click()
    page.wait_for_function("()=>!$('harness-menu').hidden&&!$('autopilot-group').hidden")


@pytest.mark.parametrize('mode', ['not-set-up', 'set-up', 'instance'])
def test_autopilot_switch_and_panel(tmp_path, mode):
    office = mode == 'instance'
    with served(tmp_path, office=office, extra_env=machine_env(tmp_path, mode == 'set-up')) as (browser, url):
        context = browser.new_context(viewport={'width': 1200, 'height': 900},
                                      permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        assert page.locator('#autopilot-badge').count() == 0  # the old header badge is gone
        if office:
            assert not page.locator('#harness-button').is_visible()
            assert page.evaluate("()=>$('autopilot-group').hidden")
            page.locator('#office-settings').click()
            page.wait_for_function("()=>!$('view-settings').hidden")
            assert not page.locator('#autopilot-panel').is_visible()
            assert not errors
            return
        open_menu(page)
        toggle = page.locator('#autopilot-toggle')
        box = page.locator('#autopilot-group').bounding_box()
        assert box['height'] < 90  # one compact row (plus the one-line hint when not set up)
        if mode == 'not-set-up':
            assert toggle.is_disabled() and not toggle.is_checked()
            assert page.locator('#autopilot-setup-hint').inner_text() == 'Not set up · Set up'
            assert page.locator('#harness-button').get_attribute('data-autopilot') == 'off'
            page.locator('#autopilot-setup-link').click()
            page.wait_for_function("()=>!$('view-settings').hidden&&autopilot.data!==null")
            assert page.locator('#harness-menu').is_hidden()
            assert page.locator('#autopilot-state').inner_text() == 'Set up: no · off'
            command = page.locator('#autopilot-setup-command').inner_text()
            assert command.startswith('sudo bash /') and '/scripts/autopilot_setup.sh --user ' in command
            page.locator('#autopilot-panel [data-copy=autopilot-setup-command]').click()
            page.wait_for_function("()=>document.querySelector('[data-copy=autopilot-setup-command]').textContent==='Copied'")
            assert page.evaluate("()=>navigator.clipboard.readText()") == command
            assert page.locator('#autopilot-details').get_attribute('href') == '/api/autopilot/docs'
            assert 'No goals yet' in page.locator('#autopilot-goals').inner_text()
            assert not errors
            return
        assert toggle.is_enabled() and toggle.is_checked() and page.locator('#autopilot-setup-hint').is_hidden()
        assert page.locator('#harness-button').get_attribute('data-autopilot') == 'on'
        toggle.click()
        page.wait_for_function("()=>state.autopilotOn===false&&!$('autopilot-toggle').disabled")
        assert not toggle.is_checked() and page.locator('#harness-button').get_attribute('data-autopilot') == 'off'
        page.reload()
        page.wait_for_function('()=>state.ready&&!!state.history')
        open_menu(page)
        page.wait_for_function("()=>state.autopilotOn===false")
        assert not page.locator('#autopilot-toggle').is_checked()  # saved on this machine
        page.locator('#autopilot-toggle').click()
        page.wait_for_function("()=>state.autopilotOn===true&&$('harness-button').dataset.autopilot==='on'")
        page.keyboard.press('Escape')
        page.locator('.tabs [data-view=settings]').click()
        page.wait_for_function("()=>autopilot.data!==null&&$('autopilot-state').textContent!==''")
        assert page.locator('#autopilot-state').inner_text() == 'Set up: yes · on'
        assert page.locator('#autopilot-setup').is_hidden()
        assert not errors

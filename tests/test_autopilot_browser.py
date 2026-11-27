"""Real Chromium: the header badge and Settings > Autopilot on Main (copy buttons, live lock status, goal form);
an instance shows neither."""
import pytest
from test_office_chats_browser import served, sign_in


@pytest.mark.parametrize('office', [False, True], ids=['main', 'instance'])
def test_autopilot_badge_and_panel(tmp_path, office):
    with served(tmp_path, office=office) as (browser, url):
        context = browser.new_context(viewport={'width': 1200, 'height': 900},
                                      permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        if office:
            page.locator('#office-settings').click()
            page.wait_for_function("()=>!$('view-settings').hidden")
            assert not page.locator('#autopilot-badge').is_visible()
            assert not page.locator('#autopilot-panel').is_visible()
            assert not errors
            return
        page.wait_for_function("()=>!$('autopilot-badge').hidden")
        level = page.evaluate("()=>state.autopilot")
        assert level in ('off', 'partial')  # the suite never has the config lock; sudo depends on the host
        assert page.locator('#autopilot-badge').inner_text() == f'Autopilot: {level}'
        page.locator('#autopilot-badge').click()
        page.wait_for_function("()=>autopilot.data!==null&&$('autopilot-env-line').textContent!==''")
        assert page.locator('#autopilot-panel').is_visible()
        assert page.locator('#autopilot-env-line').inner_text() == 'AI_MANAGER_AUTOPILOT=full'
        assert page.locator('#autopilot-env-file').inner_text().endswith('/.config/linko-bot-manager.env')
        assert 'Detected: no.' in page.locator('#autopilot-config-lock').inner_text()
        assert 'NOPASSWD: ALL' in page.locator('#autopilot-sudoers-content').inner_text()
        assert 'sudo visudo -cf /etc/sudoers.d/linko-autopilot' in page.locator('#autopilot-sudoers-validate').inner_text()
        assert page.locator('#autopilot-approvals li').count() == 5
        assert page.locator('#autopilot-goal-form').is_hidden()  # no config lock: goals cannot be saved here
        button = page.locator('#autopilot-panel [data-copy=autopilot-sudoers-install]')
        button.click()
        page.wait_for_function("()=>document.querySelector('[data-copy=autopilot-sudoers-install]').textContent==='Copied'")
        copied = page.evaluate("()=>navigator.clipboard.readText()")
        assert copied == page.locator('#autopilot-sudoers-install').inner_text() and 'visudo -cf' in copied
        page.locator('#autopilot-pause').click()
        page.wait_for_function("()=>$('autopilot-pause').textContent==='Resume Autopilot'")
        assert '(paused)' in page.locator('#autopilot-state').inner_text()
        assert not errors

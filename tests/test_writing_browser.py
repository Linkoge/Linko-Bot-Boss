"""Real Chromium: Settings > Writing saves the style samples and default rates, on Main and on an instance."""
import pytest
from test_office_chats_browser import served, sign_in

SAMPLE = "I wrote this on the train. It was late, so I had time.\n\nWas it worth it? Probably."
GEORGIAN = "დილით ადრე გავედით. გზა მშვიდი იყო."


@pytest.mark.parametrize('office', [False, True], ids=['main', 'instance'])
def test_writing_panel_saves_and_reloads(tmp_path, office):
    with served(tmp_path, office=office) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        if office:
            page.locator('#humanizer-button').click()
        else:
            page.locator('.tabs [data-view=settings]').click()
        page.wait_for_function("()=>writingSettings!==null&&$('writing-typo-rate').value==='0.2'")
        assert page.locator('#writing-panel').is_visible() and page.locator('#writing-panel h2').inner_text() == ('Humanizer' if office else 'Writing')
        assert page.locator('#writing-stats-en').inner_text() == 'No sample saved.'
        page.locator('#writing-sample-en').fill(SAMPLE)
        page.locator('#writing-sample-ka').fill(GEORGIAN)
        page.locator('#writing-typo-rate').fill('0.5')
        page.locator('#writing-punct-rate').fill('3')
        page.locator('#writing-form button.primary').click()
        saved = 'Humanizer settings saved.' if office else 'Writing settings saved.'
        page.wait_for_function("text=>$('writing-status').textContent===text", arg=saved)
        assert page.locator('#writing-stats-en').inner_text().startswith('Measured: 4 sentences')
        assert page.locator('#writing-stats-ka').inner_text().startswith('Measured: 2 sentences')
        page.reload()
        page.wait_for_function('()=>state.ready')
        page.evaluate("()=>loadWritingSettings()")
        page.wait_for_function("()=>$('writing-typo-rate').value==='0.5'")
        assert page.locator('#writing-sample-en').input_value() == SAMPLE
        assert page.locator('#writing-sample-ka').input_value() == GEORGIAN
        assert page.locator('#writing-punct-rate').input_value() == '3'
        assert not errors

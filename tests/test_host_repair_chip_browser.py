"""Real Chromium: the host repair chip renders from the portfolio, follows its conversation, and hides when restricted."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import json

from test_model_picker_browser import browser_context, page, server  # noqa: F401


def request(**extra):
    return {'id': 'r1', 'state': 'running', 'conversation_id': 'elsewhere', 'conversation_known': False, 'attempt': 1,
            'max_attempts': 5, 'elapsed': 720, 'idle': 30, 'stale': False, 'last_step': 'command: apt-get install qemu',
            **extra}


def inject(page, holder):
    def handle(route):
        response = route.fetch()
        body = response.json()
        body['host_repair'] = holder['rows']
        route.fulfill(response=response, body=json.dumps(body))
    page.route('**/api/portfolio*', handle)


def test_chip_shows_states_scoping_stale_and_restricted(page):
    holder = {'rows': [request()]}
    inject(page, holder)
    page.evaluate('()=>refresh()')
    chip = page.locator('#progress-chip')
    chip.wait_for(state='visible')
    assert chip.inner_text() == 'Host agent working · 12 min · attempt 1/5 · command: apt-get install qemu'
    assert chip.get_attribute('data-phase') == 'host'
    holder['rows'] = [request(state='retrying', attempt=2, stale=True, idle=7800)]
    page.evaluate('()=>refresh()')
    page.wait_for_function("()=>$('progress-chip').textContent.includes('possibly stuck')")
    assert 'no activity for 130 min' in chip.inner_text()
    holder['rows'] = [request(state='done', last_step='')]
    page.evaluate('()=>refresh()')
    page.wait_for_function("()=>$('progress-chip').textContent.startsWith('Host agent finished')")
    # Known conversation that is not the open chat: hidden here, shown in its own chat.
    holder['rows'] = [request(conversation_known=True)]
    page.evaluate('()=>refresh()')
    chip.wait_for(state='hidden')
    holder['rows'] = [request(conversation_known=True, conversation_id=page.evaluate('()=>state.history.conversation.id'))]
    page.evaluate('()=>refresh()')
    chip.wait_for(state='visible')
    page.evaluate('()=>{state.restricted=true;renderProgress();}')
    chip.wait_for(state='hidden')

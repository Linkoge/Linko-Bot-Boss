"""Real Chromium: muted progress at the composer bottom-left, without taking layout space, clearing at the end."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import json
import time

import pytest
from test_model_picker_browser import browser_context, page, server  # noqa: F401

PHASES = [('agent', 'Working'), ('checks', 'Running checks'), ('repair', 'Repair round 2 of 5'), ('merge', 'Merging'),
          ('deploy', 'Deploying: running tests'), ('deploy', 'Health check'), ('queued', 'Queued'),
          ('report', 'Reporting result')]
IDLE = {'active': False, 'phase': None, 'label': '', 'project_id': None, 'task_id': None, 'since': None}


def inject(page, holder):
    """Portfolio responses carry the progress the test sets; everything else is the real server."""
    def handle(route):
        response = route.fetch()
        body = response.json()
        body['progress'] = holder['progress']
        route.fulfill(response=response, body=json.dumps(body))
    page.route('**/api/portfolio*', handle)


def boxes(page):
    return [page.locator(s).bounding_box() for s in ('#messages', '#chat-form', '.composer-area')]


def phase(name, label):
    return {'active': True, 'phase': name, 'label': label, 'project_id': None, 'task_id': 't', 'since': time.time()}


def test_chip_is_hidden_when_idle_and_absolutely_positioned(page):
    assert page.locator('#progress-chip').is_hidden()
    assert page.evaluate("()=>getComputedStyle($('progress-chip')).position") == 'absolute'
    assert page.evaluate("()=>getComputedStyle($('progress-chip')).pointerEvents") == 'none'


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640)])
def test_every_phase_shows_its_label_without_taking_layout_space_then_clears(page, width, height):
    page.set_viewport_size({'width': width, 'height': height})
    holder = {'progress': IDLE}
    inject(page, holder)
    page.evaluate('()=>refresh()')
    idle = boxes(page)
    for name, label in PHASES:
        holder['progress'] = phase(name, label)
        page.evaluate('()=>refresh()')
        chip = page.locator('#progress-chip')
        chip.wait_for(state='visible')
        assert chip.inner_text() == label and chip.get_attribute('data-phase') == name
        assert page.locator('#status-label').inner_text() == label
        assert boxes(page) == idle  # no extra space anywhere
        box, form = chip.bounding_box(), page.locator('#chat-form').bounding_box()
        assert form['x'] <= box['x'] and box['x'] + box['width'] <= form['x'] + form['width'] + 1
        assert form['y'] <= box['y'] and box['y'] + box['height'] <= form['y'] + form['height'] + 1
        assert abs(box['x'] - form['x'] - 9) <= 1
        assert abs(form['y'] + form['height'] - box['y'] - box['height'] - 7) <= 1
        style = chip.evaluate('''n=>{
          const s=getComputedStyle(n),timer=getComputedStyle($('audio-status'));
          return {color:s.color,timerColor:timer.color,size:s.fontSize,timerSize:timer.fontSize,
            border:s.borderWidth,radius:s.borderRadius,background:s.backgroundColor,
            dot:getComputedStyle(n,'::before').content};
        }''')
        assert style['color'] == style['timerColor'] == 'rgb(142, 155, 178)'
        assert style['size'] == style['timerSize'] == '11px'
        assert style['border'] == '0px' and style['radius'] == '0px'
        assert style['background'] == 'rgba(0, 0, 0, 0)' and style['dot'] == 'none'
    holder['progress'] = IDLE  # done, failed, rolled back or stopped: nothing in progress
    page.evaluate('()=>refresh()')
    page.locator('#progress-chip').wait_for(state='hidden')
    assert page.locator('#progress-chip').inner_text() == '' and boxes(page) == idle
    assert page.locator('#status-label').inner_text() == 'Ready to help'


def test_manager_reply_keeps_its_own_indicator_and_stale_state_does_not_spin(page):
    holder = {'progress': phase('merge', 'Merging')}
    inject(page, holder)
    page.evaluate('()=>refresh()')
    page.locator('#progress-chip').wait_for(state='visible')
    page.evaluate("()=>{state.execution='reply';renderProgress();}")  # a streaming reply shows the message + Stop instead
    assert page.locator('#progress-chip').is_hidden()
    page.evaluate("()=>{state.execution=null;renderProgress();}")
    assert page.locator('#progress-chip').is_visible()
    page.evaluate('()=>renderProgress(true)')  # what the stale timer does when polling stops answering
    assert page.locator('#progress-chip').is_hidden() and page.locator('#progress-chip').inner_text() == ''
    assert page.evaluate('()=>PROGRESS_STALE_MS') > 3000 * 2


def test_switching_project_clears_the_previous_project_phase(page):
    holder = {'progress': phase('checks', 'Running checks')}
    inject(page, holder)
    page.evaluate('()=>refresh()')
    page.locator('#progress-chip').wait_for(state='visible')
    holder['progress'] = IDLE
    page.evaluate('()=>selectProject(null)')
    page.locator('#progress-chip').wait_for(state='hidden')


def test_deploy_after_merge_shows_from_the_saved_request_and_survives_reload(page, browser_context):
    _, _, root = browser_context
    deploy = root / 'data/deploy'
    deploy.mkdir(parents=True, exist_ok=True)
    request = {'id': 'browser1', 'project_id': 'any', 'task_id': 't', 'requested_at': time.time()}
    try:
        (deploy / 'request.json').write_text(json.dumps(request))
        (deploy / 'state.json').write_text(json.dumps({'id': 'browser1', 'phase': 'testing', 'at': time.time()}))
        page.reload()
        page.wait_for_function('()=>state.ready&&!!state.history')
        page.wait_for_function("()=>!$('progress-chip').hidden")
        assert page.locator('#progress-chip').inner_text() == 'Deploying: running tests'
        (deploy / 'state.json').write_text(json.dumps({'id': 'browser1', 'phase': 'verifying', 'at': time.time()}))
        page.evaluate('()=>refresh()')
        page.wait_for_function("()=>$('progress-chip').textContent==='Health check'")
    finally:
        (deploy / 'request.json').unlink(missing_ok=True)
        (deploy / 'state.json').unlink(missing_ok=True)
    page.evaluate('()=>refresh()')
    page.locator('#progress-chip').wait_for(state='hidden')
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    assert page.locator('#progress-chip').is_hidden()

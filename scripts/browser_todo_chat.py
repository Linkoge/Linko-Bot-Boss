"""Disposable browser acceptance for explicit To-Do handoff, trash and stable Agents."""
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    evidence = Path(os.environ.get('LINKO_BUNDLE_EVIDENCE', '/tmp/linko-todo-chat-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linko-todo-chat-') as directory:
        root = Path(directory)
        for name in ('Alpha', 'Beta'):
            (root / 'projects' / name).mkdir(parents=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        program = f'''import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
from ai_manager.todos import Todos
s=Settings();app=create_app(s,FakeServer(s))
for name in ('Alpha','Beta'):app.state.registry.add(s.project_roots[0]/name)
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')'''
        env = {**os.environ, 'AI_MANAGER_DATA': str(root / 'data'),
               'AI_MANAGER_PROJECT_ROOTS': str(root / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0'}
        process = subprocess.Popen([os.sys.executable, '-c', program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/auth-status').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 393, 'height': 851})
                errors, chats = [], []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda r: chats.append(r.post_data_json) if r.url.endswith('/api/chat') else None)
                page.goto(base)
                page.locator('#username').fill('fixture')
                page.locator('#password').fill('disposable-password')
                page.locator('#setup-token').fill((root / 'data/setup-token').read_text())
                page.locator('#login-button').click()
                page.wait_for_function('()=>state.ready&&state.history')
                alpha, beta = page.evaluate('state.portfolio.projects.map(p=>p.id)')
                page.evaluate('(id)=>selectProject(id)', alpha)
                page.locator('[data-view=todo]').click()
                page.locator('#todo-add').click()
                page.locator('[name=title]').fill('Explicit start only')
                page.locator('[name=details]').fill('Preserve original details')
                page.locator('#editor-form .primary').click()
                page.wait_for_function('()=>state.todos.length===1')
                assert not chats
                page.locator('#todo-list button', has_text='Edit').click()
                page.locator('[name=details]').fill('Edited original details')
                page.locator('#editor-form .primary').click()
                page.wait_for_function("()=>state.todos[0].details==='Edited original details'")
                assert not chats
                page.evaluate("() => {$('message-input').value='unsent draft';state.drafts.set(state.project,'unsent draft');}")
                page.route('**/api/todos/*/start', lambda route: route.fulfill(status=503, content_type='application/json', body='{"detail":"fixture dispatch unavailable"}'))
                page.locator('#todo-list button', has_text='Start').click()
                page.wait_for_timeout(300)
                assert page.locator('#todo-list article').count() == 1
                page.unroute('**/api/todos/*/start')
                page.locator('#todo-list button', has_text='Start').click()
                page.wait_for_timeout(300)
                page.wait_for_function("()=>!todoStarts.size&&$('notice-text').textContent.includes('To-Do started')")
                assert page.locator('#todo-list article').count() == 0
                assert page.locator('#message-input').input_value() == 'unsent draft'
                tasks = page.evaluate("()=>api('/api/portfolio?project_id='+state.project)")['tasks']
                assert len(tasks) == 1 and tasks[0]['description'] == 'Explicit start only\n\nEdited original details'
                assert not chats
                page.locator('[data-view=chat]').click()
                page.locator('#send').click()
                page.wait_for_function('()=>state.history.messages.length>0&&!state.execution&&!state.pending.size')

                def chat_action(control):
                    page.locator('#chat-menu-button').click()
                    page.locator('#' + control).click()
                    endpoint='/api/chat/clear' if control=='clear-chat' else '/api/chat/trash/restore'
                    with page.expect_response(lambda response:response.url.endswith(endpoint) and response.request.method=='POST') as changed:
                        page.locator('#chat-confirm-submit').click()
                    assert changed.value.status==200, changed.value.text()
                    page.wait_for_function("()=>!$('chat-confirm').open&&!$('chat-confirm-submit').disabled")
                    page.evaluate("()=>loadHistory()")

                chat_action('clear-chat')
                first = page.evaluate('state.history.trash.conversation_id')
                assert page.evaluate('state.history.messages.length') == 0
                page.locator('#attachment-input').set_input_files({'name':'draft.txt','mimeType':'text/plain','buffer':b'keep attachment bytes'})
                page.wait_for_function('()=>attachmentDraft(state.project).length===1')
                page.locator('#chat-menu-button').click()
                assert page.locator('#restore-trash').is_disabled()
                page.locator('#chat-menu-button').click()
                page.locator('#attachment-list button').click()
                chat_action('restore-trash')
                assert page.evaluate('state.history.conversation.id') == first
                assert page.evaluate('state.history.trash') is None
                for width, height in [(1440,1000), (393,851), (320,420)]:
                    page.set_viewport_size({'width':width, 'height':height})
                    chat_action('clear-chat')
                    trashed = page.evaluate('state.history.trash.conversation_id')
                    page.locator('#message-input').fill('new chat for swap')
                    page.locator('#send').click()
                    page.wait_for_function('()=>!state.pending.size&&!state.execution&&state.history.messages.length>=2')
                    current = page.evaluate('state.history.conversation.id')
                    chat_action('restore-trash')
                    assert page.evaluate('state.history.conversation.id') == trashed
                    assert page.evaluate('state.history.trash.conversation_id') == current
                    chat_action('restore-trash')
                    assert page.evaluate('state.history.conversation.id') == current
                    chat_action('clear-chat')
                    assert page.evaluate('state.history.trash.conversation_id') == current, page.evaluate('({current:state.history.conversation.id,trash:state.history.trash})')
                    page.locator('#chat-menu-button').click()
                    left = page.locator('#restore-trash').bounding_box()
                    right = page.locator('#empty-trash').bounding_box()
                    assert right['x'] > left['x'] and right['height'] >= 42
                    assert right['x']+right['width'] <= width
                    page.screenshot(path=str(evidence / f'trash-{width}.png'))
                    page.locator('#empty-trash').click()
                    page.locator('#chat-confirm-submit').click()
                    page.wait_for_function("()=>!$('chat-confirm').open&&state.history.trash===null")
                    page.locator('#message-input').fill('retain next test')
                    page.locator('#send').click()
                    page.wait_for_function('()=>!state.pending.size&&!state.execution&&state.history.messages.length>=2')
                # Portfolio responses change output/status on every poll while retaining the output DOM.
                updates = [0]
                hide_reading = [False]
                def portfolio(route):
                    response = route.fetch()
                    data = response.json()
                    updates[0] += 1
                    data['executions'] = [{'id':'reading-fixture', 'project_id':alpha, 'agent_id':'bot',
                                           'status':'running' if updates[0]<4 else 'completed', 'finished_at':None if updates[0]<4 else 3,
                                           'task_id':None, 'started_at':1, 'work_started_at':2,
                                           'output':'\n'.join(f'Line {i}: stable reading content' for i in range(250))+f'\nUpdate {updates[0]}',
                                           'error':'', 'thread_id':'fixture-thread', 'turn_id':'fixture-turn'}]
                    if updates[0]>2:
                        data['executions'].insert(0, {**data['executions'][0], 'id':'new-fixture', 'output':'New result above reading', 'finished_at':3, 'status':'completed'})
                    if updates[0]%2==0:
                        data['executions'].reverse()
                    if hide_reading[0]:
                        data['executions']=[e for e in data['executions'] if e['id']!='reading-fixture']
                    route.fulfill(response=response, json=data)
                page.route('**/api/portfolio*', portfolio)
                for width,height in [(1440,1000), (393,851), (320,420)]:
                    page.set_viewport_size({'width':width, 'height':height})
                    page.evaluate("fallback=>{$('agent-list').moveBefore=fallback?undefined:Element.prototype.moveBefore;}", width==393)
                    page.locator('[data-view=agents]').click()
                    page.evaluate('()=>refresh(false)')
                    panel = page.locator('[data-execution-id=reading-fixture]')
                    panel.locator('summary').click()
                    page.evaluate("()=>{window.savedDetails=$('agent-list').querySelector('[data-execution-id=reading-fixture] details');window.savedOutput=savedDetails.querySelector('pre');savedOutput.scrollTop=180;window.scrollTo(0,70);}")
                    scroll = page.evaluate('[window.scrollY,savedOutput.scrollTop,savedOutput.getBoundingClientRect().top]')
                    page.wait_for_timeout(6600)  # at least two real automatic polling periods
                    assert page.evaluate("()=>savedDetails===document.querySelector('[data-execution-id=reading-fixture] details')&&savedDetails.open")
                    assert page.evaluate('savedOutput.scrollTop') == scroll[1]
                    position=page.evaluate('[window.scrollY,savedOutput.getBoundingClientRect().top,Math.max(0,document.documentElement.scrollHeight-innerHeight)]')
                    desired=min(position[2],max(0,position[0]+position[1]-scroll[2]))
                    assert abs(position[0]-desired)<=1, {'width':width,'before':scroll,'after':position,'desired':desired}
                    # Selection survives changing output; latest text is applied on explicit deselection.
                    page.evaluate("()=>{const range=document.createRange();range.setStart(savedOutput.firstChild,0);range.setEnd(savedOutput.firstChild,15);getSelection().removeAllRanges();getSelection().addRange(range);}")
                    selected = page.evaluate('getSelection().toString()')
                    page.evaluate('()=>refresh(false)')
                    assert page.evaluate('getSelection().toString()') == selected
                    page.evaluate('()=>getSelection().removeAllRanges()')
                    page.wait_for_timeout(100)
                    assert 'Update' in panel.locator('pre').inner_text()
                    page.screenshot(path=str(evidence / f'agents-{width}.png'))
                    hide_reading[0]=True
                    page.evaluate('()=>refresh(false)')
                    assert panel.locator('details').evaluate('n=>n.open')
                    panel.locator('summary').click()
                    page.evaluate('()=>refresh(false)')
                    assert panel.count()==0  # closed retained result leaves on next poll
                    hide_reading[0]=False
                    page.evaluate('()=>refresh(false)')
                    assert not panel.locator('details').evaluate('n=>n.open')
                    page.locator('[data-view=chat]').click()
                # Context navigation ends retained expansion; unrelated chat scope stays intact.
                page.evaluate('(id)=>selectProject(id)', beta)
                assert page.evaluate('state.history.messages.length') == 0
                assert not errors, errors
                browser.close()
                print(json.dumps({'status':'passed','widths':[1440,393,320], 'poll_updates':updates[0],
                                  'todo_task':tasks[0]['id'], 'screenshots':str(evidence), 'page_errors':errors}))
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()

"""Real Chromium regressions for opening scroll and explicit To-Do dispatch."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import json
import time
import uuid

import pytest
from test_model_picker_browser import browser_context, page, server  # noqa: F401

from ai_manager.db import Database
from ai_manager.registry import Registry


def seed_history(browser_context):
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    conversation = db.one("SELECT id FROM conversations WHERE context='global'")['id']
    with db.transaction() as conn:
        conn.execute('DELETE FROM messages WHERE conversation_id=?', (conversation,))
        for i in range(155):
            conn.execute("INSERT INTO messages VALUES(?,?,'assistant',?,'completed',NULL,?)",
                         (uuid.uuid4().hex, conversation, f'Message {i}\n\n' + ('Long history paragraph.\n\n' * 18), time.time()))


def bottom(page):
    return page.locator('#messages').evaluate('n=>Math.abs(n.scrollHeight-n.clientHeight-n.scrollTop)<=1')


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640)])
def test_long_history_reload_reopen_and_recovery_settle_at_bottom(page, browser_context, width, height):
    seed_history(browser_context)
    page.set_viewport_size({'width': width, 'height': height})
    page.add_init_script("""
      window.fontGate=new Promise(resolve=>window.releaseFonts=resolve);
      Object.defineProperty(document.fonts,'ready',{value:fontGate});
    """)
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===30')
    # Exercise a layout change after content arrives and before fonts settle.
    page.locator('#message-input').fill('Retained draft changing composer height')
    page.evaluate('releaseFonts()')
    page.wait_for_function('()=>state.ready&&!initialChatPosition')
    assert page.locator('#messages').evaluate('n=>n.scrollHeight>n.clientHeight*10')
    assert bottom(page)
    page.locator('#messages').evaluate('n=>n.scrollTop=400')
    page.locator('[data-view=tasks]').click()
    page.locator('[data-view=chat]').click()
    page.wait_for_function('()=>!initialChatPosition')
    assert bottom(page)
    # Authentication recovery runs enter() again without a document reload.
    page.evaluate('()=>{showAuth();return enter();}')
    page.wait_for_function('()=>state.ready&&!initialChatPosition')
    assert bottom(page)


def test_manual_gesture_during_open_and_load_older_preserve_reading(page, browser_context):
    seed_history(browser_context)
    page.add_init_script("""
      window.fontGate=new Promise(resolve=>window.releaseFonts=resolve);
      Object.defineProperty(document.fonts,'ready',{value:fontGate});
    """)
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===30')
    page.locator('#messages').hover()
    page.mouse.wheel(0, -500)
    page.wait_for_timeout(100)
    page.locator('#messages').evaluate('n=>n.scrollTop=600')
    page.evaluate('releaseFonts()')
    page.wait_for_function('()=>state.ready')
    assert page.locator('#messages').evaluate('n=>n.scrollTop') == 600
    page.evaluate('()=>refresh()')
    assert page.locator('#messages').evaluate('n=>n.scrollTop') == 600
    anchor = page.locator('#messages [data-message]').first
    y = anchor.bounding_box()['y']
    page.locator('#chat-older').click()
    page.wait_for_function("()=>document.querySelectorAll('#messages [data-message]').length===60")
    # The same message remains at the same viewport coordinate after prepend.
    assert abs(page.locator('#messages [data-message]').nth(30).bounding_box()['y'] - y) <= 1
    before = page.locator('#messages').evaluate('n=>n.scrollTop')
    page.evaluate('()=>refresh()')
    assert page.locator('#messages').evaluate('n=>n.scrollTop') == before
    # A normal send and streamed reply also retain a deliberate reading position.
    page.locator('#message-input').fill('Stay at this reading position')
    page.locator('#messages').evaluate('n=>n.scrollTop=800')
    page.locator('#send').click()
    page.wait_for_function("()=>!state.pending.size&&!state.execution")
    assert page.locator('#messages').evaluate('n=>n.scrollTop') == 800


def test_start_failure_duplicate_clicks_and_lost_response_keep_one_task(page, browser_context):
    _, _, root = browser_context
    project_path = root / 'projects' / uuid.uuid4().hex
    project_path.mkdir()
    db = Database(root / 'data/manager.sqlite3')
    project = Registry(db, (root / 'projects',)).add(project_path)['id']
    # Pause this disposable project's runner so assertions observe the queued task.
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (project,))
    todo = page.evaluate("project=>api('/api/todos',{method:'POST',body:JSON.stringify({project_id:project,title:'Explicit start',details:'Keep all original source'})})", project)
    page.evaluate('()=>refresh()')
    page.locator('[data-view=todo]').click()
    button = page.locator('[data-todo-start="' + todo['id'] + '"]')
    button.wait_for()
    path = '**/api/todos/' + todo['id'] + '/start'
    page.route(path, lambda route: route.fulfill(status=503, content_type='application/json', body='{"detail":"Dispatch unavailable"}'))
    button.click()
    page.wait_for_function("()=>$('notice-text').textContent.includes('Dispatch unavailable')")
    assert button.is_enabled()
    assert db.one('SELECT archived_at FROM todos WHERE id=?', (todo['id'],))['archived_at'] is None
    assert not db.all('SELECT * FROM tasks WHERE project_id=?', (project,))
    page.unroute(path)
    held = []
    page.route(path, lambda route: held.append(route))
    button.click()
    page.wait_for_function('()=>todoStarts.size===1')
    page.wait_for_timeout(100)
    assert len(held) == 1 and button.is_disabled()
    page.evaluate("()=>{const entry=state.todos.find(t=>todoStarts.has(t.id));void startTodo(entry).catch(()=>{});$('todo-list').dataset.signature='';renderTodos();}")
    assert button.is_disabled() and len(held) == 1
    # Accept on the real backend but drop its response, simulating connection loss.
    response = held[0].fetch()
    assert response.status == 200
    task_id = response.json()['task_id']
    held[0].abort('connectionfailed')
    page.wait_for_function("()=>$('notice-text').textContent.includes('Connection interrupted')")
    page.unroute(path)
    assert button.is_enabled()
    button.click()
    page.wait_for_function("()=>!todoStarts.size&&$('notice-text').textContent.includes('To-Do started')")
    assert task_id in page.locator('#notice-text').inner_text()
    assert button.count() == 0
    tasks = db.all('SELECT * FROM tasks WHERE project_id=?', (project,))
    assert len(tasks) == 1 and tasks[0]['id'] == task_id and tasks[0]['state'] == 'queued'
    assert tasks[0]['description'] == 'Explicit start\n\nKeep all original source'
    assert db.setting('todo_handoff:' + todo['id'])['source'] == todo
    assert len(db.all("SELECT * FROM events WHERE kind='todo.consumed' AND task_id=?", (task_id,))) == 1
    assert json.loads(db.one("SELECT payload FROM events WHERE kind='task.model_selected' AND task_id=?", (task_id,))['payload'])['model']

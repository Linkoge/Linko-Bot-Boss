"""Real-browser retrieval from the compressed archive, using a disposable server."""
import json
import time

from test_model_picker_browser import browser_context, page, server  # noqa: F401
from test_retention import worker

from ai_manager.db import Database
from ai_manager.history import maintain
from ai_manager.registry import Registry


def test_load_older_chat_worker_events_and_complete_output(page, browser_context):  # noqa: F811
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    conversation = db.one("SELECT id FROM conversations WHERE context='global'")['id']
    with db.transaction() as conn:
        for i in range(113):
            conn.execute("INSERT INTO messages VALUES(?,?,'user',?,'completed',NULL,?)",
                         (f'retention-{i}', conversation, f'Historical message {i}', time.time()))
    project_path = root / 'projects/history'
    project_path.mkdir()
    project = Registry(db, (root / 'projects',)).add(project_path)['id']
    with db.transaction() as conn:
        conn.execute("INSERT INTO tasks(id,project_id,description,state,created_at,updated_at) VALUES('browser-task',?,'Read old worker reports','done',0,0)", (project,))
    worker(db, project, 'browser-task', eid='archived-worker')
    for i in range(22):
        worker(db, project, 'browser-task', eid=f'fixture-worker-{i}')
    maintain(db)
    page.reload()
    page.wait_for_function('()=>state.ready&&state.history.messages.length===30')
    assert page.locator('#messages [data-message]').count() == 30
    assert page.locator('#chat-older').count() == 0 and page.get_by_text('Load older messages').count() == 0
    page.wait_for_function('()=>!initialChatPosition')
    scroll_to_top = "()=>{$('messages').scrollTop=0}"
    page.evaluate(scroll_to_top)
    page.wait_for_function("()=>document.querySelectorAll('#messages [data-message]').length===60")
    page.evaluate(scroll_to_top)
    page.wait_for_function("()=>document.querySelectorAll('#messages [data-message]').length===90")
    page.evaluate(scroll_to_top)
    page.wait_for_function("()=>document.querySelectorAll('#messages [data-message]').length===113")
    assert page.locator('#messages [data-message]').first.get_attribute('data-message') == 'retention-0'
    assert not page.evaluate('()=>state.olderHasMore')
    # A routine refresh retains deliberately loaded older rows without reloading
    # the archive or re-enabling the exhausted cursor.
    page.evaluate('()=>refresh()')
    assert page.locator('#messages [data-message]').count() == 113
    assert not page.evaluate('()=>state.olderHasMore')
    page.locator('[data-view=tasks]').click()
    page.locator('[data-task-id=browser-task]').get_by_role('button', name='History', exact=True).click()
    page.locator('#history-reader').wait_for(state='visible')
    task = json.loads(page.locator('#reader-content pre').first.text_content())
    assert len(task['executions']) == 20 and len(task['events']) == 20
    page.get_by_role('button', name='Load older executions', exact=True).click()
    page.wait_for_function("()=>document.querySelectorAll('#reader-content pre').length===2")
    assert len(json.loads(page.locator('#reader-content pre').nth(1).text_content())['executions']) == 3
    page.get_by_role('button', name='Read execution archived-worker', exact=True).click()
    page.wait_for_function("()=>document.getElementById('reader-title').textContent==='Worker history'")
    first = json.loads(page.locator('#reader-content pre').first.text_content())
    assert len(first['events']) == 20 and 'output' not in first
    page.get_by_role('button', name='Load older events', exact=True).click()
    page.wait_for_function("()=>document.querySelectorAll('#reader-content pre').length===2")
    second = json.loads(page.locator('#reader-content pre').nth(1).text_content())
    assert second['events'][-1]['seq'] < first['events'][0]['seq']
    page.get_by_role('button', name='Read full output', exact=True).click()
    page.wait_for_function("()=>document.querySelectorAll('#reader-content pre').length===3")
    part = json.loads(page.locator('#reader-content pre').nth(2).text_content())
    assert part['output'].startswith('final report /reports/result.md')
    assert part['next_output_offset'] == 12000
    page.locator('#reader-close').click()
    page.locator('[data-view=agents]').click()
    page.locator('#execution-history').click()
    page.locator('#history-reader').wait_for(state='visible')
    assert json.loads(page.locator('#reader-content pre').first.text_content())['executions'][0]['id'] == 'fixture-worker-21'

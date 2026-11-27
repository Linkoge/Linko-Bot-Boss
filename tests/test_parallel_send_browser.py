"""Real Chromium on Main: the parallel send button shows only while a task of the current project is running or queued,
sends the flag, and the message and the task show a "parallel" badge."""
from browser_sync import refreshed
from test_model_picker_browser import browser_context, page, server  # noqa: F401

from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.registry import Registry


def test_parallel_send_button_badges_and_flag(page, browser_context):  # noqa: F811
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    path = root / 'projects' / 'parallel-app'
    path.mkdir()
    project = Registry(db, (root / 'projects',)).add(path)['id']
    # Paused: the live scheduler never dispatches the fixture task, so it stays queued (no fake running rows).
    db.run("UPDATE projects SET status='paused' WHERE id=?", (project,))
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    page.evaluate('id=>selectProject(id)', project)
    page.wait_for_function('id=>state.project===id&&!!state.history', arg=project)
    page.locator('#message-input').fill('Also add the export')
    page.wait_for_function("()=>!document.getElementById('send').hidden")
    assert page.locator('#send-parallel').count() == 1  # rendered on Main
    assert page.locator('#send-parallel').is_hidden()  # but nothing runs in this project yet
    Tasks(db).add(project, 'Already queued work')
    flagged = Tasks(db).add(project, 'Parallel work', parallel=True)
    refreshed(page, 'refresh(false)')
    page.wait_for_function("()=>!document.getElementById('send-parallel').hidden")
    assert page.locator('#send-parallel').inner_text() == '⇈'
    with page.expect_request(lambda request: request.url.endswith('/api/chat') and request.method == 'POST') as sent:
        page.locator('#send-parallel').click()
    assert sent.value.post_data_json['parallel'] is True
    page.wait_for_function("()=>!!document.querySelector('#messages .message.user .parallel-badge')")
    assert db.all("SELECT 1 FROM settings WHERE key LIKE 'execution_parallel:%'")
    page.locator('[data-view=tasks]').click()
    page.wait_for_function("id=>!!document.querySelector(`#task-list [data-task-id='${id}'] .parallel-badge`)",
                           arg=flagged['id'])
    assert page.locator('#task-list .parallel-badge').count() == 1  # only the flagged task
    # Another project with nothing running: the button hides again.
    page.evaluate('()=>selectProject(null)')
    page.wait_for_function('()=>state.project===null&&!!state.history')
    page.locator('[data-view=chat]').click()
    page.locator('#message-input').fill('Global note')
    page.wait_for_function("()=>!document.getElementById('send').hidden")
    assert page.locator('#send-parallel').is_hidden()

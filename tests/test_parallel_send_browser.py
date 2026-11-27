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


def test_parallel_button_shows_with_an_idle_composer_and_follows_the_queue(page, browser_context):  # noqa: F811
    """The owner's report: with a task running or queued, ⇈ stayed hidden until text was typed (it followed the
    composer's active state). It now shows whenever the project has worker work, and the 3 s poll keeps it current."""
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    path = root / 'projects' / 'parallel-idle'
    path.mkdir()
    project = Registry(db, (root / 'projects',)).add(path)['id']
    db.run("UPDATE projects SET status='paused' WHERE id=?", (project,))  # the live scheduler leaves it queued
    page.evaluate('id=>selectProject(id)', project)
    page.wait_for_function('id=>state.project===id&&!!state.history', arg=project)
    page.evaluate('()=>document.activeElement?.blur()')
    assert page.locator('#send-parallel').is_hidden()
    queued = Tasks(db).add(project, 'Running work')
    # No reload, no typing: the regular poll picks the task up.
    page.wait_for_function("()=>!document.getElementById('send-parallel').hidden", timeout=15000)
    assert page.locator('#message-input').input_value() == ''
    assert not page.evaluate("()=>document.getElementById('chat-form').classList.contains('composer-active')")
    button = page.locator('#send-parallel')
    assert 'parallel' in button.get_attribute('title').lower() and 'up to 3' in button.get_attribute('title')
    box = button.bounding_box()
    form = page.locator('#chat-form').bounding_box()
    assert box['width'] >= 40 and box['y'] + box['height'] <= form['y'] + form['height'] + 1  # inside the composer
    # An empty click sends nothing: it asks for the message and focuses the input.
    sent = []
    page.on('request', lambda r: sent.append(r) if r.url.endswith('/api/chat') and r.method == 'POST' else None)
    button.click()
    page.wait_for_function("()=>document.activeElement===document.getElementById('message-input')")
    assert not sent
    page.locator('#message-input').fill('Run this alongside')
    with page.expect_request(lambda r: r.url.endswith('/api/chat') and r.method == 'POST') as request:
        button.click()
    assert request.value.post_data_json['parallel'] is True
    # The task finishes: the button goes away on the next poll, without a reload.
    db.run("UPDATE tasks SET state='done' WHERE id=?", (queued['id'],))
    page.wait_for_function("()=>document.getElementById('send-parallel').hidden", timeout=15000)

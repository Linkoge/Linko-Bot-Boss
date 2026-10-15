"""Lossless hot/cold history, durable references and bounded default retrieval."""
import asyncio
import json
import sqlite3
import time
import zlib
from contextlib import closing

import pytest
from test_chat_history import chat as chat_fixture
from test_chat_history import seed
from test_web import client as client_fixture
from test_web import sign_in

from ai_manager import history
from ai_manager.chat import Chat
from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.execution_ui import event_page, execution_page, execution_summary

chat, client = chat_fixture, client_fixture


def raw(db, query):
    with closing(sqlite3.connect(db.path)) as conn:
        return conn.execute(query).fetchall()


def worker(db, project, task=None, eid='worker', finished=1, delivery='delivered'):
    with db.transaction() as conn:
        conn.execute('''INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,finished_at,
            output,input,launch_input,error,delivery,delivery_execution,commit_ref)
            VALUES(?,?,?,'worker','completed',0,?,?,?,?,?,?,'receipt-execution','commit-kept')''',
                     (eid, task, project, finished, 'final report /reports/result.md\n' + 'details\n' * 3000,
                      'full original prompt', 'native launch prompt', 'diagnostic', delivery))
        for i in range(43):
            db.event(conn, 'task.evidence' if i == 0 else 'worker.progress',
                     {'index': i, 'report': '/reports/result.md', 'delivery': 'receipt-execution'},
                     project, task, eid)


@pytest.mark.smoke
def test_chat_windows_order_search_restart_and_no_full_history_default(chat, monkeypatch):
    conversation, _ = seed(chat, count=137)
    chat.db.run('UPDATE messages SET content=? WHERE id=?', ('Подробный отчёт 前\n\x00linko-history-v1:literal', conversation + '-1'))
    original = chat.db.all('SELECT * FROM messages ORDER BY rowid')
    assert history.maintain(chat.db) == 87
    assert len(raw(chat.db, "SELECT id FROM messages WHERE typeof(content)='blob'")) == 87
    assert chat.db.all('SELECT * FROM messages ORDER BY rowid') == original
    assert history.maintain(Database(chat.db.path)) == 0
    restored = Chat(Database(chat.db.path), chat.registry, chat.server)
    calls = []
    decompress = zlib.decompress
    with monkeypatch.context() as patch:
        patch.setattr(zlib, 'decompress', lambda value: (calls.append(1), decompress(value))[1])
        page = restored.history()
        assert len(page['messages']) == 50 and page['has_more']
        assert len(calls) <= 1  # At most the lookahead row; no scan of cold bodies.
    ids = [m['id'] for m in page['messages']]
    while page['has_more']:
        page = restored.history(before=page['messages'][0]['id'])
        ids = [m['id'] for m in page['messages']] + ids
    assert ids == [m['id'] for m in original]
    assert restored.saved_history(conversation, search='message 0 ')['messages'][0]['id'] == original[0]['id']
    assert restored.search_history(search='message 0 ')['conversations'][0]['match_count'] == 1
    prompt, _ = restored.prompt(restored.conversation(), 'new question', 'new', 'new-thread', None)
    assert len(prompt['conversation_history']) == 50
    assert prompt['conversation_history'][0]['id'] == original[-50]['id']


def test_small_batches_resume_and_age_never_purges(chat):
    seed(chat, count=61)
    before = chat.db.all('SELECT * FROM messages ORDER BY rowid')
    assert history.maintain(chat.db, batch=2) == 2
    db = Database(chat.db.path)
    for _ in range(10):
        if not history.maintain(db, batch=2):
            break
    assert history.maintain(db) == 0
    # Seven days also moves a small, inactive chat into cold storage. Even after
    # ninety days and repeated restarts, the exact bodies and row order survive.
    assert history.maintain(db, now=time.time() + 100 * 86400) == 50
    assert history.maintain(Database(db.path), now=time.time() + 200 * 86400) == 0
    assert db.all('SELECT * FROM messages ORDER BY rowid') == before
    assert len(raw(db, "SELECT id FROM messages WHERE typeof(content)='blob'")) == 61


def test_events_workers_and_references_survive_with_bounded_previews(chat, tmp_path, monkeypatch):
    project_path = tmp_path / 'project'
    project_path.mkdir()
    chat.registry.roots = (tmp_path,)
    project = chat.registry.add(project_path)['id']
    task = Tasks(chat.db).add(project, 'Keep this task forever')
    worker(chat.db, project, task['id'], finished=time.time())
    original_events = chat.db.all('SELECT * FROM events ORDER BY seq')
    original_worker = chat.db.one("SELECT * FROM executions WHERE id='worker'")
    chat.db.set_setting('deployment_receipt', {'task_id': task['id'], 'execution_id': 'worker', 'report': '/reports/result.md'})
    chat.db.run('UPDATE tasks SET "merge"=? WHERE id=?', (json.dumps({'commit': 'permanent-commit'}), task['id']))
    history.maintain(chat.db)
    # Each task and each worker keeps the latest twenty events hot.
    assert len(raw(chat.db, "SELECT seq FROM events WHERE execution_id='worker' AND typeof(payload)='text'")) == 20
    page, all_events = event_page(chat.db, execution_id='worker'), []
    while True:
        all_events = page['events'] + all_events
        if not page['has_more_events']:
            break
        page = event_page(chat.db, execution_id='worker', before=page['before_event'])
    assert [json.loads(e['payload'])['index'] for e in all_events] == list(range(43))
    history.maintain(chat.db, now=time.time() + 100 * 86400)
    reopened = Database(chat.db.path)
    assert reopened.one("SELECT * FROM executions WHERE id='worker'") == original_worker
    assert raw(reopened, 'SELECT length(compressed)<original_bytes FROM history_archive ORDER BY original_bytes DESC LIMIT 1') == [(1,)]
    assert reopened.all('SELECT * FROM events WHERE seq<=? ORDER BY seq', (original_events[-1]['seq'],)) == original_events
    assert Tasks(reopened).get(task['id'])['merge'] == {'commit': 'permanent-commit'}
    assert reopened.setting('deployment_receipt')['report'] == '/reports/result.md'
    with monkeypatch.context() as patch:
        patch.setattr(zlib, 'decompress', lambda _: pytest.fail('Default summaries inflated a cold result'))
        result = execution_summary(reopened, project)['executions'][0]
        assert len(result['output']) == history.SUMMARY_CHARS
        assert 'input' not in result and 'launch_input' not in result
        assert Tasks(reopened).list(project)[0]['result_summary'] == result['output']
    assert reopened.one("SELECT json_extract(history_text(payload),'$.report') AS report FROM events WHERE kind='task.evidence'")['report'] == '/reports/result.md'


def test_atomic_failure_leaves_no_references_to_missing_archive(chat, monkeypatch):
    seed(chat, count=56)
    before = raw(chat.db, 'SELECT * FROM messages ORDER BY rowid')
    pack = history.pack
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('simulated storage failure')
        return pack(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(history, 'pack', fail_second)
        with pytest.raises(OSError, match='storage failure'):
            history.maintain(chat.db)
    assert raw(chat.db, 'SELECT * FROM messages ORDER BY rowid') == before
    assert not raw(chat.db, 'SELECT * FROM history_archive')
    assert history.maintain(Database(chat.db.path)) == 6


def test_active_and_pending_delivery_evidence_stays_hot(chat):
    worker(chat.db, None, eid='active', finished=None)
    worker(chat.db, None, eid='pending', delivery='pending')
    conversation, _ = seed(chat)
    chat.db.run("UPDATE messages SET execution_id='active',created_at=0 WHERE conversation_id=?", (conversation,))
    history.maintain(chat.db)
    assert not raw(chat.db, "SELECT id FROM messages WHERE typeof(content)='blob'")
    assert not raw(chat.db, "SELECT seq FROM events WHERE typeof(payload)='blob'")
    assert not raw(chat.db, "SELECT id FROM executions WHERE typeof(output)='blob'")
    chat.db.run("UPDATE executions SET finished_at=1,delivery='delivered'")
    assert history.maintain(chat.db) > 0
    assert history.maintain(Database(chat.db.path)) == 0


def test_http_older_chat_task_worker_and_full_result_pages(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    chat, db = app.state.chat, app.state.db
    conversation, _ = seed(chat, count=103)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    with db.transaction() as conn:
        conn.execute("INSERT INTO tasks(id,project_id,description,state,created_at,updated_at) VALUES('http-task',?,'Permanent task record','done',0,0)", (project,))
    task = Tasks(db).get('http-task')
    for i in range(23):
        worker(db, project, task['id'], eid=f'worker-{i}')
    history.maintain(db)
    # Drain bounded batches before reading cold data across the public API.
    while history.maintain(db):
        pass
    page = browser.get('/api/history').json()
    ids = [m['id'] for m in page['messages']]
    while page['has_more']:
        page = browser.get('/api/history', params={'before': page['messages'][0]['id']}).json()
        ids = [m['id'] for m in page['messages']] + ids
    assert ids == [f'{conversation}-{i}' for i in range(103)]
    result = browser.get(f"/api/tasks/{task['id']}/execution-records").json()
    assert len(result['executions']) == 20 and len(result['events']) == 20
    assert result['next_offset'] == 20 and result['has_more_events']
    assert len(browser.get(f"/api/tasks/{task['id']}/execution-records?offset=20").json()['executions']) == 3
    assert len(browser.get('/api/execution-history?offset=20').json()['executions']) == 3
    path = '/api/executions/worker-0/history'
    page = browser.get(path).json()
    assert len(page['events']) == 20 and 'output' not in page
    output, offset = '', 0
    while offset is not None:
        part = browser.get(path, params={'output_offset': offset}).json()
        output += part['output']
        offset = part['next_output_offset']
    assert output == db.one("SELECT output FROM executions WHERE id='worker-0'")['output']
    assert browser.get('/api/history?before=foreign').status_code == 409
    assert browser.get(path + '?output_offset=-1').status_code == 409
    assert browser.get('/api/execution-history?offset=-1').status_code == 409
    assert browser.get('/api/executions/missing/history').status_code == 409
    assert len(execution_page(db, task_id=task['id'])['executions']) == 20


@pytest.mark.asyncio
async def test_maintenance_uses_one_job_and_waits_for_batch_shutdown(chat, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    async def batch(*args):
        started.set()
        await finish.wait()
    monkeypatch.setattr(asyncio, 'to_thread', batch)
    maintenance = history.Maintenance(chat.db)
    maintenance.start()
    job = maintenance.job
    maintenance.start()
    assert maintenance.job is job
    await asyncio.wait_for(started.wait(), 2)
    close = asyncio.create_task(maintenance.close())
    await asyncio.sleep(0)
    assert not close.done()
    finish.set()
    await asyncio.wait_for(close, 2)
    assert job.done()


def test_archive_trash_tombstones_and_backup_keep_cold_references(chat, tmp_path):
    conversation, last = seed(chat, count=57)
    before = chat.db.all('SELECT * FROM messages ORDER BY rowid')
    chat.db.set_setting('execution_attachments:None', [{'id': 'attachment-kept', 'path': '/files/report.pdf'}])
    history.maintain(chat.db, now=time.time() + 100 * 86400)
    chat.reset(None, conversation, last, True, archive=True, title='Permanent decisions')
    chat.delete_message(None, conversation, before[0]['id'], True)
    backup, restored = tmp_path / 'backup.db', tmp_path / 'restored.db'
    chat.db.backup(backup)
    Database.restore(backup, restored)
    reopened = Chat(Database(restored), chat.registry, chat.server)
    assert reopened.history()['messages'] == []
    assert reopened.search_history(search='decisions')['conversations'][0]['id'] == conversation
    page, ids = reopened.saved_history(conversation), []
    while True:
        ids = [m['id'] for m in page['messages']] + ids
        if not page['has_more']:
            break
        page = reopened.saved_history(conversation, before=page['messages'][0]['id'])
    assert ids == [m['id'] for m in before[1:]]
    assert reopened.db.all('SELECT * FROM messages ORDER BY rowid') == before
    assert next(m for m in reopened.saved_history(conversation)['messages'] if m['role'] == 'user')['attachments'][0]['id'] == 'attachment-kept'
    assert reopened.db.setting('chat_deleted_message:' + before[0]['id'])
    fresh, last = seed(reopened, count=2)
    history.maintain(reopened.db, now=time.time() + 100 * 86400)
    current = reopened.reset(None, fresh, last, True)['conversation_id']
    reopened.change_trash(None, current, None, fresh, True, restore=True)
    assert reopened.history()['messages'][-1]['id'] == last


@pytest.mark.asyncio
async def test_manager_reads_cold_history_results_and_task_reuse(client):
    browser, app, settings = client
    # Do not start disposable queue work: insert a completed task fixture atomically.
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    db, chat = app.state.db, app.state.chat
    conversation, _ = seed(chat, project, count=65)
    with db.transaction() as conn:
        conn.execute("INSERT INTO tasks(id,project_id,description,state,created_at,updated_at) VALUES('old-task',?,'retained objective','done',0,0)", (project,))
        db.event(conn, 'task.reused', {'request_key': 'old-request'}, project, 'old-task')
    worker(db, project, 'old-task')
    while history.maintain(db, now=time.time() + 100 * 86400):
        pass
    async def read(action, **args):
        return await app.state.scheduler.manager_action(project, {'tool': 'manager_action',
            'arguments': {'action': action, **args}, 'conversation_id': conversation})
    message = (await read('read_history', query='message 0 '))['messages'][0]
    assert message['id'] == conversation + '-0'
    assert (await read('read_history', message_id=message['id']))['content'] == 'message 0 searchable needle'
    result = await read('read_result', execution_id='worker')
    assert result['next_offset'] == 12000 and result['output'].startswith('final report')
    assert (await read('read_task', task_id='old-task'))['evidence'][0]['report'] == '/reports/result.md'
    assert Tasks(db).add(project, 'same request', request_key='old-request')['id'] == 'old-task'


@pytest.mark.asyncio
async def test_maintenance_failure_retries_without_killing_background_job(chat, monkeypatch):
    calls, retried = [], asyncio.Event()
    original_sleep = asyncio.sleep
    def batch(db):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('temporary disk failure')
        return 0
    async def quick_sleep(seconds):
        if len(calls) > 1:
            retried.set()
        await original_sleep(0.001)
    monkeypatch.setattr(history, 'maintain', batch)
    monkeypatch.setattr(asyncio, 'sleep', quick_sleep)
    maintenance = history.Maintenance(chat.db)
    maintenance.start()
    try:
        await asyncio.wait_for(retried.wait(), 2)
        assert len(calls) >= 2 and not maintenance.job.done()
    finally:
        await maintenance.close()


def test_execution_cursor_does_not_shift_when_new_runs_arrive(chat):
    with chat.db.transaction() as conn:
        conn.executemany("INSERT INTO executions(id,agent_id,status,started_at,finished_at) VALUES(?,'worker','completed',0,1)",
                         [(f'execution-{i}',) for i in range(23)])
    first = execution_page(chat.db)
    assert len(first['executions']) == 20
    chat.db.run("INSERT INTO executions(id,agent_id,status,started_at,finished_at) VALUES('new-run','worker','completed',0,1)")
    second = execution_page(chat.db, before=first['before_execution'])
    assert [r['id'] for r in second['executions']] == ['execution-2', 'execution-1', 'execution-0']
    assert second['before_execution'] is None
    with pytest.raises(ValueError, match='not found'):
        execution_page(chat.db, before='missing')


def test_corrupt_archive_is_reported_instead_of_returning_incomplete_history(chat):
    conversation, _ = seed(chat, count=51)
    history.maintain(chat.db)
    chat.db.run('UPDATE history_archive SET compressed=?', (zlib.compress(b'wrong content'),))
    with pytest.raises(sqlite3.DatabaseError, match='Corrupt history archive'):
        chat.db.one('SELECT content FROM messages WHERE id=?', (conversation + '-0',))

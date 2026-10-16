
import pytest

from ai_manager.config import Settings
from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.execution_ui import execution_summary
from ai_manager.registry import Registry


@pytest.fixture
def records(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,))
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    projects = []
    for name in ('one', 'two'):
        (root / name).mkdir()
        projects.append(registry.add(root / name)['id'])
    return db, Tasks(db), projects


def execution(db, project, id, status='running', task=None, queued=100):
    with db.transaction() as conn:
        conn.execute('''INSERT INTO executions(id,project_id,agent_id,task_id,status,started_at,turn_id)
            VALUES(?,?,'execution',?,?,?,?)''', (id, project, task, status, queued, 'turn' if status == 'running' else None))
        if task:
            conn.execute("UPDATE tasks SET state='running',lease_owner=? WHERE id=?", (id, task))


def test_count_scope_question_pause_finish_and_unlimited_count(records):
    db, tasks, (one, two) = records
    task = tasks.add(one, 'Working')
    execution(db, one, 'worker', task=task['id'])
    execution(db, None, 'global')
    execution(db, two, 'other')
    for i in range(105):
        execution(db, one, str(i), 'queued', queued=200+i)
    result = execution_summary(db, one)
    assert len(result['executions']) == 106  # Every unfinished execution survives the old cap.
    assert result['active_agents'] == 1  # Active record is older than displayed history.
    assert execution_summary(db)['active_agents'] == 3
    with db.transaction() as conn:
        conn.execute("INSERT INTO questions VALUES('q','worker','{}','input',NULL,'pending',100)")
    assert execution_summary(db, one)['active_agents'] == 0
    with db.transaction() as conn:
        conn.execute("UPDATE questions SET status='answered'")
        conn.execute("UPDATE tasks SET state='queued' WHERE id=?", (task['id'],))
    assert execution_summary(db, one)['active_agents'] == 0
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET state='running' WHERE id=?", (task['id'],))
    assert execution_summary(db, one)['active_agents'] == 1
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET finished_at=400 WHERE id='worker'")
    assert execution_summary(db, one)['active_agents'] == 0


def test_work_start_uses_durable_event_not_queue_time(records):
    db, tasks, (one, two) = records
    execution(db, one, 'started')
    execution(db, one, 'queued', 'queued')
    with db.transaction() as conn:
        db.event(conn, 'worker.started', {}, one, execution_id='started')
        conn.execute("UPDATE events SET created_at=200 WHERE execution_id='started'")
        db.event(conn, 'worker.started', {}, one, execution_id='started')
    rows = {e['id']: e for e in execution_summary(db, one)['executions']}
    assert rows['started']['started_at'] == 100
    assert rows['started']['work_started_at'] == 200
    assert rows['queued']['work_started_at'] is None
    reopened = Database(db.path)
    assert execution_summary(reopened, one) == execution_summary(db, one)


def test_legacy_archive_setting_cannot_hide_unfinished_work(records):
    db, tasks, (one, two) = records
    old, new = tasks.add(one, 'Old'), tasks.add(one, 'New')
    db.set_setting('archived_tasks', {old['id']: {'at': 1}})
    assert {t['id'] for t in tasks.list()} == {old['id'], new['id']}
    assert len(db.all('SELECT * FROM tasks')) == 2
    with pytest.raises(ValueError, match='archived'):
        tasks.control(old['id'], 'hold', tasks.get(old['id'])['revision'])


def test_status_group_order_is_stable_and_history_remains(records):
    db, tasks, (one, _) = records
    ids = {status: tasks.add(one, status)['id'] for status in ('running', 'queued', 'held', 'done', 'failed')}
    with db.transaction() as conn:
        for status, id in ids.items():
            conn.execute('UPDATE tasks SET state=?,held=?,created_at=100 WHERE id=?',
                         ('queued' if status == 'held' else status, int(status == 'held'), id))
    rows = tasks.list(one)
    assert rows[0]['status'] == 'running' and {t['status'] for t in rows[1:3]} == {'queued', 'held'}
    assert {t['status'] for t in rows[3:]} == {'done', 'failed'}
    assert len(rows) == 5 and tasks.list(one) == rows
    execution(db, one, 'old-active', queued=1)
    for i in range(105):
        execution(db, one, f'history-{i}', 'completed', queued=i+10)
        with db.transaction() as conn:
            conn.execute('UPDATE executions SET finished_at=500 WHERE id=?', (f'history-{i}',))
    rows = execution_summary(db, one)['executions']
    assert rows[0]['id'] == 'old-active' and len(rows) == 11
    assert {r['id'] for r in rows[1:]} == {f'history-{i}' for i in range(95,105)}
    assert execution_summary(db, one)['executions'] == rows

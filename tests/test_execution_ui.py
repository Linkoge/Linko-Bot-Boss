import time

import pytest

from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.execution_ui import execution_summary
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks


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
            conn.execute("UPDATE tasks SET lifecycle='running',lease_owner=? WHERE id=?", (id, task))


def test_count_scope_question_pause_finish_and_unlimited_count(records):
    db, tasks, (one, two) = records
    task = tasks.add(one, 'Working')
    execution(db, one, 'worker', task=task['id'])
    execution(db, None, 'global')
    execution(db, two, 'other')
    for i in range(105):
        execution(db, one, str(i), 'queued', queued=200+i)
    result = execution_summary(db, one)
    assert len(result['executions']) == 100
    assert result['active_agents'] == 1  # Active record is older than displayed history.
    assert execution_summary(db)['active_agents'] == 3
    with db.transaction() as conn:
        conn.execute("INSERT INTO questions VALUES('q','worker','{}','input',NULL,'pending',100)")
    assert execution_summary(db, one)['active_agents'] == 0
    with db.transaction() as conn:
        conn.execute("UPDATE questions SET status='answered'")
        conn.execute("UPDATE tasks SET lifecycle='waiting' WHERE id=?", (task['id'],))
    assert execution_summary(db, one)['active_agents'] == 0
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='running' WHERE id=?", (task['id'],))
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


def test_archive_preserves_cutoff_running_artifacts_history_and_dependencies(records):
    db, tasks, (one, two) = records
    old = tasks.add(one, 'Old')
    running = tasks.add(two, 'Running')
    execution(db, two, 'busy', task=running['id'])
    leased = tasks.add(one, 'Waiting leased')
    execution(db, one, 'waiting', 'queued', task=leased['id'])
    patch = tasks.add(one, 'Prepared deployment')
    with db.transaction() as conn:
        conn.execute("CREATE TABLE dependencies(task_id TEXT,depends_on TEXT)")
        conn.execute('INSERT INTO dependencies VALUES(?,?)', (patch['id'], old['id']))
    failed = tasks.add(one, 'Failed old task')
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (failed['id'],))
    completed = tasks.add(one, 'Completed old task')
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (completed['id'],))
    cutoff = time.time()
    new = tasks.add(one, 'New descendant')
    before = db.all('SELECT * FROM dependencies')
    cleared = tasks.archive_before(cutoff, preserve=[patch['id']])
    assert cleared == [old['id'], failed['id'], completed['id']]
    assert tasks.get(completed['id'])['status'] == 'completed'
    assert tasks.get(failed['id'])['status'] == 'cancelled'
    assert tasks.get(old['id'])['status'] == 'cancelled'
    assert old['id'] not in {t['id'] for t in tasks.list()}
    assert {t['id'] for t in tasks.list()} == {running['id'], leased['id'], patch['id'], new['id']}
    assert db.all('SELECT * FROM dependencies') == before
    assert len(db.all('SELECT * FROM tasks')) == 7
    assert tasks.archive_before(cutoff, preserve=[patch['id']]) == []
    with pytest.raises(ValueError, match='archived'):
        tasks.control(old['id'], 'continue', tasks.get(old['id'])['revision'])


def test_archived_records_cannot_be_dispatched_even_if_legacy_state_requeued(records):
    db, tasks, (one, two) = records
    old = tasks.add(one, 'Legacy queued')
    tasks.archive_before(time.time())
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?", (old['id'],))
    assert tasks.claim(2) is None
    assert db.all('SELECT * FROM executions') == []


def test_archive_audit_failure_rolls_back_entire_clear(records, monkeypatch):
    db, tasks, (one, two) = records
    tasks.add(one, 'Old one')
    tasks.add(two, 'Old two')
    before = db.all('SELECT * FROM tasks')

    def fail(*args, **kwargs):
        raise RuntimeError('Audit unavailable')

    monkeypatch.setattr(db, 'event', fail)
    with pytest.raises(RuntimeError, match='Audit unavailable'):
        tasks.archive_before(time.time())
    assert db.all('SELECT * FROM tasks') == before
    assert db.setting('archived_tasks') is None


def test_status_group_order_is_stable_and_history_remains(records):
    db, tasks, (one, _) = records
    ids = {status: tasks.add(one, status)['id'] for status in ('running', 'queued', 'waiting', 'completed', 'failed')}
    with db.transaction() as conn:
        for status, id in ids.items():
            conn.execute('UPDATE tasks SET lifecycle=?,created_at=100 WHERE id=?', (status, id))
    rows = tasks.list(one)
    assert [t['status'] for t in rows[:3]] == ['running', 'queued', 'waiting']
    assert len(rows) == 5 and tasks.list(one) == rows
    execution(db, one, 'old-active', queued=1)
    for i in range(105):
        execution(db, one, f'history-{i}', 'completed', queued=i+10)
        with db.transaction() as conn:
            conn.execute('UPDATE executions SET finished_at=500 WHERE id=?', (f'history-{i}',))
    rows = execution_summary(db, one)['executions']
    assert rows[0]['id'] == 'old-active' and len(rows) == 100
    assert execution_summary(db, one)['executions'] == rows

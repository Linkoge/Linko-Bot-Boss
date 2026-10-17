"""Backend phase derivation: what the chat indicator shows after the agent's turn (checks, repair, merge, deploy)."""
import json
import os
import time
import uuid

import pytest

from ai_manager.config import Settings
from ai_manager.core import REPAIR_ROUNDS, Tasks
from ai_manager.db import Database
from ai_manager.pipeline import DEPLOY_WAIT, Pipeline, atomic
from ai_manager.progress import DEPLOY_MAX_AGE, IDLE, progress
from ai_manager.registry import Registry


@pytest.fixture
def world(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,))
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    ids = []
    for name in ('one', 'two'):
        (root / name).mkdir()
        ids.append(registry.add(root / name)['id'])
    return db, Tasks(db), Pipeline(db, registry), ids


def run(db, tasks, project, **fields):
    """A running task with its live execution; fields set task columns (stage, rounds)."""
    task = tasks.add(project, 'Work')
    eid = 'exec-' + task['id'][:8]
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,turn_id) "
                     "VALUES(?,?,?,'execution','running',?,'turn')", (eid, task['id'], project, time.time()))
        conn.execute("UPDATE tasks SET state='running',lease_owner=? WHERE id=?", (eid, task['id']))
        for key, value in fields.items():
            conn.execute(f'UPDATE tasks SET {key}=? WHERE id=?', (value, task['id']))
    return task['id'], eid


def event(db, eid, kind):
    with db.transaction() as conn:
        conn.execute("INSERT INTO events(kind,execution_id,payload,created_at) VALUES(?,?,'{}',?)", (kind, eid, time.time()))


def label(db, pipeline, project=None, **kw):
    value = progress(db, pipeline, project, **kw)
    return (value['phase'], value['label'])


def test_idle_when_nothing_is_running(world):
    db, tasks, pipeline, (one, _) = world
    assert progress(db, pipeline) == IDLE and progress(db, pipeline, one) == IDLE


def test_agent_then_checks_then_repair_then_checks_then_merge(world):
    db, tasks, pipeline, (one, _) = world
    task, eid = run(db, tasks, one)
    assert label(db, pipeline, one) == ('agent', 'Working')
    event(db, eid, 'task.check_started')
    assert label(db, pipeline, one) == ('checks', 'Running checks')
    event(db, eid, 'task.check')  # failed: the repair round starts
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET rounds=2 WHERE id=?', (task,))
    assert label(db, pipeline, one) == ('repair', f'Repair round 2 of {REPAIR_ROUNDS}')
    event(db, eid, 'task.check_started')
    assert label(db, pipeline, one) == ('checks', f'Running checks · repair round 2 of {REPAIR_ROUNDS}')
    event(db, eid, 'task.check')
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET stage='merge' WHERE id=?", (task,))
    assert label(db, pipeline, one) == ('merge', 'Merging')
    value = progress(db, pipeline, one)
    assert value['task_id'] == task and value['project_id'] == one and value['active']


def test_starting_before_the_first_turn_and_waiting_for_an_answer(world):
    db, tasks, pipeline, (one, _) = world
    _, eid = run(db, tasks, one)
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET turn_id=NULL WHERE id=?', (eid,))
    assert label(db, pipeline, one) == ('agent', 'Starting')
    with db.transaction() as conn:
        conn.execute("INSERT INTO questions VALUES('q',?,'{}','input',NULL,'pending',100)", (eid,))
    assert label(db, pipeline, one) == ('question', 'Waiting for your answer')


def test_scope_by_project(world):
    db, tasks, pipeline, (one, two) = world
    run(db, tasks, one)
    assert progress(db, pipeline, two) == IDLE
    assert progress(db, pipeline)['project_id'] == one


def test_queued_held_retry_and_paused(world):
    db, tasks, pipeline, (one, two) = world
    first = tasks.add(one, 'Queued')
    assert label(db, pipeline, one) == ('queued', 'Queued')
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET stage='merge' WHERE id=?", (first['id'],))
    assert label(db, pipeline, one) == ('queued', 'Queued to merge')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET retry_at=? WHERE id=?', (time.time() + 600, first['id']))
    assert label(db, pipeline, one) == ('queued', 'Waiting to retry')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET held=1 WHERE id=?', (first['id'],))
    assert progress(db, pipeline, one) == IDLE  # a held task is not progressing
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET held=0 WHERE id=?', (first['id'],))
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (one,))
    assert progress(db, pipeline, one) == IDLE


def request(pipeline, project, **extra):
    atomic(pipeline.root / 'request.json', {'id': 'r1', 'project_id': project, 'task_id': 't', 'requested_at': time.time(), **extra})


@pytest.mark.parametrize('phase,text', [
    (None, 'Deploy requested'), ('testing', 'Deploying: running tests'), ('waiting_idle', 'Deploying: waiting for idle'),
    ('backing_up', 'Deploying: backing up'), ('restarting', 'Deploying: restarting'), ('updating', 'Deploying: updating'),
    ('verifying', 'Health check'), ('rolling_back', 'Rolling back')])
def test_deploy_phases_after_the_task_is_done(world, phase, text):
    db, tasks, pipeline, (one, two) = world
    request(pipeline, one)
    if phase:
        atomic(pipeline.root / 'state.json', {'id': 'r1', 'phase': phase, 'at': time.time(), 'pid': os.getpid()})
    assert label(db, pipeline, one) == ('deploy', text)
    assert label(db, pipeline) == ('deploy', text)
    assert progress(db, pipeline, two) == IDLE  # another project's chat is not told about this deploy


def test_a_running_task_outranks_a_deploy_and_both_clear_at_the_end(world):
    db, tasks, pipeline, (one, _) = world
    request(pipeline, one)
    run(db, tasks, one)
    assert progress(db, pipeline, one)['phase'] == 'agent'
    (pipeline.root / 'request.json').unlink()
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET state='done',lease_owner=NULL")
        conn.execute('UPDATE executions SET finished_at=?,status=?', (time.time(), 'completed'))
    assert progress(db, pipeline, one) == IDLE


def test_stale_deploy_never_spins_forever(world):
    db, tasks, pipeline, (one, _) = world
    request(pipeline, one, requested_at=time.time() - DEPLOY_MAX_AGE - 1)
    assert progress(db, pipeline, one) == IDLE
    request(pipeline, one)
    gone = os.fork() if hasattr(os, 'fork') else None
    if gone == 0:
        os._exit(0)
    os.waitpid(gone, 0)
    atomic(pipeline.root / 'state.json', {'id': 'r1', 'phase': 'verifying', 'at': time.time() - 600, 'pid': gone})
    assert progress(db, pipeline, one) == IDLE  # the script died and went silent
    atomic(pipeline.root / 'state.json', {'id': 'r1', 'phase': 'verifying', 'at': time.time(), 'pid': gone})
    assert progress(db, pipeline, one)['phase'] == 'deploy'  # just written: still within the grace period
    assert DEPLOY_MAX_AGE > DEPLOY_WAIT


def test_deploy_withdrawal_by_the_existing_expiry_clears_it(world):
    db, tasks, pipeline, (one, _) = world
    request(pipeline, one, requested_at=time.time() - DEPLOY_WAIT - 5)
    assert progress(db, pipeline, one)['phase'] == 'deploy'
    assert pipeline.expire()
    assert progress(db, pipeline, one) == IDLE


def test_final_result_hand_off_is_reported_then_clears(world):
    db, tasks, pipeline, (one, _) = world
    task, eid = run(db, tasks, one)
    now = time.time()
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET state='done',lease_owner=NULL WHERE id=?", (task,))
        conn.execute("UPDATE executions SET status='completed',finished_at=?,delivery='pending' WHERE id=?", (now, eid))
    assert label(db, pipeline, one) == ('report', 'Reporting result')
    assert progress(db, pipeline, one, now=now + 400) == IDLE  # bounded: a result that never delivers is not progress
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET delivery='running' WHERE id=?", (eid,))
    assert progress(db, pipeline, one) == IDLE


@pytest.mark.parametrize('state', ['done', 'failed'])
def test_failed_and_done_tasks_show_nothing(world, state):
    db, tasks, pipeline, (one, _) = world
    task, eid = run(db, tasks, one)
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET state=?,lease_owner=NULL WHERE id=?', (state, task))
        conn.execute("UPDATE executions SET status='failed',finished_at=?,delivery='discarded' WHERE id=?", (time.time(), eid))
    assert progress(db, pipeline, one) == IDLE


def test_interactive_manager_turn_is_responding(world):
    db, tasks, pipeline, (one, _) = world
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,project_id,agent_id,status,started_at,turn_id) "
                     "VALUES('m',?,'manager','running',?,'turn')", (one, time.time()))
    assert label(db, pipeline, one) == ('agent', 'Responding')
    json.dumps(progress(db, pipeline, one))  # plain JSON for the portfolio response


def test_portfolio_exposes_progress_per_scope_and_clears(tmp_path):
    from fake_codex import FakeServer
    from fastapi.testclient import TestClient

    from ai_manager.web import create_app
    root = tmp_path / 'projects'
    root.mkdir()
    for name in ('one', 'two'):
        (root / name).mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as browser:
        token = (settings.data_dir / 'setup-token').read_text()
        owner = {'username': 'owner', 'password': uuid.uuid4().hex}
        assert browser.post('/api/setup', json={**owner, 'token': token}).status_code == 200
        browser.headers['X-CSRF-Token'] = browser.post('/api/login', json=owner).json()['csrf']
        one = app.state.registry.add(root / 'one')['id']
        two = app.state.registry.add(root / 'two')['id']
        assert browser.get('/api/portfolio').json()['progress'] == IDLE
        request(app.state.scheduler.pipeline, one)
        atomic(app.state.scheduler.pipeline.root / 'state.json', {'id': 'r1', 'phase': 'verifying', 'at': time.time()})
        assert browser.get('/api/portfolio', params={'project_id': one}).json()['progress']['label'] == 'Health check'
        assert browser.get('/api/portfolio', params={'project_id': two}).json()['progress'] == IDLE
        (app.state.scheduler.pipeline.root / 'request.json').unlink()
        assert browser.get('/api/portfolio', params={'project_id': one}).json()['progress'] == IDLE

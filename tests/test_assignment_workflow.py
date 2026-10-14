"""Durable idle-job checks with no live inference or production state changes."""
import asyncio
import json
import time

import pytest
from fake_codex import FakeServer
from test_minimal_flow import system as system_fixture
from test_recovery import Faults, settle

from ai_manager.assignment_workflow import GRACE, due, get, put, update
from ai_manager.chat import Chat
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

system = system_fixture


def finished(system, delivery='delivered'):
    db, registry, chat, settings = system
    project = registry.list()[0]['id']
    conversation = chat.conversation(project)
    tasks = Tasks(db)
    task = tasks.add(project, 'Authorized objective including full result review', conversation['id'])
    claim = tasks.claim(2)
    workers = Workers(db, registry, settings, lambda s, native=False: FakeServer(s))
    workers.finish(claim['lease_owner'], 'failed', 'Worker done; review remains', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
        conn.execute('UPDATE executions SET delivery=? WHERE id=?', (delivery, claim['lease_owner']))
    return task, claim['lease_owner'], conversation


def make_due(db, task):
    now = time.time()
    assert due(db, now) == []
    with db.transaction() as conn:
        workflow = get(conn, task['id'])
        workflow['idle_since'] = now - GRACE - 1
        workflow['next_at'] = now - 1
        put(conn, task['id'], workflow)
    return due(db)


@pytest.mark.asyncio
async def test_grace_dedup_competing_checks_restart_and_backoff(system):
    db, registry, chat, settings = system
    task, _, conversation = finished(system)
    assert db.setting('assignment_workflow:' + task['id'])['state'] == 'actionable'
    assert not due(db)
    assert not chat.server.prompts  # Even repeated/competing checks are SQLite only.
    assert make_due(db, task)[0]['id'] == task['id']
    other = Chat(Database(db.path), registry, FakeServer(settings))
    # Simulate crash after durable queue commit but before native launch.
    chat.launch = lambda *args: None
    other.launch = lambda *args: None
    async def queue(component):
        return await component.submit(task['project_id'], 'Internal review', conversation['id'], recovery_task=task['id'])
    results = await asyncio.gather(queue(chat), queue(other), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert not due(db)
    assert not chat.server.prompts and not other.server.prompts
    assert not db.all("SELECT * FROM messages WHERE role IN ('worker','user')")
    assert not db.all("SELECT * FROM events WHERE kind='chat.queued'")
    restored = Chat(Database(db.path), registry, FakeServer(settings))
    restored.action_handler = lambda *args: None
    await restored.recover()
    await settle(restored)
    assert len(restored.server.prompts) == 1
    prompt = json.loads(restored.server.prompts[0])
    assert prompt['assignment_followup']['task_id'] == task['id']
    assert 'Worker done' not in json.dumps(prompt)
    assert db.setting('assignment_workflow:' + task['id'])['state'] == 'actionable'
    assert not due(db)  # A finished manager response is not full job completion.
    wake = db.setting('assignment_workflow:' + task['id'])
    assert wake['attempts'] == 1 and wake['next_at'] > time.time()
    await restored.recover()
    assert len(restored.server.prompts) == 1


@pytest.mark.parametrize('held', ['paused','waiting','cancelled','pause_requested','cancel_requested'])
def test_owner_lifecycle_never_wakes(system, held):
    db, *_ = system
    task, _, _ = finished(system)
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lifecycle=? WHERE id=?', (held, task['id']))
    assert not make_due(db, task)


@pytest.mark.parametrize('state', ['owner_wait','deferred','complete'])
def test_wait_deferred_and_completed_never_wake(system, state):
    db, *_ = system
    task, _, _ = finished(system)
    with db.transaction() as conn:
        update(db, conn, task['id'], state, 'Explicit owner disposition')
    assert not make_due(db, task)


def test_project_pause_leases_and_owner_questions_block(system):
    db, *_ = system
    task, execution, _ = finished(system)
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (task['project_id'],))
    assert not make_due(db, task)
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='active' WHERE id=?", (task['project_id'],))
        conn.execute('UPDATE tasks SET lease_owner=? WHERE id=?', (execution, task['id']))
    assert not make_due(db, task)
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
        conn.execute("INSERT INTO questions(id,execution_id,request,kind,created_at) VALUES('owner',?,'{}','input',?)", (execution,time.time()))
    assert not make_due(db, task)


@pytest.mark.asyncio
async def test_pending_worker_delivery_and_interrupted_review_remain_single(system):
    db, registry, chat, settings = system
    task, execution, conversation = finished(system, 'pending')
    assert not make_due(db, task)  # Existing completion delivery already represents the wake.
    faults = Faults(['transport', 'success'])
    chat.server = faults.factory(settings)
    await chat.deliver_results()
    await settle(chat)
    assert db.one('SELECT delivery FROM executions WHERE id=?', (execution,))['delivery'] == 'running'
    assert not due(db)
    manager = db.one("SELECT * FROM executions WHERE agent_id='manager'")
    assert manager['status'] == 'recovering'
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET retry_at=0 WHERE id=?', (manager['id'],))
    await chat.recover()
    await settle(chat)
    assert db.one('SELECT delivery FROM executions WHERE id=?', (execution,))['delivery'] == 'delivered'
    assert db.setting('assignment_workflow:' + task['id'])['state'] == 'actionable'
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings), chat)
    params = {'tool':'manager_action','manager_execution_id':'review-test','conversation_id':conversation['id'],
              'mode':'auto','internal':True,'arguments':{'action':'job_followup','task_id':task['id'],
              'followup_state':'complete','reason':'Reviewed exact result; authorized work complete; deployment awaits new owner request'}}
    await scheduler.manager_action(task['project_id'], params)
    assert not due(db, time.time()+10000)
    params['mode'] = 'conversation'
    params['arguments']['followup_state'] = 'actionable'
    with pytest.raises(ValueError, match='read-only'):
        await scheduler.manager_action(task['project_id'], params)


@pytest.mark.asyncio
async def test_stop_after_wake_queue_and_pause_before_launch_do_not_infer(system):
    db, registry, chat, settings = system
    task, _, conversation = finished(system)
    make_due(db, task)
    launch = chat.launch
    chat.launch = lambda *args: None
    receipt = await chat.submit(task['project_id'], 'Internal review', conversation['id'], recovery_task=task['id'])
    await chat.interrupt(receipt['execution_id'])
    assert db.setting('assignment_workflow:' + task['id'])['state'] == 'owner_wait'
    assert not due(db, time.time()+10000)
    assert db.one("SELECT kind FROM events WHERE kind='chat.stop_requested'")
    assert not chat.server.prompts
    with db.transaction() as conn:
        update(db, conn, task['id'], 'actionable', 'New explicit owner authorization')
    make_due(db, task)
    receipt = await chat.submit(task['project_id'], 'Internal review', conversation['id'], recovery_task=task['id'])
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (task['project_id'],))
    chat.launch = launch
    await chat.recover()
    await settle(chat)
    assert not chat.server.prompts
    assert db.one('SELECT status FROM executions WHERE id=?', (receipt['execution_id'],))['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_scheduler_tick_queues_only_one_and_busy_manager_resets_grace(system):
    db, registry, chat, settings = system
    task, _, _ = finished(system)
    make_due(db, task)
    workers = Workers(db, registry, settings, lambda s,native=False: FakeServer(s))
    scheduler = Scheduler(db, registry, settings, workers, chat)
    second = Scheduler(Database(db.path), registry, settings, workers, chat)
    chat.launch = lambda *args: None
    await asyncio.gather(scheduler.tick(), second.tick())
    assert len(db.all("SELECT * FROM events WHERE kind='recovery.queued'")) == 1
    assert not chat.server.prompts
    assert not due(db, time.time()+10000)
    workflow = db.setting('assignment_workflow:' + task['id'])
    assert workflow['idle_since'] is None
    execution = db.one("SELECT * FROM executions WHERE agent_id='manager'")
    response = db.one("SELECT * FROM messages WHERE execution_id=? AND role='assistant'", (execution['id'],))
    chat._finish(execution['id'], response['id'], 'failed', 'Review unavailable', '')
    with db.transaction() as conn:
        workflow = get(conn, task['id'])
        workflow.update(idle_since=time.time()-100,next_at=time.time()-1)
        put(conn, task['id'], workflow)
    second.followup_check_at = 0
    await second.tick()
    assert len(db.all("SELECT * FROM events WHERE kind='recovery.queued'")) == 2
    workflow = db.setting('assignment_workflow:' + task['id'])
    assert workflow['attempts'] == 2 and workflow['next_at'] > time.time()+100


@pytest.mark.asyncio
async def test_restart_unanswered_native_question_does_not_resume_worker(system):
    db, registry, chat, settings = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Needs explicit owner answer', chat.conversation(registry.list()[0]['id'])['id'])
    claimed = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='waiting',blocker='Waiting for your answer' WHERE id=?", (task['id'],))
        conn.execute("UPDATE executions SET status='recovering',retry_at=0 WHERE id=?", (claimed['lease_owner'],))
        conn.execute("INSERT INTO questions(id,execution_id,request,kind,created_at) VALUES('native-owner',?,'{}','input',?)", (claimed['lease_owner'],time.time()))
    calls = []
    def factory(*args, **kwargs):
        calls.append(True)
        return FakeServer(settings)
    workers = Workers(Database(db.path), registry, settings, factory)
    await workers.recover()
    assert not calls and not workers.jobs
    assert not due(db,time.time()+10000)
    assert Tasks(db).get(task['id'])['lease_owner'] == claimed['lease_owner']


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_internal_result_cannot_deploy_or_continue_paused_worker(system):
    db, registry, chat, settings = system
    task, _, conversation = finished(system)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='paused' WHERE id=?", (task['id'],))
        update(db, conn, task['id'], 'owner_wait', 'Paused by owner')
    scheduler = Scheduler(db, registry, settings, Workers(db,registry,settings), chat)
    params = {'tool':'manager_action','manager_execution_id':'internal-test','conversation_id':conversation['id'],
              'mode':'auto','internal':True,'arguments':{'action':'continue','task_id':task['id']}}
    with pytest.raises(ValueError,match='Owner pauses'):
        await scheduler.manager_action(task['project_id'],params)
    params['arguments'] = {'action':'deploy'}
    with pytest.raises(ValueError,match='not owner authorization'):
        await scheduler.manager_action(task['project_id'],params)
    assert Tasks(db).get(task['id'])['status'] == 'paused'


@pytest.mark.asyncio
async def test_owner_pause_during_native_preparation_prevents_recovery_inference(system):
    db, _, chat, _ = system
    task, _, conversation = finished(system)
    make_due(db,task)
    original = chat.server.thread
    async def thread(*args, **kwargs):
        result = await original(*args, **kwargs)
        with db.transaction() as conn:
            conn.execute("UPDATE projects SET status='paused' WHERE id=?", (task['project_id'],))
        return result
    chat.server.thread = thread
    receipt = await chat.submit(task['project_id'],'Internal review',conversation['id'],recovery_task=task['id'])
    await settle(chat)
    assert not chat.server.prompts
    row = db.one('SELECT status,error FROM executions WHERE id=?',(receipt['execution_id'],))
    assert row['status']=='interrupted' and 'preparation' in row['error']


def test_recording_actionable_reason_cannot_erase_existing_retry_backoff(system):
    db, *_ = system
    task, _, _ = finished(system)
    with db.transaction() as conn:
        workflow = get(conn,task['id'])
        workflow.update(attempts=2,next_at=time.time()+480)
        put(conn,task['id'],workflow)
        update(db,conn,task['id'],'actionable','Review still has a concrete unfinished step','internal-manager')
    retained = db.setting('assignment_workflow:' + task['id'])
    assert retained['attempts']==2 and retained['next_at'] > time.time()+470


@pytest.mark.asyncio
async def test_owner_can_pause_full_job_after_worker_completion(system):
    db, registry, _, settings = system
    task, execution, _ = finished(system)
    workers = Workers(db,registry,settings)
    current_task = Tasks(db).get(task['id'])
    paused = await workers.control(task['id'],'pause',current_task['revision'])
    assert paused['status']=='paused' and paused['job_followup']['state']=='owner_wait'
    assert db.one('SELECT status FROM executions WHERE id=?',(execution,))['status']=='failed'
    assert not due(db,time.time()+10000)

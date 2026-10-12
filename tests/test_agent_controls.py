"""Control races use disposable process groups/partial files; no production signals."""
import asyncio
import json
import os
import sys
import time

import pytest
from test_recovery import Faults, FaultServer, settle, task_in
from test_recovery import system as system_fixture

from ai_manager.codex import AppServer, process_start
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

system = system_fixture


class ControlledServer(FaultServer):
    def __init__(self, settings, faults, native=False):
        super().__init__(settings, faults, native)
        self.cooperative = faults.cooperative
        self.close_ready, self.allow_close = asyncio.Event(), faults.allow_close

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', 'import time; time.sleep(60)', start_new_session=True)
        self.process_identity = process_start(self.process.pid)

    async def request(self, method, params, timeout=30):
        if method != 'turn/start':
            return await super().request(method, params, timeout)
        self.cwd.joinpath('partial.txt').write_text('retained original progress')
        result = await super().request(method, params, timeout)
        self.queues[params['threadId']].put_nowait({'method': 'item/agentMessage/delta',
            'params': {'turnId': result['turn']['id'], 'delta': 'Partial progress'}})
        return result

    async def interrupt(self, thread_id, turn_id):
        if self.cooperative:
            self.queues[thread_id].put_nowait({'method': 'turn/completed',
                'params': {'turn': {'id': turn_id, 'status': 'interrupted'}}})
        else:
            await asyncio.sleep(60)  # RPC timeout must not wait for substantial work.

    async def close(self):
        self.close_ready.set()
        await self.allow_close.wait()
        await AppServer.close(self)


class Controls(Faults):
    def __init__(self, cooperative=True, hold_cleanup=False):
        super().__init__(['hold'])
        self.cooperative = cooperative
        self.allow_close = asyncio.Event()
        if not hold_cleanup:
            self.allow_close.set()

    def factory(self, settings, native=False):
        return ControlledServer(settings, self, native)


async def running(system, controls):
    db, registry, _, settings = system
    task, project, origin = task_in(system, 'Original requirement ' + str(time.monotonic()))
    workers = Workers(db, registry, settings, controls.factory)
    claim = Tasks(db).claim(2)
    workers.launch(claim)
    for _ in range(100):
        if db.one('SELECT status FROM executions WHERE id=?', (claim['lease_owner'],))['status'] == 'running':
            await asyncio.sleep(.01)
            return workers, Tasks(db).get(task['id']), project, origin
        await asyncio.sleep(.01)
    raise AssertionError('worker did not start')


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['pause', 'cancel'])
async def test_request_is_not_confirmation_and_blocks_resume_until_cleanup(system, command):
    db, _, _, _ = system
    controls = Controls(hold_cleanup=True)
    workers, task, project, _ = await running(system, controls)
    eid = task['lease_owner']
    server = workers.servers[eid]
    try:
        requested = await workers.control(task['id'], command, task['revision'])
        assert requested['status'] == command + '_requested'
        await asyncio.wait_for(server.close_ready.wait(), 1)
        assert Tasks(db).get(task['id'])['status'] == command + '_requested'
        assert db.one('SELECT finished_at FROM executions WHERE id=?', (eid,))['finished_at'] is None
        with pytest.raises(ValueError, match='cleanup|already requested'):
            await workers.control(task['id'], 'continue', requested['revision'])
        assert Tasks(db).claim(2) is None
        controls.allow_close.set()
        await settle(workers)
        saved = Tasks(db).get(task['id'])
        assert saved['status'] == ('paused' if command == 'pause' else 'cancelled')
        assert saved['lease_owner'] is None
        assert server.process.returncode is not None
        assert project['path'] and server.cwd.joinpath('partial.txt').read_text() == 'retained original progress'
        assert db.one('SELECT output FROM executions WHERE id=?', (eid,))['output'] == 'Partial progress'
        restored = Workers(Database(db.path), workers.registry, workers.settings, controls.factory)
        await restored.recover()
        assert not restored.jobs
    finally:
        controls.allow_close.set()
        await workers.close()


@pytest.mark.asyncio
async def test_unresponsive_pause_has_bounded_checkpoint_wait_and_cancel_kills_immediately(system):
    db, _, _, _ = system
    receipts = []
    for command in ('pause', 'cancel'):
        controls = Controls(cooperative=False)
        workers, task, _, _ = await running(system, controls)
        server = workers.servers[task['lease_owner']]
        started = time.monotonic()
        await workers.control(task['id'], command, task['revision'])
        if command == 'cancel':
            # The click handler signals before yielding to asynchronous cleanup.
            await asyncio.wait_for(server.process.wait(), .5)
            assert time.monotonic() - started < .5
        await settle(workers)
        elapsed = time.monotonic() - started
        assert elapsed < (3 if command == 'pause' else 1)
        assert db.one('SELECT finished_at FROM executions WHERE id=?', (task['lease_owner'],))['finished_at']
        receipts.append((command, elapsed))
    print('Measured controls (seconds):', receipts)


@pytest.mark.asyncio
async def test_cancel_during_pause_cleanup_and_completion_race_stays_cancelled(system):
    controls = Controls(hold_cleanup=True)
    workers, task, _, _ = await running(system, controls)
    server = workers.servers[task['lease_owner']]
    requested = await workers.control(task['id'], 'pause', task['revision'])
    await server.close_ready.wait()
    await workers.control(task['id'], 'cancel', requested['revision'])
    controls.allow_close.set()
    await settle(workers)
    assert Tasks(workers.db).get(task['id'])['status'] == 'cancelled'
    workers.finish(task['lease_owner'], 'completed', 'late result', '')
    assert Tasks(workers.db).get(task['id'])['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_manager_bundles_prior_requirements_context_and_thread_without_duplicates(system):
    db, registry, chat, settings = system
    workers, task, project, origin = await running(system, Controls())
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET acceptance=? WHERE id=?', ('Prior acceptance', task['id']))
        db.event(conn, 'task.context', {'constraints': ['Prior constraint'], 'selected_context': ['Prior reference']}, project['id'], task['id'])
    scheduler = Scheduler(db, registry, settings, workers, chat)
    def call(action, **args):
        return {'tool': 'manager_action', 'arguments': {'action': action, 'task_id': task['id'], **args},
                'manager_execution_id': 'brainstorm-' + action, 'conversation_id': origin, 'callId': action, 'mode': 'auto'}
    await scheduler.manager_action(project['id'], call('pause'))
    await settle(workers)
    await scheduler.manager_action(project['id'], call('continue', objective='New instruction', acceptance='New acceptance',
                               constraints=['New constraint'], selected_context=['New reference']))
    same = Tasks(db).get(task['id'])
    assert same['description'].startswith('Original requirement') and 'New instruction' in same['description']
    assert same['acceptance'] == 'Prior acceptance\n\nNew acceptance'
    context = json.loads(db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task['id'],))['payload'])
    assert context['constraints'] == ['Prior constraint', 'New constraint']
    assert context['selected_context'] == ['Prior reference', 'New reference']
    await scheduler.manager_action(project['id'], call('continue', objective='New instruction'))
    assert Tasks(db).get(task['id'])['description'] == same['description']
    faults = Faults()
    resumed = Workers(db, registry, settings, faults.factory)
    resumed.launch(Tasks(db).claim(2))
    await settle(resumed)
    assert faults.resumed == [same['native_thread']]
    assert 'Inspect retained partial files' in faults.calls[0]['continuation']
    assert len(db.all('SELECT * FROM tasks')) == 1
    assert len(db.all('SELECT * FROM executions')) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,state', [(2, 'paused'), (3, 'cancelled'), (1, 'waiting')])
async def test_restart_never_replays_requested_controls_or_changes_legacy_intent(system, mode, state):
    db, registry, _, settings = system
    task, _, _ = task_in(system)
    task = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET stop_requested=?,retry_at=? WHERE id=?', (mode, time.time()+3600, task['lease_owner']))
    faults = Faults()
    restored = Workers(Database(db.path), registry, settings, faults.factory)
    await restored.recover()
    assert Tasks(db).get(task['id'])['status'] == state
    assert not faults.calls and not restored.jobs


@pytest.mark.asyncio
async def test_bundle_overflow_and_mode_scope_guards_are_atomic(system):
    db, registry, chat, settings = system
    task, project, origin = task_in(system)
    task = Tasks(db).control(task['id'], 'pause', task['revision'])
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings), chat)
    call = {'tool': 'manager_action', 'arguments': {'action': 'continue', 'task_id': task['id'], 'objective': 'x'*24000},
            'manager_execution_id': 'bundle', 'conversation_id': origin, 'callId': 'bundle'}
    with pytest.raises(ValueError, match='limits'):
        await scheduler.manager_action(project['id'], call)
    assert Tasks(db).get(task['id']) == task
    call['arguments'] = {'action': 'pause', 'task_id': task['id']}
    with pytest.raises(ValueError, match='read-only'):
        await scheduler.manager_action(project['id'], {**call, 'mode': 'conversation'})
    with pytest.raises(ValueError, match='another project'):
        await scheduler.manager_action(registry.list()[1]['id'], call)


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['pause', 'cancel'])
async def test_control_of_surviving_owned_process_never_replays_and_reaps_children(system, command):
    db, registry, _, settings = system
    task, _, _ = task_in(system)
    task = Tasks(db).claim(2)
    pidfile = settings.data_dir/'child.pid'
    program = "import subprocess,sys,signal,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path(sys.argv[1]).write_text(str(child.pid)); signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', program, str(pidfile), 'app-server', start_new_session=True)
    try:
        for _ in range(100):
            if pidfile.exists():
                break
            await asyncio.sleep(.01)
        child_pid = int(pidfile.read_text())
        child_identity = process_start(child_pid)
        with db.transaction() as conn:
            conn.execute('UPDATE executions SET pid=?,process_start=?,status=? WHERE id=?',
                         (process.pid, process_start(process.pid), 'running', task['lease_owner']))
        faults = Faults()
        workers = Workers(db, registry, settings, faults.factory)
        await workers.control(task['id'], command, task['revision'])
        if command == 'cancel':
            await asyncio.wait_for(process.wait(), .5)
        await workers.recover()  # Pause escalates receipt-proven SIGTERM-resistant group.
        await asyncio.wait_for(process.wait(), .5)
        await workers.recover()
        assert process_start(child_pid) != child_identity
        assert Tasks(db).get(task['id'])['status'] == ('paused' if command == 'pause' else 'cancelled')
        assert Tasks(db).get(task['id'])['lease_owner'] is None
        assert not faults.calls
    finally:
        if process.returncode is None:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


@pytest.mark.asyncio
async def test_paused_manager_assignment_can_be_cancelled_and_stale_revision_cannot_resume(system):
    db, registry, chat, settings = system
    task, project, origin = task_in(system)
    task = Tasks(db).control(task['id'], 'pause', task['revision'])
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings), chat)
    call = {'tool': 'manager_action', 'arguments': {'action': 'cancel', 'task_id': task['id']},
            'manager_execution_id': 'cancel', 'conversation_id': origin, 'callId': 'cancel', 'mode': 'auto'}
    result = await scheduler.manager_action(project['id'], call)
    assert result['status'] == 'cancelled'
    with pytest.raises(ValueError, match='changed'):
        Tasks(db).control(task['id'], 'continue', task['revision'])


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['pause', 'cancel'])
async def test_pending_questions_expire_and_late_approval_cannot_execute(system, command):
    from ai_manager.execution_ui import execution_summary
    controls = Controls(hold_cleanup=True)
    workers, task, project, _ = await running(system, controls)
    eid = task['lease_owner']
    question = asyncio.create_task(workers.question(eid, 'item/commandExecution/requestApproval', {}))
    await asyncio.sleep(.02)
    assert workers.db.one("SELECT status FROM questions WHERE execution_id=?", (eid,))['status'] == 'pending'
    task = Tasks(workers.db).get(task['id'])
    try:
        await workers.control(task['id'], command, task['revision'])
        assert workers.db.one("SELECT status FROM questions WHERE execution_id=?", (eid,))['status'] == 'expired'
        with pytest.raises(ValueError, match='stop requested'):
            await asyncio.wait_for(question, 1)
        with pytest.raises(ValueError, match='stop requested'):
            await workers.question(eid, 'item/commandExecution/requestApproval', {})
        await workers.servers[eid].close_ready.wait()
        assert execution_summary(workers.db, project['id'])['active_agents'] == 0
        controls.allow_close.set()
        await settle(workers)
    finally:
        controls.allow_close.set()
        await workers.close()


@pytest.mark.asyncio
async def test_unprovable_surviving_process_keeps_request_and_project_exclusivity(system):
    db, registry, _, settings = system
    task, _, _ = task_in(system)
    task = Tasks(db).claim(2)
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)', start_new_session=True)
    try:
        with db.transaction() as conn:
            conn.execute('UPDATE executions SET pid=?,process_start=? WHERE id=?', (process.pid, process_start(process.pid), task['lease_owner']))
        workers = Workers(db, registry, settings, Faults().factory)
        await workers.control(task['id'], 'cancel', task['revision'])
        await workers.recover()
        assert Tasks(db).get(task['id'])['status'] == 'cancel_requested'
        assert Tasks(db).get(task['id'])['lease_owner'] == task['lease_owner']
        assert db.one('SELECT finished_at FROM executions WHERE id=?', (task['lease_owner'],))['finished_at'] is None
        assert process.returncode is None
        assert Tasks(db).claim(2) is None
    finally:
        process.terminate()
        await process.wait()

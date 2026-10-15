"""Owner's task policy: bounded repair rounds, one worker, no review/escalation loop."""
import json
import time
from pathlib import Path

import pytest
from test_minimal_flow import system as system_fixture

from ai_manager.assignment_workflow import REPAIR_LIMIT, allowed, claim, consume_repair, finish_wake, get, put, update
from ai_manager.codex import MANAGER_INSTRUCTIONS
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.tasks import Tasks
from ai_manager.verification import plan
from ai_manager.workers import WORKER_INSTRUCTIONS, Workers

system = system_fixture


def test_repair_budget_survives_restart_resets_and_final_success(system):
    db, registry, chat, _ = system
    tasks = Tasks(db)
    task = tasks.add(registry.list()[0]['id'], 'Fix behavior', chat.conversation()['id'])
    for number in range(1, REPAIR_LIMIT + 1):
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        current = tasks.get(task['id'])
        tasks.control(task['id'], 'continue', current['revision'])
        # Reopen storage, including UI continuation's reset_retry flag.
        restored = Database(db.path)
        assert restored.setting('assignment_workflow:' + task['id'])['repair_rounds'] == number
        assert restored.setting('assignment_workflow:' + task['id'])['state'] == 'actionable'
        # Revision replay cannot charge another round or erase the existing counter.
        with pytest.raises(ValueError, match='changed'):
            tasks.control(task['id'], 'continue', current['revision'])
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task['id'],))
        update(db, conn, task['id'], 'complete', 'Fifth repair succeeded')
    assert tasks.get(task['id'])['job_followup']['state'] == 'complete'
    before = tasks.get(task['id'])
    with pytest.raises(ValueError, match='Five repair rounds'):
        tasks.control(task['id'], 'continue', before['revision'])
    assert tasks.get(task['id'])['revision'] == before['revision']
    assert tasks.get(task['id'])['job_followup']['repair_rounds'] == 5


def test_holds_and_transport_resume_do_not_charge_or_reset_repair_budget(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Authorized task', chat.conversation()['id'])
    with db.transaction() as conn:
        put(conn, task['id'], {'repair_rounds': 4, 'state': 'owner_wait', 'attempts': 8})
        # Old backoff count is not mislabelled as new repair usage.
        update(db, conn, task['id'], 'actionable', 'Explicit paused resume', reset_retry=True)
        assert get(conn, task['id'])['state'] == 'actionable'
        consume_repair(conn, {'id': task['id'], 'lifecycle': 'paused'})
        update(db, conn, task['id'], 'owner_wait', 'Deliberate hold', reset_retry=True)
        assert get(conn, task['id'])['repair_rounds'] == 4
        consume_repair(conn, {'id': task['id'], 'lifecycle': 'failed'})
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        value = update(db, conn, task['id'], 'actionable', 'Still failed', reset_retry=True)
        assert value['state'] == 'owner_wait' and value['repair_rounds'] == 5
    assert db.one("SELECT payload FROM events WHERE kind='assignment.followup' ORDER BY seq DESC")['payload'] == json.dumps(
        {'state': value['state'], 'reason': value['reason']})


def test_worker_contract_one_check_pass_five_repairs_and_destructive_confirmation():
    task_plan = plan()
    assert task_plan['repair_limit'] == 5 and 'escalate' not in task_plan and 'tier' not in task_plan
    assert 'at most five rounds' in WORKER_INSTRUCTIONS and 'RESULT: PASSED' in WORKER_INSTRUCTIONS
    assert 'up to five rounds total' in task_plan['report']
    assert 'at most five repair rounds' in MANAGER_INSTRUCTIONS
    assert 'honor the lower' in WORKER_INSTRUCTIONS
    assert 'carry the lower' in MANAGER_INSTRUCTIONS
    assert 'Destructive data/project deletion or force-push needs owner confirmation' in WORKER_INSTRUCTIONS
    source = (Path(__file__).parents[1] / 'ai_manager/workers.py').read_text()
    assert 'async def review(' not in source and 'REVIEW_INSTRUCTIONS' not in source


def test_manager_wake_cap_cannot_block_fifth_repair_launch_or_success(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Fix', chat.conversation()['id'])
    with db.transaction() as conn:
        put(conn, task['id'], {'repair_rounds': 5, 'followup_rounds': 5, 'state': 'actionable'})
        # Fifth wake has atomically queued its last repair, so allow that worker turn.
        value = update(db, conn, task['id'], 'actionable', 'Fifth queued repair', reset_retry=True)
        assert value['state'] == 'actionable'
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task['id'],))
        assert update(db, conn, task['id'], 'complete', 'Fifth repair passed')['state'] == 'complete'
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        value = update(db, conn, task['id'], 'actionable', 'Failure remains', reset_retry=True)
        assert value['state'] == 'owner_wait'
        assert not allowed(conn, dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()), value)


def test_exhausted_manager_wake_reports_once_without_reopening_held_work(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Fix', chat.conversation()['id'])
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        conn.execute('INSERT INTO settings VALUES(?,?)', ('execution_recovery:wake', json.dumps(task['id'])))
        put(conn, task['id'], {'state': 'actionable', 'followup_rounds': 5})
        finish_wake(db, conn, 'wake')
        finish_wake(db, conn, 'wake')
        assert get(conn, task['id'])['state'] == 'owner_wait'
        assert conn.execute('SELECT COUNT(*) FROM messages WHERE id=?', ('repair-limit:' + task['id'],)).fetchone()[0] == 1
        update(db, conn, task['id'], 'deferred', 'Unrelated owner hold')
        finish_wake(db, conn, 'wake')
        assert get(conn, task['id'])['state'] == 'deferred'


@pytest.mark.parametrize('used', [2, 3, 4, 5, 6])
def test_existing_usage_and_holds_survive_policy_activation(system, used):
    db, registry, chat, _ = system
    tasks = Tasks(db)
    task = tasks.add(registry.list()[0]['id'], 'Retained failure', chat.conversation()['id'])
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        put(conn, task['id'], {'state': 'owner_wait', 'repair_rounds': used,
                              'followup_rounds': 3, 'attempts': 8, 'evidence': 'Actual three used'})
    restored = Database(db.path)
    saved = restored.setting('assignment_workflow:' + task['id'])
    assert saved['state'] == 'owner_wait' and saved['repair_rounds'] == used
    with db.transaction() as conn:
        assert not allowed(conn, tasks.get(task['id']), saved)
    if used >= 5:
        with pytest.raises(ValueError, match='Five repair rounds exhausted'):
            tasks.control(task['id'], 'continue', tasks.get(task['id'])['revision'])
        assert restored.setting('assignment_workflow:' + task['id']) == saved
    else:
        continued = tasks.control(task['id'], 'continue', tasks.get(task['id'])['revision'])
        assert continued['id'] == task['id']
        assert continued['job_followup']['repair_rounds'] == used + 1
        assert continued['job_followup']['followup_rounds'] == 3
        assert continued['job_followup']['attempts'] == 8
        assert continued['job_followup']['evidence'] == 'Actual three used'


def test_manager_wakes_continue_from_three_to_five_without_reset(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Retained failure', chat.conversation()['id'])
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        put(conn, task['id'], {'state': 'actionable', 'followup_rounds': 3, 'repair_rounds': 3})
        for number in (4, 5):
            value = get(conn, task['id'])
            value.update(idle_since=0, next_at=0)
            put(conn, task['id'], value)
            wake = claim(conn, task['id'], 'wake-' + str(number), time.time(),
                         task['project_id'], task['origin_conversation'])
            assert wake['followup_rounds'] == number
            assert wake['repair_rounds'] == 3
        assert not allowed(conn, dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()),
                           get(conn, task['id']))
        finish_wake(db, conn, 'wake-5')
        assert get(conn, task['id'])['state'] == 'owner_wait'
        assert 'five follow-up rounds' in conn.execute(
            'SELECT content FROM messages WHERE id=?', ('repair-limit:' + task['id'],)).fetchone()[0]


@pytest.mark.asyncio
async def test_manager_continuation_preserves_discrepancy_context_and_exhausts_at_five(system):
    db, registry, chat, settings = system
    tasks = Tasks(db)
    task = tasks.add(registry.list()[0]['id'], 'Recording failure', chat.conversation()['id'])
    evidence = {'selected_context': ['Actual three used; machine two; retain original report']}
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='failed',native_thread='retained' WHERE id=?", (task['id'],))
        put(conn, task['id'], {'state': 'owner_wait', 'repair_rounds': 2})
        db.event(conn, 'task.context', evidence, task['project_id'], task['id'])
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings), chat)
    for number in (3, 4, 5):
        call = {'tool': 'manager_action', 'arguments': {'action': 'continue', 'task_id': task['id']},
                'manager_execution_id': 'owner-' + str(number), 'conversation_id': task['origin_conversation'],
                'callId': 'first'}
        await scheduler.manager_action(task['project_id'], call)
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        await scheduler.manager_action(task['project_id'], {**call, 'callId': 'replay'})
        assert tasks.get(task['id'])['job_followup']['repair_rounds'] == number
        assert tasks.get(task['id'])['native_thread'] == 'retained'
    with pytest.raises(ValueError, match='Five repair rounds exhausted'):
        await scheduler.manager_action(task['project_id'], {**call, 'manager_execution_id': 'sixth'})
    assert tasks.get(task['id'])['job_followup']['repair_rounds'] == 5
    assert all(json.loads(row['payload']) == evidence for row in db.all(
        "SELECT payload FROM events WHERE kind='task.context' AND task_id=?", (task['id'],)))

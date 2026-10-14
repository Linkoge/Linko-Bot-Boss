"""Owner's replacement policy: bounded repair, one worker, exact release evidence."""
import json
from pathlib import Path

import pytest
from test_minimal_flow import system as system_fixture

from ai_manager.assignment_workflow import REPAIR_LIMIT, allowed, consume_repair, finish_wake, get, put, update
from ai_manager.db import Database
from ai_manager.deployment import release_attested
from ai_manager.tasks import Tasks
from ai_manager.verification import plan, release_policy
from ai_manager.workers import WORKER_INSTRUCTIONS

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
        update(db, conn, task['id'], 'complete', 'Third repair succeeded')
    assert tasks.get(task['id'])['job_followup']['state'] == 'complete'
    before = tasks.get(task['id'])
    with pytest.raises(ValueError, match='Three repair rounds'):
        tasks.control(task['id'], 'continue', before['revision'])
    assert tasks.get(task['id'])['revision'] == before['revision']
    assert tasks.get(task['id'])['job_followup']['repair_rounds'] == 3


def test_holds_and_transport_resume_do_not_charge_or_reset_repair_budget(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Authorized task', chat.conversation()['id'])
    with db.transaction() as conn:
        put(conn, task['id'], {'repair_rounds': 2, 'state': 'owner_wait', 'attempts': 8})
        # Old backoff count is not mislabelled as new repair usage.
        update(db, conn, task['id'], 'actionable', 'Explicit paused resume', reset_retry=True)
        assert get(conn, task['id'])['state'] == 'actionable'
        consume_repair(conn, {'id': task['id'], 'lifecycle': 'paused'})
        update(db, conn, task['id'], 'owner_wait', 'Deliberate hold', reset_retry=True)
        assert get(conn, task['id'])['repair_rounds'] == 2
        consume_repair(conn, {'id': task['id'], 'lifecycle': 'failed'})
        conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        value = update(db, conn, task['id'], 'actionable', 'Still failed', reset_retry=True)
        assert value['state'] == 'owner_wait' and value['repair_rounds'] == 3
    assert db.one("SELECT payload FROM events WHERE kind='assignment.followup' ORDER BY seq DESC")['payload'] == json.dumps(
        {'state': value['state'], 'reason': value['reason']})


def test_deploy_attestation_requires_full_backend_for_new_policy():
    policy = release_policy(['docs/change.md'], [{'tier': 'small'}])
    common = {'execution_id': 'e', 'commit': 'c', 'tier_checks': 'passed', 'release_checks': 'passed'}
    manifest = {'commit': 'c', 'verification': policy, 'auto_deploy': common}
    assert policy['tier'] == 'deploy' and policy['full_backend']
    assert release_attested(manifest, 'e')
    assert not release_attested(manifest, 'other')
    policy['full_backend'] = False
    assert not release_attested(manifest, 'e')
    # A new v3 manifest cannot impersonate the immutable historical attestation shape.
    manifest['auto_deploy'] = {'execution_id': 'e', 'commit': 'c', 'tier_checks': 'passed', 'full_suite': 'passed'}
    assert not release_attested(manifest, 'e')
    manifest['auto_deploy'] = common
    policy['full_backend'] = True
    manifest['auto_deploy']['commit'] = 'other'
    assert not release_attested(manifest, 'e')


def test_worker_contract_no_review_escalation_and_destructive_confirmation():
    task_plan = plan({'tier': 'risky'}, integration=True)
    assert task_plan['repair_limit'] == 3 and 'escalate' not in task_plan
    assert 'no separate review/escalation loop' in WORKER_INSTRUCTIONS
    assert 'Destructive data/project deletion or force-push needs owner confirmation' in WORKER_INSTRUCTIONS
    # No regression to blanket backend review in the actual completion path.
    source = (Path(__file__).parents[1] / 'ai_manager/workers.py').read_text()
    postcheck = source.split('async def _verify(', 1)[1].split('async def review(', 1)[0]
    assert 'await self.review' not in postcheck and 'review_reasons(' not in postcheck


def test_manager_wake_cap_cannot_block_third_repair_launch_or_success(system):
    db, registry, chat, _ = system
    task = Tasks(db).add(registry.list()[0]['id'], 'Fix', chat.conversation()['id'])
    with db.transaction() as conn:
        put(conn, task['id'], {'repair_rounds': 3, 'followup_rounds': 3, 'state': 'actionable'})
        # Third wake has atomically queued its last repair, so allow that worker turn.
        value = update(db, conn, task['id'], 'actionable', 'Third queued repair', reset_retry=True)
        assert value['state'] == 'actionable'
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task['id'],))
        assert update(db, conn, task['id'], 'complete', 'Third repair passed')['state'] == 'complete'
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
        put(conn, task['id'], {'state': 'actionable', 'followup_rounds': 3})
        finish_wake(db, conn, 'wake')
        finish_wake(db, conn, 'wake')
        assert get(conn, task['id'])['state'] == 'owner_wait'
        assert conn.execute('SELECT COUNT(*) FROM messages WHERE id=?', ('repair-limit:' + task['id'],)).fetchone()[0] == 1
        update(db, conn, task['id'], 'deferred', 'Unrelated owner hold')
        finish_wake(db, conn, 'wake')
        assert get(conn, task['id'])['state'] == 'deferred'

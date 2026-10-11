import asyncio
import time

import pytest
from fake_codex import FakeServer
from test_orchestration import ReviewServer
from test_workers import InterruptedServer
from test_workers import worker_env as fixture_worker_env

from ai_manager.chat import Chat
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

worker_env = fixture_worker_env


async def test_verification_retry_does_not_replay_worker(worker_env):
    settings, db, registry, project, _ = worker_env
    tasks = Tasks(db)
    task = tasks.add(project["id"], "Read fixture", "Report fixture")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    assert job["result"] == "accepted" and job["status"] == "queued"
    await workers.jobs[job["execution_id"]]
    verifier = Verifier(db, registry, settings, ReviewServer)
    current = tasks.get(task["id"])
    tasks.transition(
        task["id"],
        "blocked",
        current["revision"],
        blocker="Legacy configuration blocker",
        blocker_code="missing_verification",
    )
    blocked = tasks.get(task["id"])
    with pytest.raises(ValueError, match="retry verification"):
        await workers.resume(task["id"], blocked["revision"])
    assert verifier.retry(task["id"], blocked["revision"])["result"] == "accepted"
    assert tasks.get(task["id"])["status"] == "verifying"
    assert len(db.all("SELECT * FROM executions")) == 1
    await workers.close()


async def test_scheduler_skips_dependencies_without_reconcile_storm(worker_env):
    settings, db, registry, project, _ = worker_env
    tasks = Tasks(db)
    first = tasks.add(project["id"], "Blocked prerequisite", "Prerequisite")
    tasks.transition(first["id"], "blocked", first["revision"], blocker="Needs owner decision")
    dependent = tasks.add(project["id"], "Dependent", "Dependent", dependencies=[first["id"]])
    workers = Workers(db, registry, settings, FakeServer)
    scheduler = Scheduler(
        db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
    )
    db.set_setting("scheduler_enabled", True)
    count = len(db.all("SELECT * FROM events"))
    for _ in range(4):
        await scheduler.tick()
    assert len(db.all("SELECT * FROM events")) == count
    assert not workers.jobs
    ready = workers.readiness(dependent["id"])
    assert "Unfinished dependencies" in ready["reason"]
    assert ready["dependencies"][0]["blocker"] == "Needs owner decision"
    assert scheduler.snapshot()["last_tick"]


async def test_rejected_resume_preserves_state_and_stale_identity(worker_env):
    settings, db, registry, project, _ = worker_env
    tasks = Tasks(db)
    task = tasks.add(project["id"], "Interrupted", "Reconcile")
    workers = Workers(db, registry, settings, InterruptedServer)
    job = await workers.start(task["id"])
    for _ in range(100):
        row = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
        if row["turn_id"]:
            break
        await asyncio.sleep(0.01)
    workers.servers[job["execution_id"]].queues[row["thread_id"]].put_nowait({"method": "transport/lost", "params": {}})
    await workers.jobs[job["execution_id"]]
    blocked = tasks.get(task["id"])
    # Stored PID with an old start identity must not prevent deliberate resume.
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET process_start='stale' WHERE id=?", (job["execution_id"],))
    with pytest.raises(ValueError, match="explicitly record reconciliation"):
        await workers.resume(task["id"], blocked["revision"])
    assert tasks.get(task["id"]) == blocked
    registry.update(project["id"], project["revision"], {"status": "paused"})
    with pytest.raises(ValueError, match="Project must be active"):
        await workers.resume(
            task["id"], blocked["revision"], True, "Read-only task; inspected workspace and operations"
        )
    assert tasks.get(task["id"]) == blocked
    assert len(db.all("SELECT * FROM executions")) == 1
    await workers.close()


async def test_launch_failure_is_logged_and_retryable(worker_env):
    settings, db, registry, project, _ = worker_env

    class BrokenServer(FakeServer):
        async def start(self):
            raise ValueError("fixture authentication failure")

    task = Tasks(db).add(project["id"], "Launch failure", "Observe error")
    workers = Workers(db, registry, settings, BrokenServer)
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    blocked = Tasks(db).get(task["id"])
    assert blocked["status"] == "blocked" and blocked["blocker"] == "fixture authentication failure"
    assert workers.readiness(task["id"])["can_retry"]
    assert not db.one("SELECT turn_id FROM executions")["turn_id"]
    workers.factory = FakeServer
    retried = await workers.retry(task["id"], blocked["revision"])
    await workers.jobs[retried["execution_id"]]
    assert Tasks(db).get(task["id"])["status"] == "verifying"
    await workers.close()


async def test_execute_mode_records_tasks_even_if_model_does_not_delegate(worker_env):
    settings, db, registry, project, _ = worker_env
    chat = Chat(db, registry, FakeServer(settings))
    calls = []

    async def handler(selected, params):
        calls.append(params)
        task = Tasks(db).add(selected, params["arguments"]["goal"], "Owner requested action")
        return {
            "task_ids": [task["id"]],
            "tasks": [
                {
                    "task_id": task["id"],
                    "project": project["name"],
                    "status": "pending",
                    "worker": None,
                    "blocker": "Scheduler paused",
                }
            ],
        }

    chat.action_handler = handler
    job = await chat.submit(project["id"], "Perform action", "execute")
    await chat.jobs[job["execution_id"]]
    assert calls[0]["arguments"]["action"] == "plan_goal"
    message = db.one("SELECT content FROM messages WHERE role='assistant'")["content"]
    assert "Task " in message and "Scheduler paused" in message
    assert len(Tasks(db).list()) == 1
    await chat.close()


def test_recovery_preserves_terminal_dispositions_and_clears_lease(worker_env):
    settings, db, registry, project, _ = worker_env
    task = Tasks(db).add(project["id"], "Cancelled", "Preserve")
    cancelled = Tasks(db).transition(task["id"], "cancelled", task["revision"])
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES('orphan',?,?,'worker','running',?)",
            (task["id"], project["id"], time.time()),
        )
    Workers(db, registry, settings).recover()
    assert Tasks(db).get(task["id"]) == cancelled


async def test_invalid_stored_workspace_fails_without_stranded_lease(worker_env):
    settings, db, registry, project, _ = worker_env
    task = Tasks(db).add(project["id"], "Invalid workspace", "Block")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"], {"worktree": "/tmp/outside-isolation", "branch": "invalid"})
    await workers.jobs[job["execution_id"]]
    row = Tasks(db).get(task["id"])
    assert row["status"] == "blocked" and row["lease_owner"] is None
    assert "outside manager isolation" in row["blocker"]
    await workers.close()


async def test_conversation_mode_denies_mutation(worker_env):
    settings, db, registry, project, _ = worker_env

    class MutatingServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                with pytest.raises(ValueError, match="Conversation mode"):
                    await self.tool_handlers[params["threadId"]](
                        {
                            "tool": "manager_action",
                            "callId": "mutation",
                            "arguments": {"action": "plan_goal", "goal": "Unapproved work"},
                        }
                    )
            return await super().request(method, params, timeout)

    chat = Chat(db, registry, MutatingServer(settings))

    async def forbidden(*args):
        raise AssertionError("Mutation handler must not be reached")

    chat.action_handler = forbidden
    job = await chat.submit(project["id"], "Brainstorm only", "conversation")
    await chat.jobs[job["execution_id"]]
    assert Tasks(db).list() == []
    await chat.close()


async def test_resume_reuses_thread_and_workspace_with_stale_pid(worker_env):
    settings, db, registry, project, _ = worker_env
    workers = Workers(db, registry, settings, InterruptedServer)
    task = Tasks(db).add(project["id"], "Pause and resume", "Retain workspace")
    job = await workers.start(task["id"])
    for _ in range(100):
        previous = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
        if previous["turn_id"]:
            break
        await asyncio.sleep(0.01)
    await workers.interrupt(job["execution_id"])
    await workers.jobs[job["execution_id"]]
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET process_start='old-process' WHERE id=?", (job["execution_id"],))
    workers.factory = FakeServer
    paused = Tasks(db).get(task["id"])
    resumed = await workers.resume(task["id"], paused["revision"])
    await workers.jobs[resumed["execution_id"]]
    current = db.one("SELECT * FROM executions WHERE id=?", (resumed["execution_id"],))
    assert current["thread_id"] == previous["thread_id"] and current["worktree"] == previous["worktree"]
    assert current["status"] == "verifying"
    await workers.close()


def test_expired_detached_worker_blocks_without_replay(worker_env):
    settings, db, registry, project, _ = worker_env
    task = Tasks(db).add(project["id"], "Detached claim", "Preserve evidence")
    Tasks(db).claim("detached", lease_seconds=-1)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,pid,process_start,thread_id,turn_id) VALUES('detached',?,?,'worker','running',?,999999,'old','saved-thread','saved-turn')",
            (task["id"], project["id"], time.time()),
        )
    workers = Workers(db, registry, settings, FakeServer)
    workers.detect_stale()
    current = Tasks(db).get(task["id"])
    assert current["status"] == "blocked" and current["lease_owner"] is None
    assert "process observed=False" in current["blocker"]
    execution = db.one("SELECT * FROM executions WHERE id='detached'")
    assert execution["thread_id"] == "saved-thread" and execution["turn_id"] == "saved-turn"
    assert len(db.all("SELECT * FROM executions")) == 1 and not workers.jobs


async def test_completed_batch_pauses_even_with_no_pending_tasks(worker_env):
    settings, db, registry, project, _ = worker_env
    task = Tasks(db).add(project["id"], "Budget complete", "Already verified")
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (task["id"],))
    db.set_setting("scheduler_enabled", True)
    db.set_setting("scheduler_batch", {"limit": 1, "task_ids": [task["id"]]})
    workers = Workers(db, registry, settings, FakeServer)
    scheduler = Scheduler(
        db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
    )
    await scheduler.tick()
    assert not db.setting("scheduler_enabled") and not workers.jobs


async def test_verification_retry_respects_project_concurrency(worker_env):
    settings, db, registry, project, _ = worker_env
    tasks = Tasks(db)
    task = tasks.add(project["id"], "Completed output", "Verify output")
    workers = Workers(db, registry, settings, FakeServer)
    launch = await workers.start(task["id"])
    await workers.jobs[launch["execution_id"]]
    verifier = Verifier(db, registry, settings)
    assert not await verifier.verify(task["id"])
    blocked = tasks.get(task["id"])
    db.set_setting(f"verification_commands:{project['id']}", [["python3", "-m", "unittest"]])
    other = tasks.add(project["id"], "Other active worker", "Wait")
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (other["id"],))
    with pytest.raises(ValueError, match="Project already has active work"):
        verifier.retry(task["id"], blocked["revision"])
    assert tasks.get(task["id"]) == blocked
    await workers.close()

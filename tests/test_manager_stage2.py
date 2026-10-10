import asyncio
import json
from copy import deepcopy

import pytest
from fake_codex import FakeServer
from test_orchestration import ReviewServer
from test_workers import worker_env as worker_env

from ai_manager.chat import Chat
from ai_manager.codex import CodexError
from ai_manager.db import Database
from ai_manager.orchestration import PLAN_SCHEMA, Planner, Scheduler, Verifier
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


def assignment(description="Display newest tasks first", kind="code", dependencies=()):
    spec = {
        "files": [],
        "sections": [],
        "allowed_paths": [],
        "evidence_file": "",
        "require_commands": kind == "code",
        "integration": "none",
    }
    if kind == "investigation":
        spec.update(files=["investigation.json"], evidence_file="investigation.json")
    return {
        "description": description,
        "acceptance": "Requested objective is satisfied with evidence",
        "verification": "Independent acceptance review and applicable registered tests",
        "priority": 50,
        "dependencies": list(dependencies),
        "kind": kind,
        "verification_spec": spec,
    }


def proposal(*items, reason="", existing=""):
    return {"tasks": list(items), "decomposition_reason": reason, "existing_task_id": existing}


def planning_stub(monkeypatch, result):
    from ai_manager.orchestration import structured_turn

    calls = []

    async def turn(*args, **kwargs):
        if args[3] != "planning":
            return await structured_turn(*args, **kwargs)
        calls.append(args)
        return deepcopy(result), None

    monkeypatch.setattr("ai_manager.orchestration.structured_turn", turn)
    return calls


def controller(worker_env, factory=FakeServer):
    settings, db, registry, _, _ = worker_env
    workers = Workers(db, registry, settings, factory)
    planner = Planner(db, registry, settings, FakeServer)
    scheduler = Scheduler(db, registry, settings, workers, Verifier(db, registry, settings, ReviewServer), planner)
    return planner, workers, scheduler


@pytest.mark.parametrize(
    "kind,goal",
    [
        ("code", "Make the Tasks page display newest tasks first"),
        ("investigation", "Investigate why the Tasks page has the wrong ordering"),
    ],
)
async def test_simple_request_persists_one_complete_assignment(worker_env, monkeypatch, kind, goal):
    _, db, _, project, _ = worker_env
    calls = planning_stub(monkeypatch, proposal(assignment(goal, kind)))
    _, workers, scheduler = controller(worker_env)
    result = await scheduler.manager_action(
        project["id"],
        {
            "tool": "manager_action",
            "callId": "simple",
            "arguments": {"action": "plan_goal", "goal": goal},
        },
    )
    assert len(Tasks(db).list()) == len(result["task_ids"]) == len(result["tasks"]) == 1
    task = Tasks(db).get(result["task_ids"][0])
    assert task["description"] == goal and task["kind"] == kind
    assert task["status"] == result["tasks"][0]["status"] == "pending"
    assert result["tasks"][0]["worker"] is None and not workers.jobs
    assert not db.all("SELECT * FROM dependencies")
    assert calls[0][6] is PLAN_SCHEMA and calls[0][5]["goal"] == goal


def test_multiple_assignments_require_reason_and_dependencies_remain_explicit(worker_env):
    settings, db, registry, project, _ = worker_env
    planner = Planner(db, registry, settings)
    items = [assignment("Produce an API contract"), assignment("Build client from that contract", dependencies=[0])]
    with pytest.raises(ValueError, match="decomposition reason"):
        planner.apply(project["id"], "Complex work", proposal(*items), "missing-reason")
    assert not Tasks(db).list() and not db.all("SELECT * FROM plans")
    result = planner.apply(
        project["id"],
        "Two explicit deliverables",
        proposal(*items, reason="Owner explicitly requested separate contract and client assignments"),
        "complex",
    )
    assert len(result["task_ids"]) == 2
    assert db.one("SELECT * FROM dependencies") == {
        "task_id": result["task_ids"][1],
        "depends_on": result["task_ids"][0],
    }
    assert "Unfinished dependencies" in Workers(db, registry, settings).readiness(result["task_ids"][1])["reason"]
    items[0]["dependencies"] = [1]
    with pytest.raises(ValueError, match="cyclic"):
        planner.apply(project["id"], "Invalid", proposal(*items, reason="Separate deliverables"), "cyclic")
    assert len(Tasks(db).list()) == 2


async def test_independent_requests_queue_without_dependencies_after_failure(worker_env, monkeypatch):
    _, db, _, project, _ = worker_env
    planner, workers, scheduler = controller(worker_env)
    planning_stub(monkeypatch, proposal(assignment("Change sorting")))
    first = (await planner.plan(project["id"], "Change sorting", "first"))["task_ids"][0]
    planning_stub(monkeypatch, proposal(assignment("Fix unrelated label")))
    second = (await planner.plan(project["id"], "Fix unrelated label", "second"))["task_ids"][0]
    assert not db.all("SELECT * FROM dependencies")
    db.set_setting("scheduler_enabled", True)
    await scheduler.tick()
    assert Tasks(db).get(first)["status"] == "running"
    assert Tasks(db).get(second)["status"] == "pending"
    assert "active work" in workers.readiness(second)["reason"]
    await asyncio.gather(*list(workers.jobs.values()))
    Tasks(db).transition(first, "failed", Tasks(db).get(first)["revision"], blocker="Fixture acceptance failure")
    assert workers.readiness(second)["dispatchable"]
    await scheduler.tick()
    assert Tasks(db).get(second)["status"] == "running"
    await asyncio.gather(*list(workers.jobs.values()))
    assert Tasks(db).get(first)["status"] == "failed" and len(Tasks(db).list()) == 2
    await scheduler.close()
    await workers.close()


@pytest.mark.parametrize("status", ["pending", "paused", "failed", "blocked"])
async def test_exact_repeat_bypasses_planning_preserves_task_and_history(worker_env, monkeypatch, status):
    settings, db, registry, project, _ = worker_env
    planner = Planner(db, registry, settings)
    original = planner.apply(project["id"], "Change sorting", proposal(assignment()), "original")
    task_id = original["task_ids"][0]
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET status=?,blocker='Original outcome' WHERE id=?", (status, task_id))
        conn.execute(
            "INSERT INTO executions(id,project_id,task_id,agent_id,status,output,started_at,finished_at) VALUES('history',?,?,?,'failed','Original output',1,2)",
            (project["id"], task_id, project["agent_id"]),
        )
    before_task = Tasks(db).get(task_id)
    before_execution = db.one("SELECT * FROM executions WHERE id='history'")
    calls = planning_stub(monkeypatch, proposal(assignment("Unnecessary recovery")))
    result = await planner.plan(project["id"], " Change sorting ", "repeat")
    assert result["task_ids"] == original["task_ids"] and result["reused"]
    assert await planner.plan(project["id"], "Any replay text", "repeat") == planner.existing_result(result["plan_id"])
    assert not calls and len(Tasks(db).list()) == 1
    assert Tasks(db).get(task_id) == before_task
    assert db.one("SELECT * FROM executions WHERE id='history'") == before_execution


async def test_contextual_resume_reuses_failed_assignment_without_retry(worker_env, monkeypatch):
    _, db, _, project, _ = worker_env
    planner, workers, scheduler = controller(worker_env)
    original = planner.apply(project["id"], "Change sorting", proposal(assignment()), "original")
    task_id = original["task_ids"][0]
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET status='failed',blocker='Usage limit interruption' WHERE id=?", (task_id,))
    calls = planning_stub(monkeypatch, proposal(existing=task_id))
    result = await scheduler.manager_action(
        project["id"],
        {
            "tool": "manager_action",
            "callId": "resume",
            "arguments": {"action": "plan_goal", "goal": "Continue that work"},
        },
    )
    assert calls[0][5]["existing_tasks"][0]["id"] == task_id
    assert result["task_ids"] == [task_id] and result["tasks"][0]["status"] == "failed"
    assert not workers.jobs and len(Tasks(db).list()) == 1


async def test_overlapping_identical_delegations_reuse_one_assignment(worker_env, monkeypatch):
    settings, db, registry, project, _ = worker_env
    planner = Planner(db, registry, settings)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return proposal(assignment()), None

    monkeypatch.setattr("ai_manager.orchestration.structured_turn", delayed)
    first = asyncio.create_task(planner.plan(project["id"], "Change sorting", "overlap-first"))
    await started.wait()
    second = asyncio.create_task(planner.plan(project["id"], "Change sorting", "overlap-second"))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert results[0]["task_ids"] == results[1]["task_ids"]
    assert results[1]["reused"] and len(Tasks(db).list()) == 1
    assert len(db.all("SELECT * FROM plans")) == 2


def test_explicit_independent_deliverables_have_no_dependency_edges(worker_env):
    settings, db, registry, project, _ = worker_env
    result = Planner(db, registry, settings).apply(
        project["id"],
        "Create two separate deliverables",
        proposal(
            assignment("Write onboarding guide"),
            assignment("Produce storage report"),
            reason="Owner explicitly requested two independent deliverables",
        ),
        "independent-deliverables",
    )
    assert len(result["task_ids"]) == 2 and not db.all("SELECT * FROM dependencies")


@pytest.mark.parametrize("invalid", ["unknown", "completed", "cancelled", "different_project", "mixed"])
def test_reuse_validates_existing_assignment_scope_and_state(worker_env, invalid):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Old task", "Acceptance")
    with db.transaction() as conn:
        if invalid in {"completed", "cancelled"}:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (invalid, task["id"]))
    selected = project["id"]
    if invalid == "different_project":
        other = source.parent / "other"
        other.mkdir()
        selected = registry.add(other)["id"]
    reused = "missing" if invalid == "unknown" else task["id"]
    result = proposal(*([assignment()] if invalid == "mixed" else []), existing=reused)
    with pytest.raises(ValueError, match="Reuse"):
        Planner(db, registry, settings).apply(selected, "Resume", result, "invalid")
    assert len(Tasks(db).list()) == 1 and not db.all("SELECT * FROM plans")


async def test_failed_planning_creates_no_task_chain_and_explicit_retry_applies_once(worker_env, monkeypatch):
    settings, db, registry, project, _ = worker_env
    planner = Planner(db, registry, settings)

    async def failure(*args, **kwargs):
        raise CodexError("Planning interrupted by usage limit")

    monkeypatch.setattr("ai_manager.orchestration.structured_turn", failure)
    with pytest.raises(CodexError):
        await planner.plan(project["id"], "Change sorting", "interrupted")
    assert not Tasks(db).list() and not db.all("SELECT * FROM plans")
    planning_stub(monkeypatch, proposal(assignment()))
    result = await planner.plan(project["id"], "Change sorting", "interrupted")
    replay = await planner.plan(project["id"], "Change sorting", "interrupted")
    assert result["task_ids"] == replay["task_ids"] and len(Tasks(db).list()) == 1


async def test_execute_do_it_passes_history_to_assignment_and_persists_reference(worker_env, monkeypatch):
    settings, db, registry, project, _ = worker_env
    calls = planning_stub(monkeypatch, proposal(assignment("Change sorting after discussing newest first")))
    _, _, scheduler = controller(worker_env)
    chat = Chat(db, registry, FakeServer(settings))
    chat.action_handler, chat.receipt_provider = scheduler.manager_action, scheduler.receipts
    conversation = chat.conversation(project["id"])
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('prior',?,'assistant',?,'completed',NULL,1)",
            (
                conversation["id"],
                "Change Tasks ordering to newest first and test the result",
            ),
        )
    job = await chat.submit(project["id"], "do it", "execute")
    await chat.jobs[job["execution_id"]]
    goal = json.loads(calls[0][5]["goal"])
    assert goal["message"] == "do it"
    assert "newest first" in goal["conversation_history"][0]["content"]
    task = Tasks(db).list()[0]
    records = db.one("SELECT payload FROM events WHERE kind='chat.execution_records'")
    assert json.loads(records["payload"])["tasks"][0]["task_id"] == task["id"]
    assert task["status"] == "pending" and len(Tasks(db).list()) == 1
    await chat.close()


def test_old_task_and_plan_records_remain_readable(worker_env):
    settings, db, registry, project, _ = worker_env
    old = {"tasks": [assignment()]}
    result = Planner(db, registry, settings).apply(project["id"], "Legacy goal", old, "legacy")
    before = Tasks(db).get(result["task_ids"][0])
    reopened = Database(settings.db_path)
    assert Tasks(reopened).get(before["id"]) == before
    assert Planner(reopened, registry, settings).existing_result(result["plan_id"])["task_ids"] == [before["id"]]
    assert json.loads(reopened.one("SELECT proposal FROM plans")["proposal"]) == old


class DevelopmentWorker(FakeServer):
    async def request(self, method, params, timeout=30):
        if method == "turn/start":
            self.received_task = json.loads(params["input"][0]["text"])["task"]
            await self.tool_handlers[params["threadId"]](
                {
                    "tool": "manager_workspace",
                    "callId": "worker-test",
                    "arguments": {"operation": "test"},
                }
            )
        return await super().request(method, params, timeout)


@pytest.mark.host_runtime
async def test_development_tests_and_independent_verification_share_one_task(worker_env, monkeypatch):
    _, db, _, project, _ = worker_env
    planner, workers, scheduler = controller(worker_env, DevelopmentWorker)
    db.set_setting(
        f"verification_commands:{project['id']}",
        [
            [
                "python3",
                "-c",
                "from pathlib import Path; assert Path('README.md').read_text().startswith('# Original')",
            ]
        ],
    )
    planning_stub(monkeypatch, proposal(assignment("Inspect original README and test it")))
    result = await planner.plan(project["id"], "Inspect original README and test it", "inspect")
    task_id = result["task_ids"][0]
    db.set_setting("scheduler_enabled", True)
    await scheduler.tick()
    job_id = next(iter(workers.jobs))
    await asyncio.gather(*list(workers.jobs.values()))
    assert workers.latest_worker(task_id)["status"] == Tasks(db).get(task_id)["status"] == "verifying"
    assert not db.all("SELECT * FROM verification_results")
    assert db.one("SELECT * FROM operations WHERE execution_id=? AND action='run_tests'", (job_id,))
    await scheduler.tick()
    await asyncio.gather(*list(scheduler.reviews.values()))
    assert Tasks(db).get(task_id)["status"] == "completed"
    assert len(Tasks(db).list()) == len(db.all("SELECT * FROM plans")) == 1
    assert len(db.all("SELECT * FROM executions WHERE task_id=? AND worktree IS NOT NULL", (task_id,))) == 1
    assert db.one("SELECT * FROM verification_results WHERE task_id=? AND passed=1", (task_id,))
    assert workers.latest_worker(task_id)["id"] == job_id
    await scheduler.close()
    await workers.close()

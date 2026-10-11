import asyncio
import json
import subprocess

import pytest
from fake_codex import FakeServer
from test_workers import worker_env as worker_env

from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


class ReviewServer(FakeServer):
    async def request(self, method, params, timeout=30):
        if method != "turn/start":
            return {}
        thread = params["threadId"]
        await self.tool_handlers[thread](
            {"tool": "manager_workspace", "arguments": {"operation": "read", "path": "README.md"}}
        )
        output = json.dumps(
            {"verified": True, "reason": "Independent test passed and README content inspected", "files": ["README.md"]}
        )
        queue = self.queues[thread]

        async def emit():
            await asyncio.sleep(0.01)
            queue.put_nowait({"method": "item/agentMessage/delta", "params": {"threadId": thread, "delta": output}})
            queue.put_nowait(
                {
                    "method": "turn/completed",
                    "params": {"threadId": thread, "turn": {"id": "review-turn", "status": "completed"}},
                }
            )

        asyncio.create_task(emit())
        return {"turn": {"id": "review-turn"}}


def test_plan_atomic_idempotent_and_dependency_validation(worker_env):
    settings, db, registry, project, source = worker_env
    planner = Planner(db, registry, settings)
    proposal = {
        "decomposition_reason": "Explicit owner request for separate inspection and review deliverables",
        "tasks": [
            {
                "description": "Inspect",
                "acceptance": "Evidence collected",
                "verification": "Independent inspection",
                "priority": 80,
                "dependencies": [],
            },
            {
                "description": "Review",
                "acceptance": "Review accepted",
                "verification": "Review evidence",
                "priority": 70,
                "dependencies": [0],
            },
        ]
    }
    result = planner.apply(project["id"], "Inspect and review", proposal, "request-fixture")
    assert len(result["task_ids"]) == 2
    assert planner.apply(project["id"], "Duplicate", proposal, "request-fixture")["already_applied"]
    assert len(Tasks(db).list()) == 2
    assert db.one("SELECT depends_on FROM dependencies")["depends_on"] == result["task_ids"][0]
    proposal["tasks"][0]["dependencies"] = [1]
    with pytest.raises(ValueError, match="cyclic"):
        planner.apply(project["id"], "Bad plan", proposal, "bad-fixture")
    assert len(Tasks(db).list()) == 2


@pytest.mark.host_runtime
async def test_evidence_based_auto_completion(worker_env):
    settings, db, registry, project, source = worker_env
    db.set_setting(
        f"verification_commands:{project['id']}",
        [
            [
                "python3",
                "-c",
                "from pathlib import Path; assert Path('README.md').read_text().startswith('# Original'); print('independent check passed')",
            ]
        ],
    )
    task = Tasks(db).add(project["id"], "Inspect original README", "Confirm committed content")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    verifier = Verifier(db, registry, settings, ReviewServer)
    assert await verifier.verify(task["id"])
    assert Tasks(db).get(task["id"])["status"] == "completed"
    evidence = json.loads(db.one("SELECT evidence FROM verification_results")["evidence"])
    assert evidence["tests"][0]["exit_code"] == 0 and evidence["files"]["README.md"]
    assert db.one("SELECT id FROM checkpoints")
    assert (
        "Verified completion" in next(m for m in registry.memory(project["id"]) if m["name"] == "STATE.md")["content"]
    )


async def test_automatic_verification_and_freeze_persist(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Inspect", "Evidence")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    verifier = Verifier(db, registry, settings, ReviewServer)
    assert await verifier.verify(task["id"])
    assert Tasks(db).get(task["id"])["status"] == "completed"
    scheduler = Scheduler(db, registry, settings, workers, verifier, Planner(db, registry, settings))
    await scheduler.manager_action(
        project["id"], {"tool": "manager_action", "callId": "freeze", "arguments": {"action": "freeze_projects"}}
    )
    other = settings.project_roots[0] / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="paused"):
        registry.add(other)
    with pytest.raises(ValueError, match="cross-project"):
        await scheduler.manager_action(
            project["id"],
            {"tool": "manager_action", "callId": "cross", "arguments": {"action": "prioritize", "project_id": "other"}},
        )


async def test_scheduler_respects_portfolio_priority_and_completion_budget(worker_env):
    settings, db, registry, project, source = worker_env
    second_source = source.parent / "second"
    subprocess.run(["git", "clone", "-q", str(source), str(second_source)], check=True)
    second_project = registry.add(second_source)
    registry.update(project["id"], project["revision"], {"priority": 100})
    low_task = Tasks(db).add(project["id"], "High project priority", "Evidence", priority=1)
    Tasks(db).add(second_project["id"], "Lower project, high task priority", "Evidence", priority=99)
    workers = Workers(db, registry, settings, FakeServer)
    scheduler = Scheduler(
        db, registry, settings, workers, Verifier(db, registry, settings, ReviewServer), Planner(db, registry, settings)
    )
    db.set_setting("scheduler_enabled", True)
    db.set_setting("scheduler_batch", {"limit": 1, "task_ids": []})
    await scheduler.tick()
    assert len(workers.jobs) == 1
    assert Tasks(db).get(low_task["id"])["status"] == "running"
    await asyncio.gather(*list(workers.jobs.values()))
    assert db.setting("scheduler_batch")["task_ids"] == [low_task["id"]]
    await scheduler.close()
    await workers.close()

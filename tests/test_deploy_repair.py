"""A failed deploy check of a task's own merge goes back to that task as a repair round (shared 5-round counter)."""
import itertools
import json

import pytest
from test_core import PASS
from test_core_deploy import Flow, runtime  # noqa: F401
from test_deploy_script import deploy_script, git

from ai_manager.core import REPAIR_ROUNDS

COUNTER = itertools.count(1)


async def merged_task(runtime, monkeypatch):
    runtime.start()
    flow = Flow(runtime, monkeypatch)
    flow.lab.results = [PASS]
    task = await flow.run_task("add a note", lambda root: (root / "NOTE.md").write_text("note\n"))
    assert task["state"] == "done" and task["merge"]["state"] == "merged"
    return flow, task


def receipt(flow, task, **fields):
    """Write the deploy script's result file the way scripts/deploy.py does, then let the application report it."""
    request = deploy_script.read(flow.pipeline.root / "request.json") or {}
    (flow.pipeline.root / "request.json").unlink(missing_ok=True)
    value = {"id": fields.pop("id", "d" + str(next(COUNTER))), "status": "failed",
             "reason": "full backend suite failed: FAILED tests/test_x.py::test_y", "repairable": True,
             "task_id": task["id"], "source": request.get("source", "auto"), "project_id": task["project_id"],
             "conversation_id": None, "commit": "c", "previous": "p", **fields}
    deploy_script.atomic(flow.pipeline.root / "result.json", value)
    return flow.pipeline.report()


@pytest.mark.asyncio
async def test_failed_deploy_checks_requeue_the_task_with_the_failure_and_current_main(runtime, monkeypatch):
    flow, task = await merged_task(runtime, monkeypatch)
    main = git(runtime.repo, "rev-parse", "main")
    assert flow.core.repair_deploy(receipt(flow, task))
    again = flow.core.tasks.get(task["id"])
    assert again["state"] == "queued" and again["rounds"] == 1 and again["stage"] == "work"
    assert again["workspace_base"] == main
    last = again["history"][-1]
    assert not last["ok"] and "FAILED tests/test_x.py::test_y" in last["failures"][0]
    prompt = flow.core.prompt(again, flow.core.registry.get(again["project_id"]), again["workspace"], "repair")
    assert "FAILED tests/test_x.py::test_y" in json.dumps(prompt["repair"]) and prompt["repair"]["round"] == 1


@pytest.mark.asyncio
async def test_rolled_back_health_failure_requeues_and_the_counter_stops_at_five(runtime, monkeypatch):
    flow, task = await merged_task(runtime, monkeypatch)
    for number in range(1, REPAIR_ROUNDS + 1):
        assert flow.core.repair_deploy(receipt(flow, task, status="rolled_back", reason="health check failed: x"))
        again = flow.core.tasks.get(task["id"])
        assert again["state"] == "queued" and again["rounds"] == number
        flow.core.db.run("UPDATE tasks SET state='done' WHERE id=?", (task["id"],))  # the repair merged again
    assert flow.core.repair_deploy(receipt(flow, task))
    stopped = flow.core.tasks.get(task["id"])
    assert stopped["state"] == "failed" and stopped["rounds"] == REPAIR_ROUNDS
    assert "could not be fixed" in stopped["blocker"] and "5 repair rounds" in stopped["blocker"]
    notice = flow.db.one("SELECT delivery,error FROM executions WHERE task_id=? ORDER BY finished_at DESC", (task["id"],))
    assert notice["delivery"] == "pending" and notice["error"] == stopped["blocker"]


@pytest.mark.asyncio
async def test_each_deploy_receipt_repairs_once(runtime, monkeypatch):
    flow, task = await merged_task(runtime, monkeypatch)
    first = receipt(flow, task, id="same")
    assert flow.core.repair_deploy(first)
    flow.core.db.run("UPDATE tasks SET state='done' WHERE id=?", (task["id"],))
    assert not flow.core.repair_deploy(first)
    assert flow.core.tasks.get(task["id"])["rounds"] == 1


@pytest.mark.asyncio
async def test_infrastructure_failures_and_deploys_without_a_task_do_not_requeue(runtime, monkeypatch):
    flow, task = await merged_task(runtime, monkeypatch)
    for fields in ({"repairable": False, "reason": "not idle after waiting: {}"},
                   {"repairable": False, "status": "cancelled", "reason": "withdrawn"},
                   {"repairable": False, "status": "rolled_back", "reason": "restart failed"},
                   {"status": "success", "reason": "deployed"},
                   {"task_id": None}, {"task_id": "missing"}, {"source": "owner", "task_id": None}):
        assert not flow.core.repair_deploy(receipt(flow, task, **fields))
    again = flow.core.tasks.get(task["id"])
    assert again["state"] == "done" and again["rounds"] == 0

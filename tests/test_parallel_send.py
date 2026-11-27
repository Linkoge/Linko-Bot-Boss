"""Parallel send (Main only): an owner-flagged task may run beside its project's running task, at most MAX_PARALLEL at
once; merges stay one at a time, each re-tested on the current main, and a conflict goes back to its worker."""
import asyncio
from pathlib import Path

import pytest
from test_core import git, world  # noqa: F401
from test_instance_models import pair  # noqa: F401
from test_minimal_flow import NativeFixture, system  # noqa: F401
from test_recovery import Faults

from ai_manager.chat import PARALLEL, PARALLEL_NOTE
from ai_manager.core import MAX_PARALLEL, Core, Tasks
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline


@pytest.fixture
def lab(world):  # noqa: F811
    world.deployable(False)  # merges only: no deploy request holds the queue
    world.lab.release = asyncio.Event()  # every worker waits here until the test lets it go
    return world


def states(world, *tasks):
    return [world.tasks.get(t["id"])["state"] for t in tasks]


async def finish(world):
    world.lab.release.set()
    while world.core.jobs:
        await asyncio.gather(*list(world.core.jobs.values()))


@pytest.mark.asyncio
async def test_parallel_tasks_start_beside_the_running_one_up_to_the_cap(lab):
    pid = lab.project["id"]
    first = lab.tasks.add(pid, "first, normal")
    flagged = [lab.tasks.add(pid, f"parallel {n}", parallel=True) for n in range(MAX_PARALLEL)]
    assert all(t["parallel"] == 1 for t in flagged) and first["parallel"] == 0
    await lab.core.runner.tick()
    assert MAX_PARALLEL == 3
    assert states(lab, first, *flagged) == ["running", "running", "running", "queued"]
    await lab.core.runner.tick()  # the cap holds: never more than 3 running in one project
    assert len(lab.db.all("SELECT 1 FROM tasks WHERE state='running' AND project_id=?", (pid,))) == 3
    await finish(lab)
    await lab.core.runner.tick()
    await finish(lab)
    assert states(lab, first, *flagged) == ["done"] * 4


@pytest.mark.asyncio
async def test_default_stays_one_at_a_time_and_order_is_kept(lab):
    pid = lab.project["id"]
    first, normal, later = (lab.tasks.add(pid, "first"), lab.tasks.add(pid, "second, normal"),
                            lab.tasks.add(pid, "third, parallel", parallel=True))
    await lab.core.runner.tick()
    # The normal second task waits for the slot, and the parallel one behind it keeps its place in the queue.
    assert states(lab, first, normal, later) == ["running", "queued", "queued"]
    await finish(lab)


@pytest.mark.asyncio
async def test_an_instance_ignores_the_parallel_flag(lab):
    pid = lab.project["id"]
    first, flagged = lab.tasks.add(pid, "first"), lab.tasks.add(pid, "flagged", parallel=True)
    lab.settings.restricted = True
    try:
        await lab.core.runner.tick()
        assert states(lab, first, flagged) == ["running", "queued"]
        assert lab.tasks.claim(parallel=False) is None
    finally:
        lab.settings.restricted = False
    await finish(lab)


@pytest.mark.asyncio
async def test_parallel_merges_one_at_a_time_and_a_conflict_is_redone_on_main(lab):
    pid = lab.project["id"]

    async def work(root, prompt):
        path = Path(root) / "shared.txt"
        if "repair" in prompt:  # the conflict came back: redo the change on top of main
            assert prompt["repair"]["failed_checks"] == ["merge"]
            path.write_text("line one\nline two: first and parallel\nline three\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "resolve on top of main")
            return "Resolved."
        label = "parallel" if "parallel" in prompt["objective"] else "first"
        path.write_text(f"line one\nline two: {label}\nline three\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", label)
        return "Done."

    lab.lab.default = work
    first, flagged = lab.tasks.add(pid, "first edit"), lab.tasks.add(pid, "parallel edit", parallel=True)
    await lab.core.runner.tick()
    assert states(lab, first, flagged) == ["running", "running"]
    await finish(lab)
    done = [lab.tasks.get(t["id"]) for t in (first, flagged)]
    assert [t["state"] for t in done] == ["done", "done"]
    assert all(t["merge"]["state"] == "merged" and not t["merge"]["copies"] for t in done)
    assert sum(t["rounds"] for t in done) == 1  # exactly the later merge went back to its worker
    assert len(lab.db.all("SELECT 1 FROM events WHERE kind='task.merge_conflict'")) == 1
    assert (lab.app / "shared.txt").read_text() == "line one\nline two: first and parallel\nline three\n"
    assert not list(lab.app.glob("*AI version*"))  # no duplicate copies
    assert git(lab.app, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_the_flag_is_saved_shown_and_dispatched(system):  # noqa: F811
    db, registry, chat, settings = system
    chat.server = Faults(['hold']).factory(settings)
    project = registry.list()[0]["id"]
    receipt = await chat.submit(project, "Also do this", parallel=True)
    execution = receipt["execution_id"]
    assert db.setting(PARALLEL + execution) is True
    [message] = [m for m in chat.history(project)["messages"] if m["role"] == "user"]
    assert message["parallel"] is True and message["content"] == "Also do this"  # shown text has no manager note
    assert db.one("SELECT input FROM executions WHERE id=?", (execution,))["input"].endswith(PARALLEL_NOTE)
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    params = {"tool": "manager_action", "arguments": {"action": "delegate", "objective": "Parallel work"},
              "conversation_id": chat.conversation(project)["id"], "manager_execution_id": execution, "callId": "c1"}
    task = await scheduler.manager_action(project, params)
    assert task["parallel"] == 1 and Tasks(db).get(task["id"])["parallel"] == 1
    assert Tasks(db).page(project)["tasks"][0]["parallel"] == 1
    # A message sent without the flag delegates a normal task; a notice turn never flags one.
    plain = {**params, "arguments": {"action": "delegate", "objective": "Normal work"}, "manager_execution_id": "other"}
    assert (await scheduler.manager_action(project, plain))["parallel"] == 0
    for job in chat.jobs.values():  # the held fixture reply never finishes by itself
        job.cancel()
    await asyncio.gather(*list(chat.jobs.values()), return_exceptions=True)


@pytest.mark.asyncio
async def test_chat_refuses_the_flag_on_an_instance_and_outside_a_project(system):  # noqa: F811
    db, registry, chat, settings = system
    chat.server = Faults(['hold']).factory(settings)
    with pytest.raises(ValueError, match="Parallel send"):
        await chat.submit(None, "Global", parallel=True)
    chat.restricted = True
    with pytest.raises(ValueError, match="Parallel send"):
        await chat.submit(registry.list()[0]["id"], "Instance", parallel=True)
    assert not db.all("SELECT 1 FROM messages")


def test_instance_server_rejects_parallel_send(pair):  # noqa: F811
    owner, client, *_ = pair
    response = client.post('/api/chat', json={'content': 'Hi', 'parallel': True})
    assert response.status_code == 403
    assert not pair[3].state.db.all("SELECT 1 FROM messages")
    # Main accepts the field; outside a project chat it is refused plainly.
    assert owner.post('/api/chat', json={'content': 'Hi', 'parallel': True}).status_code == 409

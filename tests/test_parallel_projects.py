"""One queue per project: a project runs one task at a time, different projects run in parallel."""
import asyncio
import sqlite3

import pytest
from test_core import git, world  # noqa: F401

from ai_manager.core import Core


@pytest.fixture
def two(world):  # noqa: F811
    """The core's world plus a second Git project; neither is deployable, so no deploy request holds the queues."""
    world.deployable(False)
    other = world.settings.project_roots[0] / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / "shared.txt").write_text("hello\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "initial")
    world.other = world.registry.add(other)
    world.db.set_setting('deploy_none:' + world.other['id'], {'reason': 'fixture', 'digest': 'x'})  # no Create deploy setup task
    world.other_path = other
    world.lab.release = asyncio.Event()  # every worker waits here until the test lets it go
    return world


def states(world, *tasks):
    return [world.tasks.get(t["id"])["state"] for t in tasks]


async def settle(world):
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(world.lab.turns) >= len(world.core.jobs):
            break


async def finish(world, core=None):
    world.lab.release.set()
    core = core or world.core
    while core.jobs:
        await asyncio.gather(*list(core.jobs.values()))


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_tasks_of_different_projects_run_at_once_but_a_project_runs_one(two):
    a1, a2 = (two.tasks.add(two.project["id"], n) for n in ("a first", "a second"))
    b1 = two.tasks.add(two.other["id"], "b first")
    await two.core.runner.tick()
    await settle(two)
    assert states(two, a1, a2, b1) == ["running", "queued", "running"]
    assert len(two.core.jobs) == 2 and len(two.core.servers) == 2
    await two.core.runner.tick()  # nothing more may start while both projects are busy
    assert states(two, a1, a2, b1) == ["running", "queued", "running"]
    with pytest.raises(sqlite3.IntegrityError):  # the database itself refuses a second running task in a project
        two.db.run("UPDATE tasks SET state='running' WHERE id=?", (a2["id"],))
    two.lab.release.set()
    while two.core.jobs:
        await asyncio.gather(*list(two.core.jobs.values()))
    assert states(two, a1, a2, b1) == ["done", "queued", "done"]
    await two.core.runner.tick()  # the project's slot is free: its next task starts
    await finish(two)
    assert states(two, a1, a2, b1) == ["done", "done", "done"]
    assert [t["objective"] for t in two.lab.turns if t["objective"].startswith("a ")] == ["a first", "a second"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_held_tasks_are_skipped_in_every_project_queue(two):
    held, a2 = (two.tasks.add(two.project["id"], n) for n in ("held first", "a second"))
    b_held, b2 = (two.tasks.add(two.other["id"], n) for n in ("b held", "b second"))
    two.tasks.control(held["id"], "hold")
    two.tasks.control(b_held["id"], "hold")
    await two.core.runner.tick()
    await settle(two)
    assert states(two, held, a2, b_held, b2) == ["queued", "running", "queued", "running"]
    assert two.tasks.get(held["id"])["status"] == "held" and two.tasks.get(b_held["id"])["status"] == "held"
    await finish(two)
    await two.core.runner.tick()
    await finish(two)
    assert states(two, held, a2, b_held, b2) == ["queued", "done", "queued", "done"]


@pytest.mark.asyncio
async def test_restart_recovers_every_running_task_without_penalty(two):
    a, b = two.tasks.add(two.project["id"], "a survives"), two.tasks.add(two.other["id"], "b survives")
    await two.core.runner.tick()
    await settle(two)
    two.db.run("UPDATE tasks SET rounds=2 WHERE id=?", (a["id"],))
    threads = {t["id"]: two.tasks.get(t["id"])["native_thread"] for t in (a, b)}
    await two.core.close()  # the service stops with both workers mid-turn
    assert states(two, a, b) == ["running", "running"]
    two.lab.release.set()
    core = Core(two.db, two.registry, two.settings, two.pipeline, two.lab.factory, gate=two.lab.gate)
    await core.runner.tick()  # startup resumes all of them in the same tick
    assert len(core.jobs) == 2
    await finish(two, core)
    for task in (a, b):
        row = two.tasks.get(task["id"])
        assert row["state"] == "done" and row["requeues"] == 0 and row["native_thread"] == threads[task["id"]]
    assert two.tasks.get(a["id"])["rounds"] == 2
    assert all("continuation" in turn for turn in two.lab.turns[2:]) and len(two.lab.turns) == 4


@pytest.mark.asyncio
async def test_cancelling_one_running_task_leaves_the_other_project_untouched(two):
    a, b = two.tasks.add(two.project["id"], "a cancelled"), two.tasks.add(two.other["id"], "b carries on")
    await two.core.runner.tick()
    await settle(two)
    claimed_b = two.tasks.get(b["id"])["lease_owner"]
    cancelled = await two.core.control(a["id"], "cancel", None)
    assert cancelled["state"] == "failed" and "Cancelled by owner" in cancelled["blocker"]
    assert two.tasks.get(b["id"])["state"] == "running" and two.tasks.get(b["id"])["lease_owner"] == claimed_b
    assert list(two.core.servers) == [claimed_b] and list(two.core.jobs) == [claimed_b]
    assert not two.core.servers[claimed_b].terminated
    await finish(two)
    assert states(two, a, b) == ["failed", "done"]
    assert git(two.app, "branch", "--list", "linko/assignment-" + a["id"])  # the cancelled branch is kept


@pytest.mark.asyncio
async def test_hold_and_continue_in_one_project_do_not_touch_the_other(two):
    a1, a2 = (two.tasks.add(two.project["id"], n) for n in ("a first", "a second"))
    b1 = two.tasks.add(two.other["id"], "b first")
    await two.core.runner.tick()
    await settle(two)
    held = two.tasks.control(a2["id"], "hold")
    assert held["status"] == "held" and states(two, a1, b1) == ["running", "running"]
    await finish(two)
    await two.core.runner.tick()
    assert not two.core.jobs and states(two, a1, a2, b1) == ["done", "queued", "done"]
    two.tasks.control(a2["id"], "release")
    await two.core.runner.tick()
    await finish(two)
    assert states(two, a2) == ["done"]


@pytest.mark.asyncio
async def test_a_pending_deploy_of_one_project_holds_the_other_projects_merge_and_starts(two):
    """Both workers reach the merge together: the first merges and owns the single deploy request, the second waits."""
    two.pipeline.save_setup(two.project["id"], {'deploy_command': ['true'], 'backup_command': ['true'],
                                                'health_command': ['true'], 'rollback_command': ['true']})
    a, b = two.tasks.add(two.project["id"], "a merges"), two.tasks.add(two.other["id"], "b merges")
    a_next = two.tasks.add(two.project["id"], "a follows")
    await two.core.runner.tick()
    await settle(two)
    await finish(two)
    done_a, waiting_b = two.tasks.get(a["id"]), two.tasks.get(b["id"])
    first_done = [t for t in (done_a, waiting_b) if t["state"] == "done"]
    assert len(first_done) == 1 and first_done[0]["merge"]["deploy"] is True  # the deploy request was never lost
    waiter = waiting_b if first_done[0] is done_a else done_a
    assert waiter["state"] == "queued" and "a deploy is pending" in waiter["blocker"] and waiter["retry_at"]
    request = two.pipeline.status()["request"]
    assert request["project_id"] == first_done[0]["project_id"]
    await two.core.runner.tick()
    assert not two.core.jobs and two.tasks.get(a_next["id"])["state"] == "queued"  # nothing starts while it is pending
    (two.pipeline.root / "request.json").unlink()  # the deploy finished
    two.db.run("UPDATE tasks SET retry_at=NULL WHERE id=?", (waiter["id"],))
    await two.core.runner.tick()
    await finish(two)
    assert states(two, a, b) == ["done", "done"]
    assert git(two.other_path, "status", "--porcelain", "--untracked-files=no") == ""
    assert git(two.app, "status", "--porcelain", "--untracked-files=no") == ""

"""Owner-controlled auto mode (ai_manager/auto_mode.py): new tasks from result notices, within hardcoded limits."""
import time
from pathlib import Path

import pytest
from test_followups import REFUSED, World, counter

from ai_manager.auto_mode import DAILY_LIMIT, FAILURE_STREAK, AutoMode
from ai_manager.codex import MANAGER_INSTRUCTIONS


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


async def switch(world, enabled):
    return await world.act("set_auto_mode", f"owner-{next(counter)}", internal=False, enabled=enabled)


async def auto(world, reason=None, project=None, **arguments):
    task = world.owner_task(root=f"owner-{next(counter)}", project=project)
    manager = world.notice(task)
    arguments.setdefault("objective", "New work " + str(next(counter)))
    return await world.act("delegate", manager, project=project, followup_trigger="auto",
                           reason=reason or "Worker suggested improvement " + letters(next(counter)), **arguments)


def letters(n):
    """Causes ignore digits, so distinct default reasons are spelled in letters."""
    return "".join(chr(97 + int(d)) for d in str(n))


def started(world):
    return world.db.all("SELECT task_id,payload,created_at FROM events WHERE kind='task.auto_started' ORDER BY seq")


def reasons(n):
    return ["Improve area " + letters(i) for i in range(n)]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_off_by_default_and_only_an_owner_turn_switches_it(world):
    assert AutoMode(world.db).state()["enabled"] is False
    with pytest.raises(ValueError, match="auto mode is off"):
        await auto(world)
    manager = world.notice(world.owner_task())
    with pytest.raises(ValueError, match=REFUSED):  # a notice turn can never switch it on
        await world.act("set_auto_mode", manager, enabled=True)
    assert AutoMode(world.db).state()["enabled"] is False
    assert (await switch(world, True))["enabled"] is True
    with pytest.raises(ValueError, match=REFUSED):  # ...or off
        await world.act("set_auto_mode", world.notice(world.owner_task()), enabled=False)
    world.boot()  # stored once, survives a restart
    assert AutoMode(world.db).state()["enabled"] is True
    assert (await switch(world, False))["enabled"] is False
    with pytest.raises(ValueError, match="enabled"):
        await switch(world, "yes")


@pytest.mark.asyncio
async def test_with_auto_mode_on_a_notice_starts_a_new_normal_task(world):
    await switch(world, True)
    made = await auto(world, reason="Add an export button the worker noticed is missing")
    task = world.tasks.get(made["id"])
    assert task["status"] == "queued" and task["chain_root"] is None and task["chain_parent"]
    [event] = started(world)
    assert event["task_id"] == made["id"]
    assert any(m["content"].startswith("Auto task started: Add an export button") for m in world.db.all(
        "SELECT content FROM messages WHERE id LIKE 'auto-%'"))
    # It is an ordinary task: the owner controls it, and its own notice gets no bounded follow-up chain.
    with pytest.raises(ValueError, match="owner-typed request"):
        await world.follow(world.fail(made["id"]))
    with pytest.raises(ValueError, match="reason"):
        await auto(world, reason=" ")
    with pytest.raises(ValueError, match="no worker result"):  # an internal turn that delivered no result
        await world.act("delegate", "not-a-notice", objective="x", followup_trigger="auto", reason="Some new goal")
    with pytest.raises(ValueError, match="only starts new tasks"):
        manager = world.notice(world.owner_task())
        await world.act("continue", manager, task_id=made["id"], objective="more", followup_trigger="auto", reason="x y")
    paused = world.registry.get(world.other)
    world.registry.update(world.other, paused["revision"], {"status": "paused"})
    with pytest.raises(ValueError, match="paused"):
        await auto(world, project=world.other, reason="Something in a paused project")
    world.db.set_setting("github_tier:" + world.project, "offline")
    with pytest.raises(ValueError, match="offline"):
        await auto(world, reason="Something in an offline project")
    assert len(started(world)) == 1


@pytest.mark.asyncio
async def test_ten_per_rolling_day_across_projects(world):
    await switch(world, True)
    for i, reason in enumerate(reasons(DAILY_LIMIT)):
        # All Projects context: an auto task may go to any active project.
        task = world.owner_task(root=f"owner-{next(counter)}")
        manager = world.notice(task)
        target = world.project if i % 2 else world.other
        await world.scheduler.manager_action(None, {
            "tool": "manager_action", "internal": True, "callId": f"c{i}", "manager_execution_id": manager,
            "conversation_id": world.conversation, "arguments": {"action": "delegate", "project_id": target,
                                                                 "objective": "Work " + reason, "followup_trigger": "auto",
                                                                 "reason": reason}})
    assert len(started(world)) == DAILY_LIMIT
    assert {e["task_id"] for e in started(world)} <= {t["id"] for t in world.db.all("SELECT id FROM tasks")}
    with pytest.raises(ValueError, match=f"limit of {DAILY_LIMIT}"):
        await auto(world, reason="One more distinct thing")
    world.db.run("UPDATE events SET created_at=created_at-86401 WHERE kind='task.auto_started' AND seq=("
                 "SELECT min(seq) FROM events WHERE kind='task.auto_started')")
    await auto(world, reason="One more distinct thing")  # the oldest left the 24-hour window
    assert len(started(world)) == DAILY_LIMIT + 1


@pytest.mark.asyncio
async def test_never_two_auto_tasks_for_the_same_cause(world):
    await switch(world, True)
    await auto(world, reason="Flaky test tests/a.py::t 1234 fails")
    with pytest.raises(ValueError, match="same cause"):
        await auto(world, reason="flaky test  tests/a.py::t 98765 FAILS")
    world.db.run("UPDATE events SET created_at=created_at-3*86400 WHERE kind='task.auto_started'")
    with pytest.raises(ValueError, match="same cause"):  # never, not just within the day
        await auto(world, reason="Flaky test tests/a.py::t 5 fails")
    assert len(started(world)) == 1


@pytest.mark.asyncio
async def test_pauses_itself_after_three_failures_in_a_row_and_tells_the_owner_once(world):
    await switch(world, True)
    made = [await auto(world, reason=r) for r in reasons(FAILURE_STREAK)]
    world.fail(made[0]["id"])
    world.db.run("UPDATE tasks SET state='done',blocker='' WHERE id=?", (made[1]["id"],))
    world.fail(made[2]["id"])
    await world.scheduler.tick()
    assert AutoMode(world.db).state()["enabled"] is True  # a success broke the streak
    more = [await auto(world, reason=r) for r in reasons(FAILURE_STREAK + 3)[FAILURE_STREAK:]]
    for task in more:
        world.fail(task["id"])
    assert AutoMode(world.db).check_pause() is True
    state = AutoMode(world.db).state()
    assert state["enabled"] is False and state["paused"]
    assert AutoMode(world.db).check_pause() is False
    await world.scheduler.tick()
    notes = world.db.all("SELECT content FROM messages WHERE id LIKE 'auto-paused-%'")
    assert len(notes) == 1 and "paused itself" in notes[0]["content"] and "auto mode on" in notes[0]["content"]
    with pytest.raises(ValueError, match="auto mode is off"):
        await auto(world, reason="After the pause")
    await switch(world, True)  # the owner turns it on again: a fresh streak
    assert AutoMode(world.db).check_pause() is False and AutoMode(world.db).state()["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("action,arguments", [
    ("deploy", {}), ("deploy", {"task_id": "x"}), ("save_deploy_setup", {"setup": None}),
    ("emergency_repair", {"objective": "fix host"}), ("hold", {"task_id": "x"}), ("release", {"task_id": "x"}),
    ("cancel", {"task_id": "x"}), ("pause_project", {}), ("resume_project", {}),
    ("save_todo", {"title": "Ready", "details": "d", "kind": "ready"}), ("set_auto_mode", {"enabled": False}),
    ("delegate", {"objective": "Start the to-do", "todos": ["t"], "followup_trigger": "auto", "reason": "r"}),
    ("continue", {"task_id": "x", "followup_trigger": "auto", "reason": "r"}),
    ("delegate", {"objective": "No trigger at all"}),
])
async def test_forbidden_actions_are_still_rejected_in_auto_mode(world, action, arguments):
    await switch(world, True)
    task = world.owner_task()
    manager = world.notice(task)
    arguments = {**arguments, **({"task_id": task["id"]} if arguments.get("task_id") == "x" else {})}
    before = world.db.one("SELECT count(*) AS n FROM tasks")["n"]
    with pytest.raises(ValueError, match=REFUSED):
        await world.act(action, manager, **arguments)
    assert world.db.one("SELECT count(*) AS n FROM tasks")["n"] == before
    assert not world.pipeline.pending() and not list(world.settings.data_dir.glob("repair/requests/*"))
    assert world.tasks.get(task["id"])["status"] == "failed" and AutoMode(world.db).state()["enabled"] is True
    assert not started(world)


def owner_turn(world, text="Good morning"):
    execution = f"owner-turn-{next(counter)}"
    world.db.run("INSERT INTO executions(id,project_id,agent_id,status,started_at,conversation_id) "
                 "VALUES(?,?,'manager','running',?,?)", (execution, world.project, time.time(), world.conversation))
    world.db.run("INSERT INTO messages(id,conversation_id,role,content,status,created_at,execution_id) "
                 "VALUES(?,?,'user',?,'completed',?,?)", ("m-" + execution, world.conversation, text, time.time(), execution))
    conversation = world.db.one("SELECT * FROM conversations WHERE id=?", (world.conversation,))
    prompt, _ = world.chat.prompt(conversation, text, execution, None, False)
    return execution, prompt


@pytest.mark.asyncio
async def test_morning_summary_lists_auto_tasks_once(world):
    await switch(world, True)
    first = await auto(world, reason="Add the export button")
    second = await auto(world, reason="Speed up the slow search page")
    world.fail(first["id"], blocker="Checks failed after 5 repair rounds: boom")
    world.db.run("UPDATE tasks SET state='done',blocker='' WHERE id=?", (second["id"],))
    summary = await world.act("auto_summary", world.notice(world.owner_task()))  # readable from any turn
    assert [(t["task_id"], t["reason"]) for t in summary["tasks"]] == [
        (first["id"], "Add the export button"), (second["id"], "Speed up the slow search page")]
    assert summary["tasks"][0]["outcome"].startswith("failed: Checks failed") and summary["tasks"][1]["outcome"] == "done"
    execution, prompt = owner_turn(world)
    listed = prompt["auto_summary"]["tasks"]
    assert [t["reason"] for t in listed] == ["Add the export button", "Speed up the slow search page"]
    assert "one line per task" in prompt["auto_summary"]["instruction"]
    assert prompt["context"]["auto_mode"]["enabled"] is True
    conversation = world.db.one("SELECT * FROM conversations WHERE id=?", (world.conversation,))
    again, _ = world.chat.prompt(conversation, "Good morning", execution, None, False)  # same turn rebuilt
    assert again["auto_summary"]["tasks"] == listed
    _, later = owner_turn(world, "And now?")
    assert "auto_summary" not in later  # reported once
    third = await auto(world, reason="Tidy the settings page")
    _, running = owner_turn(world, "Status?")
    assert [t["task_id"] for t in running["auto_summary"]["tasks"]] == [third["id"]]  # unfinished: listed again later
    _, still = owner_turn(world, "Status again?")
    assert [t["task_id"] for t in still["auto_summary"]["tasks"]] == [third["id"]]
    notice_prompt, _ = world.chat.prompt(conversation, "x", world.notice(world.owner_task()), None, False)
    assert "auto_summary" not in notice_prompt


def test_prompt_rules_readme_and_header_describe_auto_mode():
    root = Path(__file__).parents[1]
    assert "set_auto_mode" in MANAGER_INSTRUCTIONS and "10 per 24 hours" in MANAGER_INSTRUCTIONS
    assert "Auto mode is the owner's one global" in (root / "RULES.md").read_text()
    assert "**Auto mode**" in (root / "README.md").read_text()
    assert 'id="auto-mode-label"' in (root / "ai_manager/static/index.html").read_text()
    assert "portfolio.auto_mode" in (root / "ai_manager/static/app.js").read_text()

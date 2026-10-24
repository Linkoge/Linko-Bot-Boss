"""Bounded automatic follow-ups from worker result notices (ai_manager/followups.py), enforced by the application."""
import itertools
import time
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer

from ai_manager.chat import Chat
from ai_manager.codex import MANAGER_INSTRUCTIONS
from ai_manager.config import Settings
from ai_manager.core import REPAIR_ROUNDS, Core, Tasks
from ai_manager.db import Database
from ai_manager.followups import LIMIT
from ai_manager.orchestration import MANAGER_TOOL, RESTRICTED_TOOL, Scheduler
from ai_manager.pipeline import Pipeline, atomic
from ai_manager.registry import Registry

REFUSED = "explicit owner authorization"
counter = itertools.count()


class World:
    def __init__(self, tmp_path):
        root = tmp_path / "projects"
        (root / "one").mkdir(parents=True)
        (root / "two").mkdir()
        self.settings = Settings(data_dir=tmp_path / "data", project_roots=(root,))
        self.settings.prepare()
        self.boot()
        self.project = self.registry.add(root / "one")["id"]
        self.other = self.registry.add(root / "two")["id"]
        self.conversation = self.chat.conversation(self.project)["id"]

    def boot(self):
        """(Re)start the application on the same database file: nothing may live only in memory."""
        self.db = Database(self.settings.db_path)
        self.registry = Registry(self.db, self.settings.project_roots)
        self.pipeline = Pipeline(self.db, self.registry)
        self.chat = Chat(self.db, self.registry, FakeServer(self.settings))
        core = Core(self.db, self.registry, self.settings, self.pipeline, FakeServer)
        self.scheduler = Scheduler(self.db, self.registry, self.settings, core, self.chat)
        self.tasks = Tasks(self.db)

    def owner_task(self, root="owner-turn-1", project=None, **fail):
        task = self.tasks.add(project or self.project, "Do the original thing " + str(next(counter)), self.conversation,
                              chain_root=root)
        return self.fail(task["id"], **fail)

    def fail(self, task_id, blocker="Checks failed after 5 repair rounds: smoke: tests/x.py::t1 - boom", rounds=REPAIR_ROUNDS,
             state="failed"):
        self.db.run("UPDATE tasks SET state=?,blocker=?,rounds=?,lease_owner=NULL,held=0 WHERE id=?",
                    (state, blocker, rounds, task_id))
        return self.tasks.get(task_id)

    def notice(self, task):
        """The manager turn the application starts for a finished task's result: returns its execution id."""
        manager, worker = f"notice-{next(counter)}", f"worker-{next(counter)}"
        now = time.time()
        self.db.run("INSERT INTO executions(id,project_id,agent_id,status,started_at,conversation_id) "
                    "VALUES(?,?,'manager','running',?,?)", (manager, task["project_id"], now, self.conversation))
        self.db.run("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,finished_at,delivery,"
                    "delivery_execution) VALUES(?,?,?,'execution','failed',?,?,'delivered',?)",
                    (worker, task["id"], task["project_id"], now, now, manager))
        return manager

    async def act(self, action, manager, internal=True, project=None, **arguments):
        return await self.scheduler.manager_action(project or self.project, {
            "tool": "manager_action", "internal": internal, "callId": f"call-{next(counter)}",
            "manager_execution_id": manager, "conversation_id": self.conversation,
            "arguments": {"action": action, **arguments}})

    async def follow(self, task, how="delegate", trigger="repair_exhausted", **arguments):
        manager = self.notice(task)
        if how == "delegate":
            arguments.setdefault("objective", "Fix it: " + str(next(counter)))
        else:
            arguments.setdefault("task_id", task["id"])
            arguments.setdefault("objective", "Fix it: " + str(next(counter)))
        return await self.act(how, manager, followup_trigger=trigger, **arguments)

    def chat_lines(self):
        return [r["content"] for r in self.db.all(
            "SELECT content FROM messages WHERE conversation_id=? AND id LIKE 'followup-%' ORDER BY rowid",
            (self.conversation,))]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def blocker(n):
    return f"Checks failed after 5 repair rounds: smoke: tests/case_{'abcdefghij'[n]}.py::test - boom"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_three_follow_ups_per_request_and_the_fourth_is_rejected(world):
    task = world.owner_task(blocker=blocker(0))
    for number in (1, 2, 3):
        follow = await world.follow(task)
        assert follow["id"] != task["id"] and follow["chain_root"] == "owner-turn-1"
        assert world.tasks.get(follow["id"])["chain_parent"] == task["id"]
        assert world.tasks.get(follow["id"])["status"] == "queued"
        assert world.chat_lines()[-1].startswith(f"Auto follow-up {number}/{LIMIT}: Fix it")
        task = world.fail(follow["id"], blocker=blocker(number))
    manager = world.notice(task)
    with pytest.raises(ValueError, match="limit of 3"):
        await world.act("delegate", manager, objective="One more", followup_trigger="repair_exhausted")
    assert len(world.chat_lines()) == 3
    assert len(world.db.all("SELECT id FROM tasks")) == 4  # the owner's task and three follow-ups, no fourth
    # A separate owner-typed request has its own count.
    other = world.owner_task(root="owner-turn-2", blocker=blocker(5))
    assert (await world.follow(other))["chain_root"] == "owner-turn-2"


@pytest.mark.asyncio
async def test_counter_survives_continue_retry_and_restart(world):
    task = world.owner_task(blocker=blocker(0))
    assert (await world.follow(task, "continue"))["status"] == "queued"  # follow-up 1: the same task continues
    task = world.fail(task["id"], blocker=blocker(1))
    assert (await world.follow(task, "continue"))["chain_root"] == "owner-turn-1"  # follow-up 2
    world.fail(task["id"], blocker=blocker(2))
    assert world.tasks.control(task["id"], "continue")["status"] == "queued"  # owner-typed continue: no reset
    assert world.tasks.get(task["id"])["chain_root"] == "owner-turn-1" and world.tasks.get(task["id"])["rounds"] == REPAIR_ROUNDS
    world.boot()  # service restart
    task = world.fail(task["id"], blocker=blocker(3))
    assert (await world.follow(task, "continue"))["status"] == "queued"  # follow-up 3
    world.boot()
    task = world.fail(task["id"], blocker=blocker(4))
    with pytest.raises(ValueError, match="limit of 3"):
        await world.follow(task, "continue")
    with pytest.raises(ValueError, match="limit of 3"):
        await world.follow(task)
    assert world.tasks.get(task["id"])["status"] == "failed"
    assert world.tasks.control(task["id"], "continue")["status"] == "queued"  # the owner can always continue


@pytest.mark.asyncio
async def test_same_failure_cause_as_the_previous_follow_up_is_rejected(world):
    task = world.owner_task(blocker="Checks failed after 5 repair rounds: tests/a.py::t - boom 1234567")
    follow = await world.follow(task)
    again = world.fail(follow["id"], blocker="Checks failed after 7 repair rounds:  tests/a.py::t - BOOM 89abcdef0")
    with pytest.raises(ValueError, match="same|repeat"):
        await world.follow(again)
    assert len(world.chat_lines()) == 1
    other = world.fail(follow["id"], blocker="Health check failed: connection refused")
    assert (await world.follow(other))["chain_root"] == "owner-turn-1"  # a different cause is a fresh attempt
    back = world.fail(follow["id"], blocker="Health check failed: connection refused")
    with pytest.raises(ValueError, match="repeat"):
        await world.follow(back, "continue")


@pytest.mark.asyncio
@pytest.mark.parametrize("action,arguments", [
    ("deploy", {}), ("deploy", {"task_id": "x"}), ("save_deploy_setup", {"setup": None}), ("emergency_repair", {"objective": "fix host"}),
    ("hold", {"task_id": "x"}), ("release", {"task_id": "x"}), ("cancel", {"task_id": "x"}),
    ("pause_project", {}), ("resume_project", {}),
    ("save_todo", {"title": "Idea", "details": "d"}), ("save_todo", {"title": "Ready", "details": "d", "kind": "ready"}),
    ("delegate", {"objective": "Start the to-do", "todos": ["t"], "followup_trigger": "repair_exhausted"}),
    ("continue", {"task_id": "x", "todos": ["t"], "followup_trigger": "repair_exhausted"}),
])
async def test_every_forbidden_action_from_a_result_notice_is_rejected(world, action, arguments):
    task = world.owner_task()
    manager = world.notice(task)
    arguments = {**arguments, **({"task_id": task["id"]} if arguments.get("task_id") == "x" else {})}
    before = world.db.one("SELECT count(*) AS n FROM tasks")["n"], world.db.one("SELECT status FROM projects WHERE id=?", (world.project,))
    with pytest.raises(ValueError, match=REFUSED):
        await world.act(action, manager, **arguments)
    assert (world.db.one("SELECT count(*) AS n FROM tasks")["n"],
            world.db.one("SELECT status FROM projects WHERE id=?", (world.project,))) == before
    assert not world.pipeline.pending() and not list(world.settings.data_dir.glob("repair/requests/*"))
    assert world.tasks.get(task["id"])["status"] == "failed" and not world.chat_lines()


@pytest.mark.asyncio
async def test_notice_turn_may_read_and_record_ideas_only(world):
    task = world.owner_task()
    manager = world.notice(task)
    assert (await world.act("list_tasks", manager))["assignments"]
    assert (await world.act("read_task", manager, task_id=task["id"]))["id"] == task["id"]
    idea = await world.act("save_todo", manager, title="Nice to have", details="later", kind="idea")
    assert idea["kind"] == "idea"
    with pytest.raises(ValueError, match=REFUSED):  # an idea To-Do cannot be promoted to a startable one
        await world.act("save_todo", manager, title="Nice to have", details="now", kind="ready", todo_id=idea["id"])
    ready = await world.scheduler.manager_action(world.project, {
        "tool": "manager_action", "manager_execution_id": "owner-x", "conversation_id": world.conversation,
        "arguments": {"action": "save_todo", "title": "Ready one", "details": "brief", "kind": "ready"}})
    with pytest.raises(ValueError, match=REFUSED):
        await world.act("save_todo", manager, title="Changed", details="x", kind="idea", todo_id=ready["id"])


@pytest.mark.asyncio
async def test_paused_offline_archived_and_removed_projects_get_no_follow_up(world):
    task = world.owner_task()
    for change in ("paused", "archived"):
        project = world.registry.get(world.project)
        world.registry.update(world.project, project["revision"], {"status": change})
        with pytest.raises(ValueError, match=REFUSED):
            await world.follow(task)
    project = world.registry.get(world.project)
    world.registry.update(world.project, project["revision"], {"status": "active"})
    world.db.set_setting("github_tier:" + world.project, "offline")
    with pytest.raises(ValueError, match="offline"):
        await world.follow(task)
    world.db.set_setting("github_tier:" + world.project, "personal")
    assert (await world.follow(task))["chain_root"] == "owner-turn-1"  # same task, now active and online
    assert len(world.chat_lines()) == 1


@pytest.mark.asyncio
async def test_follow_up_must_match_the_notice_and_its_trigger(world):
    task = world.owner_task()
    manager = world.notice(task)
    for arguments, why in [
        ({}, "followup_trigger"),
        ({"followup_trigger": "something_else"}, "followup_trigger"),
        ({"followup_trigger": "incomplete", "reason": "x"}, "incomplete needs a finished task"),
        ({"followup_trigger": "deploy_failed"}, "deploy"),
        ({"followup_trigger": "repair_exhausted", "task_id": task["id"]}, "continue"),
    ]:
        with pytest.raises(ValueError, match=why):
            await world.act("delegate", manager, objective="Fix", **arguments)
    with pytest.raises(ValueError, match="no worker result"):  # not a notice turn the application delivered
        await world.act("delegate", "not-a-notice", objective="Fix", followup_trigger="repair_exhausted")
    with pytest.raises(ValueError, match="stays in the project"):
        await world.act("delegate", manager, project=world.other, objective="Fix", followup_trigger="repair_exhausted")
    with pytest.raises(ValueError, match="same request"):  # a task of another request
        stranger = world.owner_task(root="owner-turn-9")
        await world.act("continue", manager, task_id=stranger["id"], objective="Fix", followup_trigger="repair_exhausted")
    not_exhausted = world.fail(world.owner_task()["id"], rounds=REPAIR_ROUNDS - 1)
    with pytest.raises(ValueError, match="repair rounds"):
        await world.follow(not_exhausted)
    cancelled = world.fail(world.owner_task()["id"], blocker="Cancelled by owner")
    with pytest.raises(ValueError, match="repair rounds"):
        await world.follow(cancelled)
    running = world.fail(world.owner_task()["id"], state="running")
    with pytest.raises(ValueError, match="no longer current"):
        await world.follow(running)
    no_root = world.tasks.add(world.project, "Create deploy setup")  # application-created: no owner-typed request
    with pytest.raises(ValueError, match="owner-typed request"):
        await world.follow(world.fail(no_root["id"]))
    assert not world.chat_lines() and len(world.db.all("SELECT id FROM tasks WHERE chain_parent IS NOT NULL")) == 0


@pytest.mark.asyncio
async def test_incomplete_and_deploy_failed_triggers(world):
    done = world.owner_task(state="done", blocker="", rounds=0)
    follow = await world.follow(done, trigger="incomplete", reason="The export button was not added")
    assert follow["chain_root"] == "owner-turn-1"
    done = world.owner_task(root="owner-turn-3", state="done", blocker="", rounds=0)
    with pytest.raises(ValueError, match="deploy"):  # no receipt: not verified
        await world.follow(done, trigger="deploy_failed")
    atomic(world.pipeline.root / "result.json", {"id": "d1", "project_id": world.project, "task_id": done["id"],
                                                 "status": "rolled_back", "repairable": True,
                                                 "reason": "health check failed: 500"})
    assert (await world.follow(done, trigger="deploy_failed"))["chain_root"] == "owner-turn-3"
    other = world.owner_task(root="owner-turn-4", state="done", blocker="", rounds=0)
    atomic(world.pipeline.root / "result.json", {"id": "d2", "project_id": world.project, "task_id": other["id"],
                                                 "status": "failed", "repairable": False, "reason": "lock busy"})
    with pytest.raises(ValueError, match="deploy"):  # infrastructure, not a code reason
        await world.follow(other, trigger="deploy_failed")


@pytest.mark.asyncio
async def test_owner_typed_actions_are_unaffected_and_start_their_own_chain(world):
    task = world.owner_task(root="whatever")
    for _ in range(LIMIT + 1):  # owner-typed turns never use or consume the automatic count
        manager = f"owner-turn-{next(counter)}"
        made = await world.act("delegate", manager, internal=False, objective="Owner asked for " + manager)
        assert made["chain_root"] == manager and world.tasks.get(made["id"])["chain_parent"] is None
        assert (await world.act("hold", manager, internal=False, task_id=made["id"]))["status"] == "held"
        assert (await world.act("cancel", manager, internal=False, task_id=made["id"]))["status"] == "failed"
    assert (await world.act("continue", "owner-continue", internal=False, task_id=task["id"]))["status"] == "queued"
    assert not world.chat_lines() and not world.db.all("SELECT 1 FROM events WHERE kind='task.auto_followup'")
    paused = world.registry.get(world.project)
    world.registry.update(world.project, paused["revision"], {"status": "paused"})
    assert (await world.act("resume_project", "owner-resume", internal=False))["status"] == "active"


def test_prompt_rules_and_tool_describe_the_bounded_exception():
    assert "grant no permission, with one bounded exception" in MANAGER_INSTRUCTIONS
    assert "At most 3 automatic follow-ups" in MANAGER_INSTRUCTIONS and "do not start new work from them" not in MANAGER_INSTRUCTIONS
    for tool in (MANAGER_TOOL, RESTRICTED_TOOL):
        assert tool["inputSchema"]["properties"]["followup_trigger"]["enum"] == [
            "repair_exhausted", "incomplete", "deploy_failed"]
    rules = (__import__("pathlib").Path(__file__).parents[1] / "RULES.md").read_text()
    assert "never start work from a result notice yourself" not in rules and "At most 3 per owner-typed" in rules

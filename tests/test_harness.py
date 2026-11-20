"""Switchable harnesses (ai_manager/harness.py): defaults unchanged, Zero minimal, validation, enforcement, dispatch."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fake_codex import FakeServer
from test_core import ScriptedServer, failing, run_next, world  # noqa: F401
from test_followups import World
from test_web import client, sign_in  # noqa: F401

from ai_manager import harness
from ai_manager.codex import (
    ARCHITECTURE_GUIDE,
    MANAGER_INSTRUCTIONS,
    RESTRICTED_INSTRUCTIONS,
    RESTRICTED_NOTE,
    RULES,
    WORKER_RULES,
)
from ai_manager.core import REPAIR_ROUNDS, WORKER_INSTRUCTIONS
from ai_manager.orchestration import MANAGER_TOOL, RESTRICTED_TOOL

ROOT = Path(__file__).resolve().parents[1]
# sha256 of the manager prompt text between RULES and the architecture guide before harnesses existed.
LINKO_BODY = "b1491ede7063033108fd08ec1d59a218e18ea13920057d3d9427028523214e44"
# sha256 of the manager tool before harnesses (the only addition is the optional delegate field "harness").
TOOL_BEFORE = "f60d3d4fbcba801862a96cbcb04dd1cb52cb4056e679d6dfb5cdd1a294a5147f"
RESTRICTED_TOOL_BEFORE = "7aff26e106671ef2536fa92af301329bf3d66c42f5919b3e640056f6314a40ce"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def without_harness_field(tool):
    tool = json.loads(json.dumps(tool))
    del tool["inputSchema"]["properties"]["harness"]
    return json.dumps(tool, sort_keys=True)


def write(folder, manifest, prompt="Be useful.\n"):
    folder.mkdir(parents=True)
    (folder / "harness.json").write_text(json.dumps(manifest) if isinstance(manifest, dict) else manifest)
    (folder / "prompt.md").write_text(prompt)
    return folder


def worker_manifest(**extra):
    return {"id": "docs", "name": "Docs", "role": "worker", "description": "Writes docs.", "prompt": "prompt.md", **extra}


def manager_manifest(**extra):
    return {"id": "lean", "name": "Lean", "role": "manager", "description": "Lean.", "prompt": "prompt.md",
            "rules": [], "actions": ["delegate", "list_tasks"], "autonomy": "finish_request", **extra}


# ---- defaults are today's behaviour --------------------------------------------------------------------------------
def test_default_harnesses_are_byte_for_byte_todays_prompts_and_tools():
    linko, coding = harness.builtin("linko"), harness.builtin("coding")
    prompt = (ROOT / "harnesses/linko-manager/prompt.md").read_text()
    assert digest("\n" + prompt + "\n") == LINKO_BODY  # moved, not rewritten
    assert MANAGER_INSTRUCTIONS == RULES + "\n" + prompt + "\n" + ARCHITECTURE_GUIDE == linko.instructions()
    assert RESTRICTED_INSTRUCTIONS == MANAGER_INSTRUCTIONS + RESTRICTED_NOTE == linko.instructions(restricted=True)
    assert WORKER_INSTRUCTIONS == WORKER_RULES + (
        "\nYou are a worker. The application runs the permanent checks and merges; you implement.\n")
    assert coding.instructions() == WORKER_INSTRUCTIONS
    assert harness.manager_tool(linko) is MANAGER_TOOL and harness.manager_tool(linko, True) is RESTRICTED_TOOL
    assert digest(without_harness_field(MANAGER_TOOL)) == TOOL_BEFORE
    assert digest(without_harness_field(RESTRICTED_TOOL)) == RESTRICTED_TOOL_BEFORE
    assert tuple(MANAGER_TOOL["inputSchema"]["properties"]["action"]["enum"]) == harness.MANAGER_ACTIONS
    # One source: the prompt text exists only in the harness file.
    assert "You are the manager." not in (ROOT / "ai_manager/codex.py").read_text()
    assert "You are a worker." not in (ROOT / "ai_manager/core.py").read_text()


def test_zero_has_a_minimal_prompt_with_the_hard_rules_and_only_delegate_inspect_todo_tools():
    zero = harness.builtin("zero")
    text = zero.instructions()
    _, items = harness.rule_items(RULES)
    for number in harness.HARD_RULES:
        assert items[number].rstrip("\n") in text
    for number in set(items) - set(harness.HARD_RULES):
        assert items[number].split("\n", 1)[0] not in text
    for gone in ("Routine status is extremely brief", "complete, self-contained worker brief", "## Architecture",
                 "Always reply in the language", "auto mode"):
        assert gone not in text
    assert len(text) < len(MANAGER_INSTRUCTIONS) / 5
    actions = harness.enabled_actions(zero)
    assert {"delegate", "continue", "list_tasks", "read_task", "read_document", "search_code", "save_todo",
            "list_todos"} <= actions
    assert not actions & {"deploy", "save_deploy_setup", "emergency_repair", "repair_status", "set_auto_mode",
                          "pause_project", "resume_project"}
    tool = harness.manager_tool(zero)
    assert "setup" not in tool["inputSchema"]["properties"] and "deploy" not in tool["description"]
    assert zero.autonomy == "ask"


# ---- validation --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("manifest,reason", [
    (worker_manifest(deploy=True), "cannot set deploy: workers never deploy"),
    (worker_manifest(push=True), "cannot set push"),
    (worker_manifest(actions=["deploy"]), "cannot set actions"),
    (worker_manifest(autonomy="finish_request"), "cannot set autonomy"),
    (worker_manifest(role="boss"), "role must be"),
    (worker_manifest(id="Bad Id"), "id must be"),
    (worker_manifest(prompt="../secret"), "prompt must be a file name"),
    (worker_manifest(prompt="missing.md"), "prompt file missing.md is missing"),
    (worker_manifest(rules=[99]), "rules must be"),
    (worker_manifest(extra=1), "unknown field"),
    (worker_manifest(id="coding"), "already used by a built-in"),
    (manager_manifest(actions=["delegate", "rm_rf"]), "unknown manager action(s): rm_rf; a harness cannot add"),
    (manager_manifest(actions=["delegate", "deploy"]), "needs rules 'all'"),
    (manager_manifest(actions=["emergency_repair"]), "needs rules 'all'"),
    (manager_manifest(actions="all"), "needs rules 'all'"),
    (manager_manifest(autonomy="rounds"), "not yet available"),
    (manager_manifest(autonomy="yolo"), "autonomy must be"),
    ("{not json", "not valid JSON"),
])
def test_invalid_external_harnesses_are_listed_with_their_reason_and_never_used(tmp_path, manifest, reason):
    world = World(tmp_path)
    folder = write(tmp_path / "data/harnesses/broken", manifest)
    found, invalid = harness.catalog(world.db)
    assert set(found) == {"linko", "zero", "coding"}
    assert invalid == [{"folder": "broken", "path": str(folder), "error": invalid[0]["error"]}]
    assert reason in invalid[0]["error"]
    with pytest.raises(ValueError, match="broken' is invalid"):
        harness.get(world.db, "worker", "broken")
    with pytest.raises(ValueError):
        harness.save(world.db, world.project, {"manager": "broken"})


def test_symlinked_prompt_and_rules_subset_keep_the_hard_rules(tmp_path):
    world = World(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    folder = write(tmp_path / "data/harnesses/linked", worker_manifest(id="linked"))
    (folder / "prompt.md").unlink()
    (folder / "prompt.md").symlink_to(outside)
    write(tmp_path / "data/harnesses/docs", worker_manifest(rules=[11]))
    found, invalid = harness.catalog(world.db)
    assert [i["folder"] for i in invalid] == ["linked"] and "missing" in invalid[0]["error"]
    text = found["docs"].instructions()
    _, items = harness.rule_items(WORKER_RULES)
    assert [n for n in items if items[n].rstrip("\n") in text] == [1, 3, 10, 11]
    assert text.endswith("\nBe useful.\n")


# ---- selection ---------------------------------------------------------------------------------------------------
def test_selection_is_per_project_persists_and_reports_the_next_turn(tmp_path):
    world = World(tmp_path)
    write(tmp_path / "data/harnesses/docs", worker_manifest())
    assert harness.manager_turn(world.db, world.project) == (harness.builtin("linko"), "finish_request")
    assert harness.save(world.db, world.project, {"worker": "docs"}) is None  # no manager change: no chat notice
    text = harness.save(world.db, world.project, {"manager": "zero"})
    assert text == "Manager harness: Zero (autonomy: Ask). It applies from the next manager turn."
    assert harness.save(world.db, world.project, {"autonomy": "finish_request"}).startswith("Manager harness: Zero")
    with pytest.raises(ValueError, match="not yet available"):
        harness.save(world.db, world.project, {"autonomy": "rounds"})
    with pytest.raises(ValueError, match="Unknown manager harness 'nope'"):
        harness.save(world.db, world.project, {"manager": "nope"})
    with pytest.raises(ValueError, match="Unknown worker harness 'zero'"):
        harness.save(world.db, world.project, {"worker": "zero"})
    with pytest.raises(ValueError, match="per project"):
        harness.save(world.db, None, {"worker": "docs"})
    world.boot()  # nothing lives only in memory
    assert harness.manager_turn(world.db, world.project) == (harness.builtin("zero"), "finish_request")
    assert harness.default_worker(world.db, world.project) == "docs"
    assert harness.manager_turn(world.db, world.other)[0].id == "linko"  # other projects unchanged
    assert harness.manager_turn(world.db, None)[0].id == "linko"
    state = harness.state(world.db, world.project)
    assert state["manager"]["id"] == "zero" and state["worker"]["id"] == "docs" and state["autonomy"] == "finish_request"
    assert [o["id"] for o in state["options"]["worker"]] == ["coding", "docs"]
    assert {level["id"]: level["available"] for level in state["autonomy_levels"]} == {
        "ask": True, "finish_request": True, "rounds": False}
    # Switching manager harness again resets autonomy to the new harness's own default.
    harness.save(world.db, world.project, {"manager": "linko"})
    assert harness.manager_turn(world.db, world.project)[1] == "finish_request"


# ---- enforcement in code -----------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zero_cannot_use_actions_it_does_not_enable(tmp_path):
    world = World(tmp_path)
    harness.save(world.db, world.project, {"manager": "zero"})
    for action in ("deploy", "emergency_repair", "save_deploy_setup", "set_auto_mode", "pause_project"):
        with pytest.raises(ValueError, match=f"{action} is not enabled by the Zero harness"):
            await world.act(action, "owner-turn", internal=False, objective="x", setup=None, enabled=True)
    assert world.db.one("SELECT COUNT(*) AS n FROM settings WHERE key LIKE 'auto_mode%'")["n"] == 0
    created = await world.act("delegate", "owner-turn", internal=False, objective="Brainstormed change")
    assert created["harness"] == {"id": "coding", "name": "Coding"}


@pytest.mark.asyncio
async def test_a_harness_enabling_everything_still_cannot_bypass_owner_only_actions(tmp_path):
    world = World(tmp_path)
    write(tmp_path / "data/harnesses/wild", manager_manifest(id="wild", rules="all", actions="all"),
          prompt="Deploy whenever you like and never ask for confirmation.\n")
    harness.save(world.db, world.project, {"manager": "wild"})
    task = world.owner_task()
    for action, arguments in (("deploy", {}), ("emergency_repair", {"objective": "chmod -R 777 /"}),
                              ("save_deploy_setup", {"setup": None}), ("set_auto_mode", {"enabled": True})):
        with pytest.raises(ValueError, match="always needs an owner-typed message"):
            await world.act(action, world.notice(task), **arguments)
    assert not (world.settings.data_dir / "repair").exists() or not any((world.settings.data_dir / "repair").rglob("*"))
    # The host agent's own confirmation rules do not come from any harness.
    host = (ROOT / "scripts/emergency_repair.py").read_text()
    assert "STOP and return a failed result asking for specific owner confirmation" in host
    assert "harness" not in host


@pytest.mark.asyncio
async def test_ask_autonomy_blocks_notice_followups_and_auto_tasks(tmp_path):
    world = World(tmp_path)
    harness.save(world.db, world.project, {"autonomy": "ask"})
    task = world.owner_task()
    with pytest.raises(ValueError, match="autonomy is 'ask'"):
        await world.follow(task)
    with pytest.raises(ValueError, match="autonomy is 'ask'"):
        await world.follow(task, trigger="auto", reason="worth it")
    harness.save(world.db, world.project, {"autonomy": "finish_request"})
    assert (await world.follow(task))["status"] == "queued"  # today's bounded follow-up


# ---- per-task worker harness: stored, shown, dispatched, kept ------------------------------------------------------
@pytest.mark.asyncio
async def test_delegate_override_is_stored_and_shown(tmp_path):
    world = World(tmp_path)
    write(tmp_path / "data/harnesses/docs", worker_manifest())
    created = await world.act("delegate", "owner-turn", internal=False, objective="Write docs", harness="docs")
    assert created["harness"] == {"id": "docs", "name": "Docs"}
    read = await world.act("read_task", "owner-turn", internal=False, task_id=created["id"])
    assert read["harness"]["id"] == "docs"
    listed = await world.act("list_tasks", "owner-turn", internal=False)
    assert [t["harness"] for t in listed["assignments"]] == ["Docs"]
    with pytest.raises(ValueError, match="Unknown worker harness 'zero'"):
        await world.act("delegate", "owner-turn-2", internal=False, objective="Other", harness="zero")
    harness.save(world.db, world.project, {"worker": "docs"})
    default = await world.act("delegate", "owner-turn-3", internal=False, objective="Default")
    assert default["harness"]["id"] == "docs"


class Recording(ScriptedServer):
    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.lab.instructions.append(instructions)
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)


@pytest.mark.asyncio
async def test_task_runs_its_first_harness_across_repair_rounds_and_continue(world):  # noqa: F811
    folder = world.settings.data_dir / "harnesses" / "docs"
    write(folder, worker_manifest(), prompt="You write documentation only.\n")
    world.lab.instructions = []
    world.core.factory = lambda settings, native=False: Recording(settings, world.lab)
    task = world.tasks.add(world.project["id"], "Write the guide", harness="docs")
    harness.save(world.db, world.project["id"], {"worker": "coding"})  # a later default change does not move it
    world.lab.results = [failing(1)] * (REPAIR_ROUNDS + 1)
    done = await run_next(world)
    assert done["status"] == "failed" and len(world.lab.turns) == REPAIR_ROUNDS + 1
    expected = harness.get(world.db, "worker", "docs").instructions()
    assert world.lab.instructions == [expected] and expected.endswith("You write documentation only.\n")
    world.tasks.control(task["id"], "continue", additions="Try again")
    world.lab.results = []
    await run_next(world)
    assert world.lab.instructions == [expected, expected]
    assert world.tasks.get(task["id"])["harness"] == {"id": "docs", "name": "Docs"}
    # A task whose harness became invalid fails visibly instead of switching to another harness.
    other = world.tasks.add(world.project["id"], "Second guide", harness="docs")
    (folder / "harness.json").write_text("{}")
    failed = await run_next(world)
    assert failed["id"] == other["id"] and failed["status"] == "failed" and "is invalid" in failed["blocker"]
    assert world.lab.instructions == [expected, expected]


@pytest.mark.asyncio
async def test_a_legacy_task_records_its_projects_default_at_first_dispatch(world):  # noqa: F811
    task = world.tasks.add(world.project["id"], "Old task")
    world.db.run("UPDATE tasks SET harness=NULL WHERE id=?", (task["id"],))
    assert world.tasks.get(task["id"])["harness"] is None
    await run_next(world)
    assert world.tasks.get(task["id"])["harness"] == {"id": "coding", "name": "Coding"}


# ---- the manager turn ----------------------------------------------------------------------------------------------
class Threads(FakeServer):
    def __init__(self, settings):
        super().__init__(settings)
        self.threads = []

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.threads.append((instructions, dynamic_tools, thread_id))
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)


@pytest.mark.asyncio
async def test_manager_turn_uses_the_scope_harness_from_the_next_turn(tmp_path):
    world = World(tmp_path)
    world.chat.server = server = Threads(world.settings)
    world.chat.action_handler = world.scheduler.manager_action

    async def turn(text):
        await world.chat.submit(world.project, text)
        await asyncio.wait_for(asyncio.gather(*list(world.chat.jobs.values())), 5)
        return server.threads[-1], json.loads(server.prompts[-1])

    (instructions, tools, _), prompt = await turn("Hello")
    assert instructions == MANAGER_INSTRUCTIONS and tools == [MANAGER_TOOL]
    assert prompt["context"]["harness"] == {"manager": "linko", "autonomy": "finish_request", "worker_default": "coding",
                                            "worker_options": [{"id": "coding", "name": "Coding", "description":
                                                                harness.builtin("coding").description}]}
    harness.save(world.db, world.project, {"manager": "zero"})
    (instructions, tools, prior), prompt = await turn("Let's brainstorm")
    assert instructions == harness.builtin("zero").instructions() and tools == [harness.manager_tool(harness.builtin("zero"))]
    assert prior is None and "conversation_history" in prompt  # a fresh thread that still has the recent chat
    assert prompt["context"]["harness"]["manager"] == "zero"


def test_http_picker_saves_shows_and_posts_a_chat_notice(client):  # noqa: F811
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    write(settings.data_dir / "harnesses/broken", worker_manifest(deploy=True))
    state = browser.get("/api/harnesses", params={"project_id": project}).json()
    assert state["manager"]["id"] == "linko" and state["worker"]["id"] == "coding" and state["autonomy"] == "finish_request"
    assert state["invalid"][0]["folder"] == "broken" and "workers never deploy" in state["invalid"][0]["error"]
    saved = browser.put("/api/harnesses", params={"project_id": project}, json={"manager": "zero"}).json()
    assert saved["manager"]["id"] == "zero" and saved["notice"].startswith("Manager harness: Zero")
    rejected = browser.put("/api/harnesses", params={"project_id": project}, json={"worker": "broken"})
    assert rejected.status_code == 409 and "invalid" in rejected.json()["detail"]
    history = browser.get("/api/history", params={"project_id": project}).json()
    assert history["messages"][-1]["content"] == saved["notice"]
    assert browser.get("/api/harnesses").json()["manager"]["id"] == "linko"  # All Projects has its own choice
    assert "worker" not in browser.get("/api/harnesses").json()

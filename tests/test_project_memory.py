import asyncio
import copy
import json
import time

import pytest
from fake_codex import FakeServer
from test_minimal_flow import NativeFixture, settled
from test_minimal_flow import system as system_fixture
from test_web import client as client_fixture
from test_web import sign_in

from ai_manager.chat import Chat
from ai_manager.codex import safe_config
from ai_manager.core import Core, Tasks
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline, atomic
from ai_manager.project_memory import MAXIMUM, MemoryReviews, ProjectMemory, encode

system = system_fixture
client = client_fixture


def ready(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    memory = ProjectMemory(db, registry)
    snapshot = memory.snapshot(project["id"])
    brief = {
        **snapshot["brief"],
        "purpose": "Manage durable projects",
        "goals": ["Persistent owner goals"],
        "constraints": ["Preserve unrelated data"],
        "state": ["Verified current feature"],
        "decisions": ["SQLite: simple durable storage"],
        "pending": ["Open reboot acceptance"],
    }
    return project["id"], memory, activate(memory, project["id"], brief)


def activate(memory, project, brief, reason="test fixture"):
    with memory.db.transaction() as conn:
        memory.activate(conn, project, memory.control(conn, project), brief, reason)
    return memory.snapshot(project)


def test_fresh_chat_brief_retains_goals_state_global_preference_without_bulk_notes(system):
    project, memory, before = ready(system)
    db, registry, chat, _ = system
    db.set_setting("global_instructions", "Routine owner report extremely brief")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO memory VALUES(?,?,?,?,?)",
            (project, "OWNER.md", "historical secret phrase " * 3000, 0, time.time()),
        )
    restored = Chat(db, registry, chat.server)
    context = restored.context(project)
    assert context["project_brief"] == before["brief"]
    assert context["project_brief"]["goals"] == ["Persistent owner goals"]
    assert context["instructions"] == "Routine owner report extremely brief"
    assert "owner_notes" not in context
    assert "historical secret phrase" not in encode(context)
    assert len(encode({k: v for k, v in context.items() if k != "instructions"}).encode()) <= 2000
    assert memory.snapshot(project)["active"] == before["active"]


@pytest.mark.asyncio
async def test_archive_excluded_from_fresh_prompt_and_explicitly_retrievable(system):
    project, _, before = ready(system)
    db, registry, chat, settings = system
    old = chat.conversation(project)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('old',?,'user','archived project evidence','completed',NULL,?)",
            (old["id"], time.time()),
        )
    chat.reset(project, old["id"], "old", True, archive=True)
    fresh = chat.conversation(project)
    prompt, _ = chat.prompt(fresh, "new question", "new", "new-thread", None)
    assert "archived project evidence" not in encode(prompt)
    assert prompt["context"]["project_brief"]["goals"] == before["brief"]["goals"]
    scheduler = Scheduler(db, registry, settings, Core(db, registry, settings, Pipeline(db, registry), NativeFixture), chat)

    async def read(action, **extra):
        return await scheduler.manager_action(
            project,
            {
                "tool": "manager_action",
                "conversation_id": fresh["id"],
                "arguments": {"action": action, **extra},
            },
        )

    listing = await read("list_history", query="archived")
    assert listing["conversations"][0]["id"] == old["id"]
    assert (await read("read_history", conversation_id=old["id"], message_id="old"))[
        "content"
    ] == "archived project evidence"
    assert (await read("read_memory", path=before["active"]))["content"]
    other = registry.list()[1]["id"]
    other_origin = chat.conversation(other)
    with pytest.raises(ValueError, match="Unknown conversation"):
        await read("read_history", conversation_id=other_origin["id"])


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_workers_receive_focused_acceptance_and_selected_context_only(system):
    project, memory, _ = ready(system)
    db, registry, chat, settings = system
    db.set_setting("global_instructions", "Routine manager replies ONLY short")
    servers = []

    def factory(settings, native=False):
        server = NativeFixture(settings, native=native)
        servers.append(server)
        return server

    workers = Core(db, registry, settings, Pipeline(db, registry), factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    origin = chat.conversation(project)["id"]
    task = await scheduler.manager_action(
        project,
        {
            "tool": "manager_action",
            "conversation_id": origin,
            "manager_execution_id": "focused-execution",
            "arguments": {
                "action": "delegate",
                "objective": "Fix selected feature",
                "acceptance": "Regression passes",
                "constraints": ["Preserve listener"],
                "dependencies": ["Existing API"],
                "selected_context": ["One relevant decision"],
            },
        },
    )
    workers.launch(Tasks(db).claim())
    await settled(workers)
    prompt = json.loads(servers[-1].prompts[-1])
    assert prompt["objective"] == "Fix selected feature"
    assert prompt["acceptance_criteria"] == "Regression passes"
    assert prompt["task_context"] == {
        "constraints": ["Preserve listener"],
        "dependencies": ["Existing API"],
        "selected_context": ["One relevant decision"],
    }
    assert "project_brief" not in prompt and "owner_notes" not in prompt and "global_instructions" not in prompt
    assert "Persistent owner goals" not in encode(prompt)
    assert Tasks(db).get(task["id"])["status"] == "done"


def test_candidate_diff_preservation_apply_rollback_and_ambiguous_keep(system):
    project, memory, old = ready(system)
    review = memory.begin(project)
    candidate = copy.deepcopy(old["brief"])
    candidate["state"] = ["New verified description"]
    candidate["goals"] = []
    result = memory.candidate(project, review, candidate, ["Feature absence is ambiguous"], ["file:app.py"])
    assert memory.snapshot(project)["active"] == old["active"]
    assert len(result["mismatches"]) == 3 and "Active" in result["diff"] and "Candidate" in result["diff"]
    with pytest.raises(ValueError, match="resolve"):
        memory.apply(project, review, old["revision"], [])
    applied = memory.apply(
        project,
        review,
        old["revision"],
        [
            {"action": "accept", "reason": "Description checked; preserve intended goal"},
            {"action": "keep", "reason": "Missing code is not intended deletion"},
            {"action": "accept", "reason": "Verified description supersedes prior descriptive fact"},
        ],
    )
    assert applied["brief"]["goals"] == old["brief"]["goals"]
    assert memory.get(project, review)["candidate"]["goals"] == []
    assert memory.get(project, old["active"])["brief"] == old["brief"]
    restored = memory.rollback(project, old["active"], applied["revision"])
    assert restored["brief"] == old["brief"]
    assert applied["active"] in restored["versions"]
    other = memory.registry.list()[1]["id"]
    with pytest.raises(ValueError, match="unavailable"):
        memory.rollback(other, old["active"], 0)


@pytest.mark.asyncio
async def test_full_review_scoped_tools_usage_failure_cancel_restart(system):
    project, memory, old = ready(system)
    _, registry, _, settings = system
    root = registry.allowed_path(registry.get(project)["path"])
    (root / "app.py").write_text("feature = True")
    (root / "secrets.json").write_text("private")
    (root / ".env").write_text("private")
    external = root.parent / "private.py"
    external.write_text("external")
    (root / "linked.py").symlink_to(external)

    class ReviewServer(FakeServer):
        mode = "candidate"

        async def request(self, method, params, timeout=30):
            if method != "turn/start":
                return {}
            handler = self.tool_handlers[params["threadId"]]
            await handler({"arguments": {"kind": "file", "path": "app.py"}})
            for path in ("../private.py", "linked.py", "secrets.json", ".env"):
                with pytest.raises(ValueError):
                    await handler({"arguments": {"kind": "file", "path": path}})
            if self.mode == "hang":
                return {"turn": {"id": "review-turn"}}
            brief = copy.deepcopy(old["brief"])
            brief["state"] = ["Code feature exists (file:app.py); verification pending."]
            result = {"brief": brief, "mismatches": ["No test evidence for feature"], "sources": ["file:app.py"]}
            queue = self.queues[params["threadId"]]
            queue.put_nowait(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "tokenUsage": {"total": {"inputTokens": 50}, "last": {"inputTokens": 50, "outputTokens": 10}}
                    },
                }
            )
            queue.put_nowait(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"delta": encode(result) if self.mode == "candidate" else "invalid"},
                }
            )
            queue.put_nowait(
                {"method": "turn/completed", "params": {"turn": {"id": "review-turn", "status": "completed"}}}
            )
            return {"turn": {"id": "review-turn"}}

    reviews = MemoryReviews(memory, settings, ReviewServer)
    with pytest.raises(ValueError, match="Explicitly"):
        reviews.start(project, False)
    ref = reviews.start(project, True)["review"]
    await asyncio.gather(*list(reviews.jobs.values()))
    result = memory.get(project, ref)
    assert result["status"] == "candidate" and result["usage"]["turn"]["inputTokens"] == 50
    assert "file:app.py" in result["sources"] and "project:durable_state" in result["sources"]
    assert memory.snapshot(project)["active"] == old["active"]
    ReviewServer.mode = "bad"
    ref = reviews.start(project, True)["review"]
    await asyncio.gather(*list(reviews.jobs.values()))
    assert memory.get(project, ref)["status"] == "failed"
    assert memory.snapshot(project)["active"] == old["active"]
    ReviewServer.mode = "hang"
    ref = reviews.start(project, True)["review"]
    await asyncio.sleep(0.02)
    await reviews.cancel(project, ref)
    assert memory.get(project, ref)["status"] == "cancelled"
    assert memory.snapshot(project)["active"] == old["active"]
    memory.begin(project)
    reviews.recover()
    assert not any(memory.get(project, ref)["status"] == "running" for ref in memory.snapshot(project)["reviews"])
    assert safe_config()["features.shell_tool"] is False


def test_review_http_auth_scope_apply_rollback(client):
    browser, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    assert browser.get(f"/api/projects/{project}/brief").status_code == 401
    assert browser.post(f"/api/projects/{project}/brief/review", json={"confirmed": True}).status_code == 401
    sign_in(browser, app, settings)
    old = browser.get(f"/api/projects/{project}/brief").json()
    assert browser.post(f"/api/projects/{project}/brief/review", json={"confirmed": False}).status_code == 409
    ref = app.state.memory.begin(project)
    brief = {**old["brief"], "state": ["Candidate feature"]}
    app.state.memory.candidate(project, ref, brief, [], ["file:app.py"])
    candidate = browser.get(f"/api/projects/{project}/brief").json()["review_records"][0]
    assert candidate["status"] == "candidate" and candidate["diff"]
    assert (
        browser.post(
            f"/api/projects/{project}/brief/apply", json={"review": ref, "revision": old["revision"]}
        ).status_code
        == 200
    )
    active = browser.get(f"/api/projects/{project}/brief").json()
    assert active["brief"]["state"] == ["Candidate feature"]
    assert (
        browser.post(
            f"/api/projects/{project}/brief/rollback", json={"version": old["active"], "revision": active["revision"]}
        ).status_code
        == 200
    )
    other = app.state.registry.add(settings.project_roots[0] / "two")["id"]
    assert browser.get(f"/api/projects/{other}/brief/version", params={"reference": old["active"]}).status_code == 409
    assert browser.post(f"/api/projects/{other}/brief/apply", json={"review": ref, "revision": 1}).status_code == 409
    assert app.state.db.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert not app.state.db.all("PRAGMA foreign_key_check")


def test_oversized_initial_fields_preserved_with_explicit_retrieval_notice(system):
    _, registry, chat, _ = system
    project = registry.list()[0]
    registry.update(
        project["id"],
        project["revision"],
        {"objective": "authoritative goal " * 2000, "instructions": "must preserve " * 2000},
    )
    memory = ProjectMemory(system[0], registry)
    snapshot = memory.snapshot(project["id"])
    assert "exceed brief budget" in snapshot["brief"]["pending"][0]
    assert "Retrieve project fields" in snapshot["brief"]["goals"][0]
    record = memory.get(project["id"], snapshot["active"])
    assert record["project_fields"]["objective"] == "authoritative goal " * 2000
    assert len(encode(chat.context(project["id"])).encode()) <= 2000
    assert not system[0].all("SELECT * FROM executions")


def test_budget_overflow_is_atomic_and_leaves_the_active_brief_and_evidence(system):
    project, memory, before = ready(system)
    records = memory.db.all("SELECT * FROM memory WHERE project_id=?", (project,))
    with pytest.raises(ValueError, match="ceiling"):
        activate(memory, project, {**before["brief"], "state": ["x" * MAXIMUM]})
    assert memory.snapshot(project) == before
    assert memory.db.all("SELECT * FROM memory WHERE project_id=?", (project,)) == records


def test_stale_candidate_cannot_overwrite_a_newer_version(system):
    project, memory, old = ready(system)
    review = memory.begin(project)
    memory.candidate(project, review, old["brief"], [], ["memory:base"])
    latest = activate(memory, project, {**old["brief"], "state": ["Newer fact"]})
    with pytest.raises(ValueError, match="stale"):
        memory.apply(project, review, latest["revision"], [])
    assert memory.snapshot(project)["active"] == latest["active"]


def test_long_deployment_result_is_summarised_in_the_manager_context(system):
    project, _, _ = ready(system)
    Pipeline(system[0], system[1]).save_setup(project, {
        'deploy_command': ['true'], 'backup_command': ['true'],
        'health_command': ['true'], 'rollback_command': ['true'],
    })
    atomic(
        system[0].path.parent / "deploy" / "result.json",
        {"id": "0123456789abcdef", "project_id": project, "status": "success", "reason": "historical bulk " * 10000},
    )
    chat = system[2]
    prompt, _ = chat.prompt(chat.conversation(project), "hello", "execution", "new-thread", None)
    context = {k: v for k, v in prompt["context"].items() if k != "instructions"}
    assert context["deployment"].startswith("success: historical bulk")
    assert len(context["deployment"]) <= 200  # The long result is summarized, never embedded.
    assert len(encode(context).encode()) <= 2000

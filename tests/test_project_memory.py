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
from ai_manager.deployment import Handoff, atomic
from ai_manager.orchestration import Scheduler
from ai_manager.project_memory import MAXIMUM, MemoryReviews, ProjectMemory, encode
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

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
    snapshot = memory.update(project["id"], snapshot["revision"], brief)
    return project["id"], memory, snapshot


def entry(i, deployment="not_requested"):
    return {
        "id": str(i),
        "date": "2026-10-12",
        "change": "Feature " + str(i),
        "effect": "Durable behavior",
        "verification": "Tested",
        "deployment": deployment,
        "report": "docs/report.md",
    }


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
    prompt, _ = chat.prompt(fresh, "new question", "auto", "new", "new-thread", None)
    assert "archived project evidence" not in encode(prompt)
    assert prompt["context"]["project_brief"]["goals"] == before["brief"]["goals"]
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings, NativeFixture), chat)

    async def read(action, **extra):
        return await scheduler.manager_action(
            project,
            {
                "tool": "manager_action",
                "conversation_id": fresh["id"],
                "mode": "conversation",
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


def test_fourth_delivery_archives_full_evidence_retains_effects_and_blockers(system):
    project, memory, snapshot = ready(system)
    original = memory.get(project, snapshot["active"])
    reports = []
    for i in range(4):
        snapshot = memory.delivery(
            project,
            snapshot["revision"],
            entry(i),
            ["Lasting effect " + str(i)],
            "full evidence " + str(i) + "x" * 30000,
        )
        reports.append(snapshot["brief"]["recent"][0]["report"])
    assert [e["id"] for e in snapshot["brief"]["recent"]] == ["3", "2", "1"]
    assert all("Lasting effect " + str(i) in snapshot["brief"]["state"] for i in range(4))
    assert "Open reboot acceptance" in snapshot["brief"]["pending"]
    assert len(memory.get(project, reports[0])["evidence"]) > 30000
    assert memory.get(project, reports[0])["external_report"] == "docs/report.md"
    assert memory.get(project, snapshot["versions"][0])["brief"]
    assert memory.get(project, snapshot["versions"][1]) == original


def test_related_fixes_use_one_recent_entry_and_preserve_both_reports(system):
    project, memory, snapshot = ready(system)
    first = memory.delivery(project, snapshot["revision"], entry("same"), ["Feature"], "first detailed evidence")
    ref = first["brief"]["recent"][0]["report"]
    second = memory.delivery(project, first["revision"], entry("same"), ["Fix"], "second detailed evidence")
    assert len(second["brief"]["recent"]) == 1
    assert memory.get(project, ref)["evidence"] == "first detailed evidence"
    assert "Feature" in second["brief"]["state"] and "Fix" in second["brief"]["state"]


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_deployment_pending_failed_truth_and_durable_success_gate(system, status):
    project, memory, snapshot = ready(system)
    snapshot = memory.delivery(
        project, snapshot["revision"], entry("release", status), ["Implemented, tested"], "evidence"
    )
    assert snapshot["brief"]["recent"][0]["deployment"] == status
    assert "Delivery release activation " + status in snapshot["brief"]["pending"]
    with pytest.raises(ValueError, match="durable succeeded"):
        memory.delivery(project, snapshot["revision"], entry("release", "deployed"), [], "claim only")
    handoff = Handoff(memory.db.path.parent)
    root = memory.registry.get(project)["path"]
    atomic(handoff.root / "config.json", {"repo": root})
    atomic(handoff.root / "results/receipt.json", {"phase": "failed"})
    with pytest.raises(ValueError, match="not established"):
        memory.delivery(project, snapshot["revision"], entry("release", "deployed"), [], "claim only", "receipt")
    atomic(
        handoff.root / "results/receipt.json", {"phase": "succeeded", "release_id": "release", "health": {"auth": True}}
    )
    applied = memory.delivery(
        project, snapshot["revision"], entry("release", "deployed"), [], "actual receipt", "receipt"
    )
    assert applied["brief"]["recent"][0]["deployment"] == "deployed"
    assert not any("Delivery release activation" in p for p in applied["brief"]["pending"])


def test_budget_overflow_atomic_preserves_requirements_and_evidence(system):
    project, memory, before = ready(system)
    records = memory.db.all("SELECT * FROM memory WHERE project_id=?", (project,))
    huge = {**before["brief"], "state": ["x" * MAXIMUM]}
    with pytest.raises(ValueError, match="ceiling"):
        memory.update(project, before["revision"], huge)
    with pytest.raises(ValueError, match="ceiling"):
        memory.delivery(project, before["revision"], entry("huge"), ["x" * MAXIMUM], "evidence")
    assert memory.snapshot(project) == before
    assert memory.db.all("SELECT * FROM memory WHERE project_id=?", (project,)) == records
    with pytest.raises(ValueError, match="retain"):
        memory.update(project, before["revision"], {**before["brief"], "goals": []})


def test_many_deliveries_bound_default_context_with_explicit_state_condensation(system):
    project, memory, snapshot = ready(system)
    chat = system[2]
    for i in range(30):
        # Routine current-state representation can condense effects; history stays immutable.
        brief = {**snapshot["brief"], "state": ["Verified feature set; detailed evolution in delivery reports."]}
        snapshot = memory.update(
            project,
            snapshot["revision"],
            brief,
            retained=[
                {"field": "state", "previous": value, "current": brief["state"][0], "reference": snapshot["active"]}
                for value in snapshot["brief"]["state"]
                if value != brief["state"][0]
            ],
        )
        snapshot = memory.delivery(
            project, snapshot["revision"], entry(i), ["Lasting effect " + str(i)], "full evidence " + str(i)
        )
        context = chat.context(project)
        assert len(encode({k: v for k, v in context.items() if k != "instructions"}).encode()) <= 2000
    assert len(snapshot["brief"]["recent"]) == 3
    assert len(memory.db.all("SELECT name FROM memory WHERE project_id=? AND name LIKE 'delivery:%'", (project,))) == 30
    assert snapshot["brief"]["goals"] == ["Persistent owner goals"]
    assert "Open reboot acceptance" in snapshot["brief"]["pending"]


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

    workers = Workers(db, registry, settings, factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    origin = chat.conversation(project)["id"]
    task = await scheduler.manager_action(
        project,
        {
            "tool": "manager_action",
            "conversation_id": origin,
            "mode": "auto",
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
    workers.launch(Tasks(db).claim(2))
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
    assert Tasks(db).get(task["id"])["status"] == "completed"


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


def test_stale_candidate_cannot_overwrite_new_delivery(system):
    project, memory, old = ready(system)
    review = memory.begin(project)
    memory.candidate(project, review, old["brief"], [], ["memory:base"])
    latest = memory.delivery(project, old["revision"], entry("new"), ["Current delivery"], "full evidence")
    with pytest.raises(ValueError, match="stale"):
        memory.apply(project, review, latest["revision"], [])
    assert memory.snapshot(project)["active"] == latest["active"]


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


def test_condensation_and_completed_pending_require_explicit_scoped_provenance(system):
    project, memory, before = ready(system)
    compact = {**before["brief"], "goals": ["Durable owner goals"], "pending": []}
    retained = [
        {
            "field": "goals",
            "previous": "Persistent owner goals",
            "current": "Durable owner goals",
            "reference": before["active"],
        }
    ]
    resolved = [
        {
            "pending": "Open reboot acceptance",
            "reason": "Acceptance completed; source contains receipt",
            "reference": before["active"],
        }
    ]
    with pytest.raises(ValueError, match="retain"):
        memory.update(project, before["revision"], compact)
    other = memory.registry.list()[1]["id"]
    other_ref = memory.snapshot(other)["active"]
    with pytest.raises(ValueError, match="scoped"):
        memory.update(
            project, before["revision"], compact, retained=[{**retained[0], "reference": other_ref}], resolved=resolved
        )
    updated = memory.update(project, before["revision"], compact, retained=retained, resolved=resolved)
    record = memory.get(project, updated["active"])
    assert record["provenance"] == {"retained": retained, "resolved": resolved}
    assert memory.get(project, before["active"])["brief"]["goals"] == ["Persistent owner goals"]
    assert updated["brief"]["goals"] == ["Durable owner goals"] and not updated["brief"]["pending"]


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


def test_archive_metadata_and_envelope_exclude_bulk_deployment_receipt(system):
    project, memory, before = ready(system)
    snapshot = before
    reports = []
    for i in range(4):
        snapshot = memory.delivery(project, snapshot["revision"], entry(i), [], str(i) + " detailed evidence")
        reports.append(snapshot["brief"]["recent"][0]["report"])
    assert reports[0] in memory.get(project, snapshot["active"])["delivery_archive"]
    handoff = Handoff(memory.db.path.parent)
    atomic(
        handoff.root / "active.json",
        {"phase": "succeeded", "release_id": "release-one", "health": "historical bulk " * 10000},
    )
    chat = system[2]
    prompt, _ = chat.prompt(chat.conversation(project), "hello", "auto", "execution", "new-thread", None)
    context = {k: v for k, v in prompt["context"].items() if k != "instructions"}
    assert context["deployment"] == {"phase": "succeeded", "release_id": "release-one"}
    assert "historical bulk" not in encode(context)
    assert len(encode(context).encode()) <= 2000


def test_exact_release_and_other_project_scoped_receipt(system):
    project, memory, snapshot = ready(system)
    receipt = "deployment:fixture"
    with memory.db.transaction() as conn:
        memory.put(
            conn,
            project,
            receipt,
            {
                "project_id": project,
                "status": "succeeded",
                "release_id": "old-release",
                "finished_at": time.time(),
                "verification": {"health": "passed"},
            },
        )
    with pytest.raises(ValueError, match="different release"):
        memory.delivery(
            project,
            snapshot["revision"],
            entry("new-release", "deployed"),
            [],
            "new feature evidence",
            "memory:" + receipt,
        )
    other = memory.registry.list()[1]["id"]
    other_snapshot = memory.snapshot(other)
    with pytest.raises(ValueError, match="Unknown project memory"):
        memory.delivery(
            other,
            other_snapshot["revision"],
            entry("old-release", "deployed"),
            [],
            "other evidence",
            "memory:" + receipt,
        )
    applied = memory.delivery(
        project,
        snapshot["revision"],
        entry("delivery-group", "deployed"),
        [],
        "precise existing deployment evidence",
        "memory:" + receipt,
        release_id="old-release",
    )
    assert applied["brief"]["recent"][0]["deployment"] == "deployed"

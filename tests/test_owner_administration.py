import json
import time

import pytest
from test_web import client as client
from test_web import sign_in
from test_workers import worker_env as worker_env

from ai_manager.administration import Administration
from ai_manager.db import Database
from ai_manager.policies import Policies
from ai_manager.tasks import Tasks
from ai_manager.verification_runtime import VerificationRuntime, tree_identity
from ai_manager.workspace import Workspace


def test_scoped_grants_receipts_revocation_and_no_replay(worker_env):
    settings, db, registry, project, _ = worker_env
    admin = Administration(db, registry, settings)
    admin.grant(
        "manager-scope",
        project["id"],
        ["discover_verification", "snapshot", "reconcile_task"],
        "Owner instruction",
        "local_owner",
    )
    request = {
        "id": "stable-request",
        "grant_id": "manager-scope",
        "action": "discover_verification",
        "project_id": project["id"],
    }
    receipt = admin.execute(request, "local_owner")
    assert receipt["status"] == "completed"
    assert admin.execute(request, "local_owner") == receipt
    assert len(db.all("SELECT * FROM events WHERE kind='owner_admin.completed'")) == 1
    assert Administration(Database(settings.db_path), registry, settings).execute(request, "local_owner") == receipt
    with pytest.raises(ValueError, match="different arguments"):
        admin.execute({**request, "action": "snapshot"}, "local_owner")
    admin.revoke("manager-scope", "local_owner")
    assert admin.execute({**request, "id": "after-revocation"}, "local_owner")["result"] == "rejected"
    assert db.one("SELECT * FROM events WHERE kind='owner_admin.rejected'")


def test_active_chat_authority_cannot_be_claimed_by_worker(worker_env):
    settings, db, registry, project, _ = worker_env
    admin = Administration(db, registry, settings)
    admin.grant("scope", project["id"], ["snapshot"], "Owner instruction", "local_owner")
    request = {"id": "request", "grant_id": "scope", "action": "snapshot", "project_id": project["id"]}
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,agent_id,status,started_at) VALUES('worker','project-worker','running',?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO executions(id,agent_id,status,started_at) VALUES('manager','manager','running',?)",
            (time.time(),),
        )
    with pytest.raises(ValueError, match="workers are excluded"):
        admin.execute(request, "authenticated_owner", "worker", project["id"])
    assert admin.execute(request, "authenticated_owner", "manager", project["id"])["status"] == "completed"
    with pytest.raises(ValueError, match="selected project"):
        admin.execute({**request, "project_id": None}, "authenticated_owner", "manager", project["id"])
    with pytest.raises(ValueError, match="Supported action"):
        admin.execute({**request, "action": "deploy"}, "local_owner")


def test_uncertain_admin_receipt_is_never_replayed(worker_env, monkeypatch):
    settings, db, registry, project, _ = worker_env
    admin = Administration(db, registry, settings)
    admin.grant("scope", project["id"], ["discover_verification"], "Owner instruction", "local_owner")
    request = {"id": "uncertain", "grant_id": "scope", "action": "discover_verification", "project_id": project["id"]}

    def crash(*args):
        raise KeyboardInterrupt("crash after persistent start")

    monkeypatch.setattr(admin, "apply", crash)
    with pytest.raises(KeyboardInterrupt):
        admin.execute(request, "local_owner")
    with pytest.raises(ValueError, match="uncertain"):
        admin.execute(request, "local_owner")


def test_obsolete_reconciliation_preserves_criteria_blocker_and_events(worker_env):
    settings, db, registry, project, _ = worker_env
    admin = Administration(db, registry, settings)
    admin.grant("scope", project["id"], ["reconcile_task"], "Owner instruction", "local_owner")
    task = Tasks(db).add(project["id"], "Obsolete diagnosis", "Original acceptance remains recorded")
    task = Tasks(db).transition(task["id"], "blocked", task["revision"], blocker="Original exact blocker")
    receipt = admin.execute(
        {
            "id": "obsolete",
            "grant_id": "scope",
            "action": "reconcile_task",
            "project_id": project["id"],
            "arguments": {
                "task_id": task["id"],
                "revision": task["revision"],
                "disposition": "obsolete",
                "evidence": "Superseded by independently tested change and owner instruction",
            },
        },
        "local_owner",
    )
    assert receipt["status"] == "completed"
    latest = Tasks(db).get(task["id"])
    assert (
        latest["status"] == "cancelled"
        and latest["blocker"] == "Original exact blocker"
        and latest["acceptance"] == task["acceptance"]
    )
    assert db.one("SELECT * FROM events WHERE kind='task.owner_reconciled'")


def test_authenticated_administration_routes_require_csrf_and_revocable_scope(client):
    browser, app, settings = client
    assert browser.get("/api/administration").status_code == 401
    sign_in(browser, app, settings)
    project = app.state.registry.add(settings.project_roots[0] / "one")
    body = {
        "id": "scope",
        "project_id": project["id"],
        "actions": ["snapshot"],
        "authorization": "Explicit fixture owner instruction",
    }
    assert browser.post("/api/administration/grants", json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert browser.post("/api/administration/grants", json=body).status_code == 200
    request = {"id": "route-action", "grant_id": "scope", "project_id": project["id"], "action": "snapshot"}
    assert browser.post("/api/administration/actions", json=request).json()["status"] == "completed"
    assert browser.delete("/api/administration/grants/scope").status_code == 200
    assert browser.post("/api/administration/actions", json={**request, "id": "revoked"}).json()["result"] == "rejected"


@pytest.mark.host_runtime
async def test_offline_runtime_copy_is_scoped_integrity_checked_and_readonly(worker_env):
    settings, db, registry, project, source = worker_env
    artifact = settings.data_dir / "verification-runtimes" / project["id"] / "fixture"
    artifact.mkdir(parents=True)
    (artifact / "runtime.txt").write_text("approved offline artifact")
    identity = tree_identity(artifact)
    db.set_setting(
        "verification_runtime:" + project["id"], {"path": str(artifact), "sha256": identity, "python": "fixture"}
    )
    workspace = Workspace(settings.data_dir, "runtime-fixture")
    workspace.prepare(source)
    (workspace.root / ".linko-runtime.json").write_text(json.dumps({"sha256": identity}))
    task = Tasks(db).add(project["id"], "Check runtime", "Bounded approved runtime")
    Policies(db).set(project["id"], "install_dependencies", "ALLOW")
    await VerificationRuntime(db, settings).provision(project["id"], workspace, task["id"], "fixture")
    assert tree_identity(workspace.root / ".linko-runtime") == identity
    result = await workspace.isolated_test(
        [
            "python3",
            "-c",
            "assert open('.linko-runtime/runtime.txt').read()=='approved offline artifact'; open('.linko-runtime/runtime.txt','w').write('tamper')",
        ],
        writable=True,
    )
    assert result["exit_code"] != 0 and "Read-only file system" in result["output"]
    (artifact / "runtime.txt").write_text("unapproved artifact")
    with pytest.raises(ValueError, match="artifact changed"):
        await VerificationRuntime(db, settings).provision(project["id"], workspace, task["id"], "fixture")


async def test_owner_chat_invokes_administration_and_appends_persistent_receipt(worker_env):
    from fake_codex import FakeServer

    from ai_manager.chat import Chat
    from ai_manager.orchestration import Planner, Scheduler, Verifier
    from ai_manager.workers import Workers

    settings, db, registry, project, source = worker_env
    Administration(db, registry, settings).grant(
        "chat-scope", project["id"], ["discover_verification"], "Owner instruction", "local_owner"
    )
    request = {
        "id": "chat-discovery",
        "grant_id": "chat-scope",
        "action": "discover_verification",
        "project_id": project["id"],
    }

    class AdministrationServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                await self.tool_handlers[params["threadId"]](
                    {
                        "tool": "manager_action",
                        "callId": "admin-tool",
                        "arguments": {"action": "owner_admin", "request": json.dumps(request)},
                    }
                )
            return await super().request(method, params, timeout)

    workers = Workers(db, registry, settings, FakeServer)
    scheduler = Scheduler(
        db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
    )
    chat = Chat(db, registry, AdministrationServer(settings))
    chat.action_handler, chat.receipt_provider = scheduler.manager_action, scheduler.receipts
    accepted = await chat.submit(
        project["id"], "Discover the manager verification commands under my persisted authorization", "auto"
    )
    await chat.jobs[accepted["execution_id"]]
    message = db.one("SELECT content FROM messages WHERE role='assistant'")["content"]
    assert "Persistent owner administration receipts" in message and "chat-discovery" in message
    assert "no executable task" not in message and not Tasks(db).list()
    assert db.one("SELECT * FROM events WHERE kind='owner_admin.completed'")["execution_id"] == accepted["execution_id"]
    await chat.close()

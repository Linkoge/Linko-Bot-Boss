import time

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager.auth import digest
from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.web import create_app


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("one", "two"):
        (root / name).mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    app.state.db.set_setting("scheduler_enabled", False)  # API fixtures launch work explicitly.
    with TestClient(app) as client:
        yield client, app, settings


def sign_in(client, app, settings):
    token = (settings.data_dir / "setup-token").read_text()
    response = client.post(
        "/api/setup", json={"username": "owner", "password": "a-long-private-password", "token": token}
    )
    assert response.status_code == 200
    response = client.post("/api/login", json={"username": "owner", "password": "a-long-private-password"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def test_auth_setup_csrf_logout_and_secure_headers(client):
    browser, app, settings = client
    assert browser.get("/api/portfolio").status_code == 401
    assert browser.get("/").status_code == 200
    assert "frame-ancestors 'none'" in browser.get("/").headers["content-security-policy"]
    assert (
        browser.post(
            "/api/setup",
            json={"username": "owner", "password": "a-long-private-password", "token": "incorrect-token-value-long"},
        ).status_code
        == 409
    )
    sign_in(browser, app, settings)
    assert not (settings.data_dir / "setup-token").exists()
    assert (
        browser.post(
            "/api/setup",
            json={"username": "attacker", "password": "a-long-private-password", "token": "invalid-token-value-long"},
        ).status_code
        == 409
    )
    assert browser.get("/api/portfolio").status_code == 200
    assert browser.post("/api/projects/discover", headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert browser.post("/api/projects/discover", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert browser.get("/api/portfolio", headers={"Host": "attacker.example"}).status_code == 400
    session_token = browser.cookies.get("ai_manager_session")
    stored = app.state.db.one("SELECT * FROM sessions")
    assert stored["token_hash"] == digest(session_token) and session_token != stored["token_hash"]
    assert app.state.db.one("SELECT password_hash FROM owner")["password_hash"].startswith("$argon2id$")
    assert browser.post("/api/logout").status_code == 200
    browser.cookies.set("ai_manager_session", session_token)
    assert browser.get("/api/portfolio").status_code == 401


def test_bruteforce_throttle_and_expiration(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert browser.get("/api/history").status_code == 401
    for _ in range(10):
        browser.post("/api/login", json={"username": "owner", "password": "wrong"})
    assert browser.post("/api/login", json={"username": "owner", "password": "wrong"}).status_code == 429


def test_project_crud_and_memory_conflict(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "one")}).json()
    assert (
        browser.patch(
            f"/api/projects/{project['id']}", json={"revision": 0, "objective": "Ship a usable product"}
        ).status_code
        == 200
    )
    assert (
        browser.patch(f"/api/projects/{project['id']}", json={"revision": 0, "objective": "Stale"}).status_code == 409
    )
    assert browser.get(f"/api/projects/{project['id']}").json()["project"]["objective"] == "Ship a usable product"
    path = f"/api/projects/{project['id']}/memory/STATE.md"
    assert browser.put(path, json={"revision": 0, "content": "# Verified state"}).status_code == 200
    assert browser.put(path, json={"revision": 0, "content": "# Stale"}).status_code == 409


def test_stream_history_context_and_model_routes(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    one = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "one")}).json()
    two = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "two")}).json()
    result = browser.post("/api/chat", json={"project_id": one["id"], "content": "Only project one"})
    assert result.status_code == 200
    execution = result.json()["execution_id"]
    # Job persists independently of the originating POST request / browser.
    time.sleep(0.1)
    stream = browser.get(f"/api/executions/{execution}/events").text
    assert "chat.delta" in stream and "Saved streamed answer" in stream and "stream.end" in stream
    history = browser.get("/api/history", params={"project_id": one["id"]}).json()
    assert history["messages"][-1]["status"] == "completed"
    assert browser.get("/api/history", params={"project_id": two["id"]}).json()["messages"] == []
    assert browser.get("/api/history").json()["messages"] == []
    assert '"name": "one"' in app.state.chat.server.prompts[-1]
    assert '"name": "two"' not in app.state.chat.server.prompts[-1]
    assert (
        len(browser.get("/api/history", params={"project_id": one["id"], "search": "streamed"}).json()["messages"]) == 1
    )
    last_seq = history["messages"][-1]["stream_cursor"]
    assert "chat.delta" not in browser.get(f"/api/executions/{execution}/events?after={last_seq}").text
    assert browser.put("/api/models/interactive", json={"model": "test-model", "effort": "high"}).status_code == 200
    assert browser.put("/api/models/interactive", json={"model": "nonexistent", "effort": "low"}).status_code == 409
    assert browser.put("/api/models/interactive", json={"model": "test-model", "effort": "ultra"}).status_code == 409
    assert browser.get("/api/models").json()["routes"]["interactive"]["effort"] == "high"
    assert browser.get("/api/audit").status_code == 200


def test_restart_retains_history_and_marks_uncertain(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", project_roots=(), secure_cookie=False)
    settings.prepare()
    db = Database(settings.db_path)
    chat = Chat(db, Registry(db, ()), FakeServer(settings))
    conversation = chat.conversation()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,agent_id,status,started_at) VALUES('uncertain','manager','running',?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO messages VALUES('response',?,'assistant','Partial answer','streaming','uncertain',?)",
            (conversation["id"], time.time()),
        )
    restarted = Chat(Database(settings.db_path), Registry(db, ()), FakeServer(settings))
    restarted.recover()
    assert restarted.history()["messages"][0]["content"] == "Partial answer"
    assert restarted.history()["messages"][0]["status"] == "interrupted"
    assert db.one("SELECT status FROM executions WHERE id='uncertain'")["status"] == "interrupted"


def test_policy_api_is_authenticated_and_audited(client):
    browser, app, settings = client
    assert browser.get("/api/policies").status_code == 401
    sign_in(browser, app, settings)
    assert {p["action"] for p in browser.get("/api/policies").json()} == set(
        __import__("ai_manager.policies", fromlist=["DEFAULTS"]).DEFAULTS
    )
    assert browser.put("/api/policies/edit_files", json={"mode": "DENY"}).status_code == 200
    assert next(p for p in browser.get("/api/policies").json() if p["action"] == "edit_files")["mode"] == "ALLOW"
    assert browser.put("/api/policies/paid_services", json={"mode": "ALLOW"}).status_code == 409
    assert (
        browser.put("/api/policies/paid_services", json={"mode": "ALLOW", "risk_accepted": True}).json()["available"]
        is False
    )
    assert browser.get("/api/approvals").json() == []
    assert browser.post("/api/approvals/unknown/decision", json={"approve": True}).status_code == 409
    assert any(e["kind"] == "policy.changed" for e in browser.get("/api/audit").json())


def test_rename_api_identity_recency_and_global_context(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    first = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "one")}).json()
    second = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "two")}).json()
    for project in (first, second):
        assert browser.get("/api/history", params={"project_id": project["id"]}).status_code == 200
        assert browser.get(f"/api/projects/{project['id']}").status_code == 200
    assert app.state.registry.get(first["id"])["last_used_at"] == 0
    result = browser.patch(f"/api/projects/{first['id']}", json={"revision": 0, "display_name": "Bot Manager"})
    assert result.status_code == 200
    renamed = result.json()
    assert renamed["display_name"] == "Bot Manager" and renamed["name"] == "one"
    for key in ("id", "path", "repository", "agent_id", "last_used_at"):
        assert renamed[key] == first[key]
    assert (
        browser.patch(f"/api/projects/{first['id']}", json={"revision": 0, "display_name": "Stale"}).status_code == 409
    )
    assert (
        browser.patch(f"/api/projects/{first['id']}", json={"revision": 1, "display_name": "bad\nname"}).status_code
        == 409
    )
    assert (
        browser.post("/api/chat", json={"project_id": second["id"], "content": "Project scoped message"}).status_code
        == 200
    )
    timestamp = app.state.registry.get(second["id"])["last_used_at"]
    assert timestamp > 0 and browser.get("/api/portfolio").json()["projects"][0]["id"] == second["id"]
    assert browser.post("/api/chat", json={"project_id": None, "content": "Global message"}).status_code == 200
    assert app.state.registry.get(second["id"])["last_used_at"] == timestamp
    assert len(browser.get("/api/portfolio").json()["projects"]) == 2
    assert browser.get("/api/history").json()["conversation"]["project_id"] is None
    restarted = Registry(Database(app.state.db.path), settings.project_roots)
    assert restarted.get(first["id"])["display_name"] == "Bot Manager"
    assert restarted.get(second["id"])["last_used_at"] == timestamp


def test_explicit_task_controls_and_verification_config_audit(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "one")}).json()
    task = browser.post(
        "/api/tasks",
        json={"project_id": project["id"], "description": "A harmless fixture", "acceptance": "No changes"},
    ).json()
    cancelled = browser.patch(
        f"/api/tasks/{task['id']}", json={"revision": task["revision"], "status": "cancelled"}
    ).json()
    assert cancelled["result"] == "accepted" and cancelled["status"] == "cancelled"
    before = app.state.db.one("SELECT * FROM tasks WHERE id=?", (task["id"],))
    rejected = browser.post(f"/api/tasks/{task['id']}/start")
    assert rejected.status_code == 409 and rejected.json()["result"] == "rejected"
    assert app.state.db.one("SELECT * FROM tasks WHERE id=?", (task["id"],)) == before
    configured = browser.put(
        f"/api/projects/{project['id']}/verification", json={"commands": [["python3", "-m", "unittest"]]}
    ).json()
    assert configured["result"] == "accepted"
    assert app.state.db.one("SELECT kind FROM events WHERE kind='verification.configured'")
    portfolio = browser.get("/api/portfolio").json()
    assert portfolio["scheduler"]["running"] and portfolio["tasks"][0]["readiness"]["verification_configured"]
    assert browser.get(f"/api/tasks/{task['id']}/execution-records").json()["task"]["status"] == "cancelled"
    blocked = browser.post("/api/chat", json={"content": "Do action", "mode": "execute"})
    assert blocked.status_code == 409 and "Select a project" in blocked.json()["detail"]


def test_chat_archive_clear_auth_confirmation_and_search(client):
    browser, app, settings = client
    assert browser.get("/api/chat-history").status_code == 401
    assert browser.post("/api/chat/archive", json={}).status_code == 401
    sign_in(browser, app, settings)
    from test_chat_history import seed

    old, last = seed(app.state.chat)
    body = {"conversation_id": old, "last_message_id": last, "title": "Saved decisions"}
    assert browser.post("/api/chat/archive", json=body).status_code == 409
    body["confirmed"] = True
    assert browser.post("/api/chat/archive", json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    archived = browser.post("/api/chat/archive", json=body)
    assert archived.status_code == 200 and archived.json()["result"] == "accepted"
    assert browser.get("/api/history").json()["messages"] == []
    assert browser.get("/api/chat-history", params={"search": "decisions"}).json()["conversations"][0]["id"] == old
    assert len(browser.get(f"/api/chat-history/{old}").json()["messages"]) == 2
    assert browser.get("/api/chat-history", params={"offset": -1}).status_code == 422
    assert browser.get("/api/chat-history", params={"search": "x" * 501}).status_code == 422
    current, last = seed(app.state.chat)
    body = {"conversation_id": current, "last_message_id": last, "confirmed": True}
    assert browser.post("/api/chat/clear", json=body).status_code == 200
    assert browser.get("/api/history").json()["messages"] == []
    assert browser.get(f"/api/chat-history/{old}").json()["messages"]
    assert {"chat.archived", "chat.cleared"} <= {r["kind"] for r in app.state.db.all("SELECT kind FROM events")}

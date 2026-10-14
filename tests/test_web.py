import time

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager.auth import digest
from ai_manager.config import Settings
from ai_manager.web import create_app


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("one", "two"):
        (root / name).mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url="http://localhost") as client:
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


@pytest.mark.smoke
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


def test_chat_modes_stale_origin_and_history_without_supervision_tables(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = app.state.registry.add(settings.project_roots[0] / "one")
    origin = app.state.chat.conversation(project["id"])["id"]
    response = browser.post(
        "/api/chat",
        json={"project_id": project["id"], "content": "Discuss", "conversation_id": "stale", "mode": "conversation"},
    )
    assert response.status_code == 409
    assert not app.state.db.all("SELECT * FROM messages")
    assert browser.post("/api/chat", json={"content": "Discuss", "mode": "planning"}).status_code == 422
    task = app.state.scheduler.tasks.add(project["id"], "History fixture", origin)
    response = browser.get(f"/api/tasks/{task['id']}/execution-records")
    assert response.status_code == 200
    assert response.json()["historical_verification"] == response.json()["historical_integration"] == []
    with app.state.db.transaction() as conn:
        conn.execute("CREATE TABLE verification_results(task_id TEXT, evidence TEXT)")
        conn.execute("INSERT INTO verification_results VALUES(?,?)", (task["id"], "Historical evidence"))
    response = browser.get(f"/api/tasks/{task['id']}/execution-records")
    assert response.json()["historical_verification"] == [{"task_id": task["id"], "evidence": "Historical evidence"}]


@pytest.mark.smoke
def test_owner_request_receipt_replay_and_changed_payload_conflict(client):
    browser, app, settings = client
    body = {"request_id": "http-owner-request-1", "content": "Discuss", "mode": "conversation"}
    assert browser.post("/api/chat", json=body).status_code == 401
    sign_in(browser, app, settings)
    original = browser.post("/api/chat", json=body)
    assert original.status_code == 200
    time.sleep(0.05)
    assert browser.post("/api/chat", json=body).json() == original.json()
    assert browser.post("/api/chat", json=body | {"content": "Changed"}).status_code == 409
    assert len(app.state.db.all("SELECT * FROM messages WHERE role='user'")) == 1
    assert len(app.state.db.all("SELECT * FROM executions")) == 1


def test_portfolio_project_scope_never_reassigns_tasks(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    one = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    two = app.state.registry.add(settings.project_roots[0] / 'two')['id']
    tasks = app.state.scheduler.tasks
    a, b = tasks.add(one, 'A'), tasks.add(two, 'B')
    # Keep this read-only scope fixture independent of scheduler dispatch.
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='waiting'")
    assert {t['id'] for t in browser.get('/api/portfolio').json()['tasks']} == {a['id'], b['id']}
    for project, expected in ((one, a['id']), (two, b['id']), (one, a['id'])):
        result = browser.get('/api/portfolio', params={'project_id': project}).json()
        assert result['scope_project_id'] == project
        assert [t['id'] for t in result['tasks']] == [expected]
        assert all(e['project_id'] == project for e in result['executions'])
    with app.state.db.transaction() as conn:
        app.state.db.event(conn, 'task.evidence', {'report': '/private/report.md'}, one, a['id'])
    evidence = browser.get(f"/api/tasks/{a['id']}/execution-records").json()['evidence']
    assert '/private/report.md' in evidence[0]['payload']
    assert tasks.get(a['id'])['project_id'] == one
    assert tasks.get(b['id'])['project_id'] == two
    assert browser.get('/api/portfolio?project_id=unknown').status_code == 409

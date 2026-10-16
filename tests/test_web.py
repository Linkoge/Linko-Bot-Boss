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


def test_chat_stale_origin_and_history_without_supervision_tables(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    project = app.state.registry.add(settings.project_roots[0] / "one")
    origin = app.state.chat.conversation(project["id"])["id"]
    response = browser.post(
        "/api/chat",
        json={"project_id": project["id"], "content": "Discuss", "conversation_id": "stale"},
    )
    assert response.status_code == 409
    assert not app.state.db.all("SELECT * FROM messages")
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
    body = {"request_id": "http-owner-request-1", "content": "Discuss"}
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
        conn.execute("UPDATE tasks SET held=1")
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


def test_emergency_owner_api_bypasses_task_queue_and_requires_auth_and_csrf(client, monkeypatch):
    from scripts.emergency_repair import atomic
    browser, app, settings = client
    assert browser.get('/api/repair').status_code == 401
    assert browser.post('/api/repair', json={}).status_code in {401, 403}
    sign_in(browser, app, settings)
    origin = app.state.chat.conversation(None)['id']
    atomic(settings.data_dir / 'repair/installed.json', {'repo': str(settings.data_dir)})
    def unavailable(*a, **kw):
        raise AssertionError('Normal queue must not be consulted')
    monkeypatch.setattr(app.state.scheduler.tasks, 'add', unavailable)
    body = {'request_id': 'emergency', 'instructions': 'Repair host', 'conversation_id': origin,
            'model': 'future-unlisted-model'}
    assert browser.post('/api/repair', json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    receipt = browser.post('/api/repair', json=body)
    assert receipt.status_code == 200 and receipt.json()['status'] == 'pending'
    assert browser.post('/api/repair', json=body).json()['id'] == receipt.json()['id']
    assert browser.get('/api/repair').json()[0]['conversation_id'] == origin

    from scripts.emergency_repair import Repair
    repair = Repair(settings.data_dir)
    monkeypatch.setattr(repair, 'healthy', lambda: True)
    repair.run(lambda *args: 'Application recovered')
    history = app.state.chat.history()
    assert history['messages'][-1]['content'].endswith('Application recovered')
    assert all(message['status'] == 'completed' for message in history['messages'])


def test_removed_settings_and_endpoints_are_gone(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    assert "auto_deploy" not in browser.get("/api/settings").json()
    assert browser.put("/api/settings/auto-deploy", json={"project_id": "p", "enabled": True}).status_code in {404, 405}
    assert browser.get("/api/models").status_code == 404
    html = browser.get("/").text
    assert "chat-mode" not in html and "auto-deploy-settings" not in html


@pytest.mark.smoke
def test_history_endpoint_latest_older_and_since_cursor(client):
    from test_chat_history import seed

    browser, app, settings = client
    sign_in(browser, app, settings)
    conversation, last = seed(app.state.chat, count=92)
    latest = browser.get('/api/history').json()
    assert len(latest['messages']) == 30 and latest['messages'][0]['id'] == f'{conversation}-62'
    older = browser.get('/api/history', params={'before': latest['messages'][0]['id']}).json()
    assert len(older['messages']) == 30 and older['messages'][-1]['id'] == f'{conversation}-61'
    delta = browser.get('/api/history', params={'since': f'{conversation}-89'}).json()
    assert [m['id'] for m in delta['messages']] == [f'{conversation}-90', last]
    assert delta['incremental'] and not delta['sync_has_more']
    empty = browser.get('/api/history', params={'since': last}).json()
    assert empty['messages'] == [] and empty['cursor'] == last

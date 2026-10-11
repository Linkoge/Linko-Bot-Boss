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

"""Sliding login sessions use disposable databases in both instance modes."""
import secrets
import time
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager import auth as auth_module
from ai_manager.config import Settings
from ai_manager.web import create_app


@pytest.fixture(params=[False, True], ids=["main", "restricted"])
def session_client(tmp_path, monkeypatch, request):
    monkeypatch.delenv("AI_MANAGER_SESSION_SECONDS", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(project,), restricted=request.param,
                        project_path=project if request.param else None, secure_cookie=False,
                        trusted_proxies=("testclient",), allowed_hosts=("localhost",), public_hosts=())
    app = create_app(settings, FakeServer(settings))
    clock = [time.time()]
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(time=lambda: clock[0]))
    with TestClient(app, base_url="https://localhost") as browser:
        owner = {"username": "owner", "password": secrets.token_urlsafe(16)}
        setup = browser.post("/api/setup", json={**owner, "token": (settings.data_dir / "setup-token").read_text()})
        assert setup.status_code == 200
        login = browser.post("/api/login", json=owner)
        assert login.status_code == 200
        browser.headers["X-CSRF-Token"] = login.json()["csrf"]
        yield browser, app, settings, login, clock


def stored(app):
    return app.state.db.one("SELECT * FROM sessions")


def assert_cookie(response, lifetime):
    cookie = response.headers["set-cookie"].lower()
    for flag in (f"max-age={lifetime}", "httponly", "samesite=strict", "secure", "path=/"):
        assert flag in cookie


def test_default_and_environment_session_lifetime(monkeypatch):
    monkeypatch.delenv("AI_MANAGER_SESSION_SECONDS", raising=False)
    monkeypatch.setenv("AI_MANAGER_RESTRICTED", "0")
    assert Settings().session_seconds == 30 * 24 * 3600
    monkeypatch.setenv("AI_MANAGER_SESSION_SECONDS", "7200")
    assert Settings().session_seconds == 7200
    assert Settings(session_seconds=120).session_seconds == 120


@pytest.mark.smoke
def test_login_and_hourly_sliding_cookie_and_database_expiry(session_client, monkeypatch):
    browser, app, settings, login, clock = session_client
    lifetime = 30 * 24 * 3600
    assert settings.session_seconds == lifetime
    assert_cookie(login, lifetime)
    original = stored(app)
    assert original["expires_at"] == pytest.approx(clock[0] + lifetime, abs=1, rel=0)
    token = browser.cookies.get("ai_manager_session")
    # Frequent requests neither write to SQLite nor send replacement cookies.
    def unexpected_write():
        raise AssertionError("A recent session must stay read-only")
    started = clock[0]
    with monkeypatch.context() as patch:
        patch.setattr(app.state.db, "transaction", unexpected_write)
        for elapsed in (1, 1800, 3599):
            clock[0] = started + elapsed
            response = browser.get("/api/auth-status")
            assert response.json()["authenticated"] is True
            assert "set-cookie" not in response.headers
    assert stored(app) == original
    clock[0] += 1
    response = browser.get("/api/portfolio")
    assert response.status_code == 200
    assert_cookie(response, lifetime)
    renewed = stored(app)
    assert renewed["expires_at"] == pytest.approx(clock[0] + lifetime, abs=1, rel=0)
    assert renewed["expires_at"] == pytest.approx(original["expires_at"] + 3600, abs=1, rel=0)
    assert renewed["csrf"] == original["csrf"]
    assert browser.cookies.get("ai_manager_session") == token
    assert "set-cookie" not in browser.get("/api/auth-status").headers
    clock[0] += 3600
    assert_cookie(browser.get("/api/auth-status"), lifetime)
    assert stored(app)["expires_at"] == pytest.approx(clock[0] + lifetime, abs=1, rel=0)


def test_idle_expiration_cannot_be_renewed(session_client):
    browser, app, settings, login, clock = session_client
    token = browser.cookies.get("ai_manager_session")
    original = stored(app)
    clock[0] = original["expires_at"]  # equality is already expired
    response = browser.get("/api/portfolio")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    response = browser.get("/api/auth-status")
    assert response.json()["authenticated"] is False
    assert "set-cookie" not in response.headers
    assert not app.state.auth.renew_session(token)
    assert stored(app) == original


@pytest.mark.parametrize("endpoint", ["/api/logout", "/api/sessions/revoke"])
def test_logout_and_revocation_remove_even_a_session_due_for_renewal(session_client, endpoint):
    browser, app, settings, login, clock = session_client
    token = browser.cookies.get("ai_manager_session")
    clock[0] += 3601
    response = browser.post(endpoint)
    assert response.status_code == 200
    if endpoint == "/api/logout":
        assert_cookie(response, 0)
    else:
        assert "set-cookie" not in response.headers
    assert stored(app) is None
    browser.cookies.set("ai_manager_session", token)
    assert browser.get("/api/portfolio").status_code == 401
    assert not app.state.auth.renew_session(token)


def test_rejected_csrf_and_origin_do_not_renew(session_client):
    browser, app, settings, login, clock = session_client
    original = stored(app)
    clock[0] += 3601
    for headers in ({"X-CSRF-Token": "bad"}, {"Origin": "https://other.example"}):
        response = browser.post("/api/logout", headers=headers)
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert stored(app) == original


def test_old_fixed_session_is_upgraded_and_checks_stay_read_only(session_client):
    browser, app, settings, login, clock = session_client
    token = browser.cookies.get("ai_manager_session")
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at=?", (clock[0] + 43200,))
    legacy = stored(app)
    assert app.state.auth.session(token)
    assert stored(app) == legacy
    response = browser.get("/api/auth-status")
    assert_cookie(response, settings.session_seconds)
    assert stored(app)["expires_at"] == pytest.approx(clock[0] + settings.session_seconds, abs=1, rel=0)


@pytest.mark.parametrize("competing_action", ["renew", "logout"])
def test_concurrent_request_rechecks_before_renewal(session_client, monkeypatch, competing_action):
    browser, app, settings, login, clock = session_client
    token = browser.cookies.get("ai_manager_session")
    clock[0] += 3601
    original_one = app.state.db.one

    def racing_read(sql, params=()):
        row = original_one(sql, params)
        if sql.startswith("SELECT 1 FROM sessions"):
            monkeypatch.setattr(app.state.db, "one", original_one)
            if competing_action == "renew":
                assert app.state.auth.renew_session(token)
            else:
                app.state.auth.logout(token)
        return row

    monkeypatch.setattr(app.state.db, "one", racing_read)
    assert not app.state.auth.renew_session(token)
    if competing_action == "renew":
        assert stored(app)["expires_at"] == pytest.approx(clock[0] + settings.session_seconds, abs=1, rel=0)
    else:
        assert stored(app) is None


def test_short_custom_lifetime_renews_before_expiry(session_client):
    browser, app, settings, login, clock = session_client
    settings.session_seconds = 120
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at=?", (clock[0] + 120,))
    clock[0] += 59
    assert "set-cookie" not in browser.get("/api/auth-status").headers
    clock[0] += 1
    assert_cookie(browser.get("/api/auth-status"), 120)
    assert stored(app)["expires_at"] == pytest.approx(clock[0] + 120, abs=1, rel=0)


def test_renewal_interval_is_per_session(session_client):
    browser, app, settings, login, clock = session_client
    first = browser.cookies.get("ai_manager_session")
    second = secrets.token_urlsafe(32)
    with app.state.db.transaction() as conn:
        conn.execute("INSERT INTO sessions VALUES(?,?,?)",
                     (auth_module.digest(second), secrets.token_urlsafe(32), clock[0] + settings.session_seconds))
    clock[0] += 3600
    assert app.state.auth.renew_session(first)
    assert not app.state.auth.renew_session(first)
    assert app.state.auth.renew_session(second)
    assert not app.state.auth.renew_session(second)

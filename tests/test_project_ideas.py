"""Global notebook CRUD, durable storage and isolation from executable work."""
import pytest
from fastapi.testclient import TestClient
from test_restricted import instance
from test_restricted import sign_in as restricted_sign_in
from test_web import sign_in

from ai_manager.db import Database
from ai_manager.project_ideas import ProjectIdeas

pytest_plugins = ["test_web"]
ENDPOINT = "/api/project-ideas"


@pytest.mark.smoke
def test_crud_titles_persistence_and_no_project_or_task_side_effects(client):
    browser, app, settings = client
    assert browser.get(ENDPOINT).status_code == 401
    assert browser.post(ENDPOINT, json={"details": "Later"}).status_code == 401
    sign_in(browser, app, settings)
    tables = ("projects", "tasks", "executions", "todos")
    before = {table: app.state.db.all(f"SELECT * FROM {table}") for table in tables}
    folders = sorted(settings.project_roots[0].iterdir())
    text = "\n  Build   a future garden planner  \n\nKeep all this text.\n  Including indentation.\n"
    response = browser.post(ENDPOINT, json={"title": "  ", "details": text})
    assert response.status_code == 200
    automatic = response.json()
    assert automatic["title"] == "Build a future garden planner"
    assert automatic["details"] == text and "project_id" not in automatic
    custom = browser.post(ENDPOINT, json={"title": "My custom title", "details": "Different first line"}).json()
    assert custom["title"] == "My custom title"
    assert browser.get(ENDPOINT).json()["entries"] == [automatic, custom]
    assert ProjectIdeas(Database(settings.db_path)).list() == [automatic, custom]
    endpoint = ENDPOINT + "/" + automatic["id"]
    updated = browser.patch(endpoint, json={"title": "Edited", "details": "New full text\n\nWith instructions"})
    assert updated.status_code == 200
    updated = updated.json()
    assert updated["title"] == "Edited" and updated["details"] == "New full text\n\nWith instructions"
    assert updated["created_at"] == automatic["created_at"]
    regenerated = browser.patch(endpoint, json={"details": "Updated automatic title"}).json()
    assert regenerated["title"] == "Updated automatic title"
    assert browser.post(endpoint + "/start").status_code == 404
    assert browser.post(ENDPOINT, json={"details": "Never launch", "project_id": "other"}).status_code == 422
    assert browser.delete(endpoint).json() == {"deleted": automatic["id"]}
    assert browser.delete(endpoint).status_code == 409
    assert browser.patch(endpoint, json={"details": "Missing"}).status_code == 409
    assert ProjectIdeas(Database(settings.db_path)).list() == [custom]
    assert browser.get(ENDPOINT).json()["entries"] == [custom]
    assert {table: app.state.db.all(f"SELECT * FROM {table}") for table in tables} == before
    assert sorted(settings.project_roots[0].iterdir()) == folders


def test_validation_csrf_and_origin_guard_every_mutation(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    for payload in ({"details": "  \n "}, {"details": "x" * 24001}, {"details": "ok", "title": "x" * 201},
                    {"details": "ok", "title": "two\nlines"}, {"details": "ok", "title": None}, {}):
        assert browser.post(ENDPOINT, json=payload).status_code in (409, 422)
    entry = browser.post(ENDPOINT, json={"details": "A" * 90 + "\nmore", "title": ""}).json()
    assert entry["title"] == "A" * 80
    for method, path, payload in (("POST", ENDPOINT, {"details": "New"}),
                                  ("PATCH", ENDPOINT + "/" + entry["id"], {"details": "Edit"}),
                                  ("DELETE", ENDPOINT + "/" + entry["id"], None)):
        assert browser.request(method, path, json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert browser.request(method, path, json=payload, headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.get(ENDPOINT).json()["entries"] == [entry]


def test_restricted_instance_rejects_global_ideas(tmp_path):
    settings, app = instance(tmp_path)
    # Existing global data must not leak even if the database was populated before restriction.
    entry = ProjectIdeas(app.state.db).save("Private global idea", "Future work")
    with TestClient(app, base_url="http://localhost") as browser:
        restricted_sign_in(browser, settings)
        for method, path, payload in (("GET", ENDPOINT, None), ("POST", ENDPOINT, {"details": "New"}),
                                      ("PATCH", ENDPOINT + "/" + entry["id"], {"details": "Edit"}),
                                      ("DELETE", ENDPOINT + "/" + entry["id"], None)):
            response = browser.request(method, path, json=payload)
            assert response.status_code == 403
            assert response.json()["detail"] == "Not available on a restricted instance"
    assert ProjectIdeas(app.state.db).list() == [entry]

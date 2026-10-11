import pytest
from test_web import sign_in

from ai_manager.db import Database
from ai_manager.todos import DEFERRED_ASSIGNMENT, Todos

pytest_plugins = ["test_web"]


def test_persistence_isolation_atomic_selection_archive_and_no_dispatch(client):
    browser, app, settings = client
    assert browser.get("/api/todos").status_code == 401
    sign_in(browser, app, settings)
    one, two = [app.state.registry.add(settings.project_roots[0] / name)["id"] for name in ("one", "two")]
    task = app.state.scheduler.tasks.add(one, "Execution assignment must survive To-Do deletion")
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='waiting' WHERE id=?", (task["id"],))
    recency = app.state.db.all("SELECT id,last_used_at FROM projects ORDER BY id")
    before_tasks = app.state.db.all("SELECT * FROM tasks")
    before_executions = app.state.db.all("SELECT * FROM executions")
    entries = []
    for project in (one, two):
        response = browser.post(
            "/api/todos", json={"project_id": project, "title": "Later", "details": "Never execute"}
        )
        assert response.status_code == 200
        entries.append(response.json())
    a, b = entries
    assert (
        browser.post(
            "/api/todos/archive", json={"selected": [{"id": task["id"], "revision": task["revision"]}]}
        ).status_code
        == 409
    )
    assert len(browser.get("/api/todos").json()["entries"]) == 2
    assert browser.get("/api/todos", params={"project_id": one}).json()["entries"] == [a]
    assert Todos(Database(settings.db_path)).list(two) == [b]
    assert (
        browser.patch(
            "/api/todos/" + a["id"], json={"project_id": two, "title": "Wrong project", "revision": a["revision"]}
        ).status_code
        == 409
    )
    updated = browser.patch(
        "/api/todos/" + a["id"],
        json={"project_id": one, "title": "Updated", "details": "Retained", "revision": a["revision"]},
    ).json()
    assert updated["revision"] == a["revision"] + 1
    import json

    audit = json.loads(app.state.db.one("SELECT payload FROM events WHERE kind='todo.updated'")["payload"])
    assert audit["previous"]["details"] == a["details"]
    selected = [{"id": b["id"], "revision": b["revision"]}, {"id": a["id"], "revision": a["revision"]}]
    assert browser.post("/api/todos/archive", json={"selected": selected}).status_code == 409
    assert len(Todos(app.state.db).list()) == 2  # failed batch rolls back earlier entries
    assert browser.post("/api/todos/archive", json={"project_id": one, "selected": [selected[0]]}).status_code == 409
    assert (
        browser.post(
            "/api/todos/archive", json={"selected": [{"id": updated["id"], "revision": updated["revision"]}]}
        ).status_code
        == 200
    )
    assert Todos(Database(settings.db_path)).list() == [b]
    archived = app.state.db.one("SELECT * FROM todos WHERE id=?", (a["id"],))
    assert archived["archived_at"] and archived["details"] == "Retained"
    assert app.state.db.all("SELECT id,last_used_at FROM projects ORDER BY id") == recency
    assert app.state.db.all("SELECT * FROM tasks") == before_tasks
    assert app.state.db.all("SELECT * FROM executions") == before_executions
    assert browser.post("/api/todos", json={"project_id": one, "title": "  "}).status_code == 409
    assert browser.post("/api/todos", json={"project_id": "unknown", "title": "Later"}).status_code == 409
    assert (
        browser.post(
            "/api/todos", headers={"X-CSRF-Token": "wrong"}, json={"project_id": one, "title": "Later"}
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_manager_future_recording_in_every_mode_without_task_or_queue(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    scheduler = app.state.scheduler
    for mode in ("conversation", "auto", "execute"):
        params = {
            "tool": "manager_action",
            "arguments": {"action": "save_todo", "title": "Later " + mode, "details": "Deferred"},
            "mode": mode,
        }
        entry = await scheduler.manager_action(project, params)
        assert entry["project_id"] == project
        params["arguments"].update(todo_id=entry["id"], revision=entry["revision"], details="Revised")
        await scheduler.manager_action(project, params)
    assert not app.state.db.all("SELECT * FROM tasks")
    assert not app.state.db.all("SELECT * FROM executions")
    assert scheduler.tasks.claim(2) is None
    listed = await scheduler.manager_action(None, {"tool": "manager_action", "arguments": {"action": "list_todos"}})
    assert len(listed["entries"]) == 3
    with pytest.raises(ValueError):
        await scheduler.manager_action(
            "different",
            {
                "tool": "manager_action",
                "arguments": {"action": "save_todo", "project_id": project, "title": "Cross project"},
            },
        )


def test_deferred_note_migrates_once_preserving_original_assignment_and_audit(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    db = app.state.db
    task = app.state.scheduler.tasks.add(
        project, "Original deferred objective; preserve architectural review and audit"
    )
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET id=?,lifecycle=? WHERE id=?", (DEFERRED_ASSIGNMENT, "completed", task["id"]))
        conn.execute("DELETE FROM schema_version WHERE version=11")
        db.event(conn, "task.evidence", {"report": "original-note-report"}, project, DEFERRED_ASSIGNMENT)
    before = db.one("SELECT * FROM tasks WHERE id=?", (DEFERRED_ASSIGNMENT,))
    original_events = db.all("SELECT * FROM events")
    migrated = Database(settings.db_path)
    entry = Todos(migrated).list(project)[0]
    assert entry["source_task_id"] == DEFERRED_ASSIGNMENT
    assert before["description"] in entry["details"]
    assert "retention/summarization" in entry["details"]
    assert migrated.one("SELECT * FROM tasks WHERE id=?", (DEFERRED_ASSIGNMENT,)) == before
    assert migrated.all("SELECT * FROM events")[: len(original_events)] == original_events
    assert len(Todos(Database(settings.db_path)).list(project)) == 1
    Todos(migrated).archive([{"id": entry["id"], "revision": entry["revision"]}])
    assert Todos(Database(settings.db_path)).list(project) == []  # no resurrection

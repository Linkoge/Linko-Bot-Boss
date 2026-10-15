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
        conn.execute("UPDATE tasks SET held=1 WHERE id=?", (task["id"],))
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
    assert scheduler.tasks.claim() is None
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
        conn.execute("UPDATE tasks SET id=?,state=? WHERE id=?", (DEFERRED_ASSIGNMENT, "done", task["id"]))
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


def test_handoff_atomic_replay_failure_and_retained_source(client, monkeypatch):
    import json
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    todos, tasks, db = Todos(app.state.db), app.state.scheduler.tasks, app.state.db
    a = todos.save(project, "Start explicitly", "Original details and source")
    b = todos.save(project, "Other idea", "Leave active")
    selected = [{"id": a["id"], "revision": a["revision"]}]
    with pytest.raises(ValueError, match="changed"):
        tasks.add(project, "Invalid start", todos=selected + [{"id": b["id"], "revision": 999}])
    assert not db.all("SELECT * FROM tasks")
    assert todos.list(project) == [a, b]
    original_event = db.event
    def fail(conn, kind, *args, **kwargs):
        if kind == "todo.consumed":
            raise RuntimeError("Handoff persistence failed")
        return original_event(conn, kind, *args, **kwargs)
    monkeypatch.setattr(db, "event", fail)
    with pytest.raises(RuntimeError):
        tasks.add(project, "Failed dispatch", todos=selected)
    assert not db.all("SELECT * FROM tasks")
    assert todos.list(project) == [a, b]
    monkeypatch.setattr(db, "event", original_event)
    task = tasks.add(project, "Accepted start", request_key="start-key", todos=selected)
    assert todos.list(project) == [b]
    assert tasks.add(project, "Accepted start", request_key="start-key", todos=selected)["id"] == task["id"]
    assert len(db.all("SELECT * FROM events WHERE kind='todo.consumed'")) == 1
    record = db.setting("todo_handoff:" + a["id"])
    assert record["task_id"] == task["id"] and record["source"] == a
    event = json.loads(db.one("SELECT payload FROM events WHERE kind='todo.consumed'")["payload"])
    assert event == record
    with pytest.raises(ValueError, match="already started"):
        tasks.add(project, "Duplicate task", request_key="another", todos=selected)
    assert len(db.all("SELECT * FROM tasks")) == 1
    assert Todos(Database(settings.db_path)).list(project) == [b]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_manager_delegate_and_bundled_continue_handoff(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    scheduler, db = app.state.scheduler, app.state.db
    todos = Todos(db)
    a, b = [todos.save(project, title) for title in ("First", "Bundle")]
    conversation = app.state.chat.conversation(project)["id"]
    params = {"tool": "manager_action", "manager_execution_id": "fixture-manager", "callId": "fixture",
              "conversation_id": conversation, "mode": "auto", "arguments": {
                  "action": "delegate", "objective": "Explicit schedule", "todos": [{"id": a["id"], "revision": 0}]}}
    task = await scheduler.manager_action(project, params)
    assert todos.list(project) == [b]
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET held=1 WHERE id=?", (task["id"],))
    params["arguments"] = {"action": "continue", "task_id": task["id"], "objective": "Bundle owner addition",
                           "todos": [{"id": b["id"], "revision": 0}]}
    params["manager_execution_id"] = "fixture-resume"
    continued = await scheduler.manager_action(project, params)
    assert continued["id"] == task["id"] and continued["status"] == "queued"
    assert not todos.list(project)
    assert (await scheduler.manager_action(project, params))["id"] == task["id"]
    assert len(db.all("SELECT * FROM tasks")) == 1
    c = todos.save(project, "Still deferred")
    params["arguments"]["todos"] = [{"id": c["id"], "revision": 0}]
    params["internal"] = True
    with pytest.raises(ValueError, match="explicit owner"):
        await scheduler.manager_action(project, params)
    assert todos.list(project) == [c]


@pytest.mark.asyncio
async def test_archived_handoff_read_is_scoped_and_failed_manager_dispatch_retains_todo(client, monkeypatch):
    _, app, settings = client
    one, two = [app.state.registry.add(settings.project_roots[0] / name)["id"] for name in ("one", "two")]
    todo = Todos(app.state.db).save(one, "Explicit start", "Original content")
    params = {"tool": "manager_action", "manager_execution_id": "failed-manager", "mode": "auto",
              "conversation_id": app.state.chat.conversation(one)["id"], "arguments": {
                  "action": "delegate", "objective": "Start", "todos": [{"id": todo["id"], "revision": 0}]}}
    async def fail():
        raise RuntimeError("Catalog unavailable")
    with monkeypatch.context() as m:
        m.setattr(app.state.chat.server, "start", fail)
        with pytest.raises(RuntimeError):
            await app.state.scheduler.manager_action(one, params)
    assert Todos(app.state.db).list(one) == [todo]
    assert not app.state.db.all("SELECT * FROM tasks")
    task = await app.state.scheduler.manager_action(one, params)
    read = {"tool": "manager_action", "arguments": {"action": "list_todos", "todo_id": todo["id"]}, "mode": "conversation"}
    receipt = await app.state.scheduler.manager_action(one, read)
    assert receipt["entry"]["archived_at"] and receipt["handoff"]["source"] == todo
    assert receipt["handoff"]["task_id"] == task["id"]
    with pytest.raises(ValueError, match="another project"):
        await app.state.scheduler.manager_action(two, read)


@pytest.mark.asyncio
async def test_ui_start_binds_source_before_dispatch_and_replays_after_consumption(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    todo = Todos(app.state.db).save(project, "Start me", "Source survives")
    selected = [{"id": todo["id"], "revision": 0}]
    conversation = app.state.chat.conversation(project)["id"]
    with pytest.raises(ValueError, match="Auto/Execute"):
        await app.state.chat.submit(project, "Start", conversation, mode="conversation", todos=selected)
    with pytest.raises(ValueError, match="changed"):
        await app.state.chat.submit(project, "Start", conversation, todos=[{"id": todo["id"], "revision": 9}])
    assert not app.state.db.all("SELECT * FROM executions")
    receipt = await app.state.chat.submit(project, "Explicit start", conversation, request_id="bound-start-000001", todos=selected)
    assert Todos(app.state.db).list(project) == [todo]
    assert not app.state.db.all("SELECT * FROM tasks")
    assert app.state.db.setting("execution_todo_sources:" + receipt["execution_id"]) == selected
    # The UI source is deterministic even if delegate omits the optional todos field.
    task = await app.state.scheduler.manager_action(project, {"tool": "manager_action", "mode": "auto",
        "conversation_id": conversation, "manager_execution_id": receipt["execution_id"],
        "arguments": {"action": "delegate", "objective": "Execute source objective"}})
    assert not Todos(app.state.db).list(project)
    assert app.state.db.setting("todo_handoff:" + todo["id"])["task_id"] == task["id"]
    assert await app.state.chat.submit(project, "Explicit start", conversation, request_id="bound-start-000001", todos=selected) == receipt
    assert len(app.state.db.all("SELECT * FROM tasks")) == 1

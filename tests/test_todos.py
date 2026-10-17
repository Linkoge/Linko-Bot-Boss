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
    assert browser.post("/api/todos/archive", json={"selected": [task["id"]]}).status_code == 409
    assert len(browser.get("/api/todos").json()["entries"]) == 2
    assert browser.get("/api/todos", params={"project_id": one}).json()["entries"] == [a]
    assert Todos(Database(settings.db_path)).list(two) == [b]
    assert (
        browser.patch(
            "/api/todos/" + a["id"], json={"project_id": two, "title": "Wrong project"}
        ).status_code
        == 409
    )
    updated = browser.patch(
        "/api/todos/" + a["id"],
        json={"project_id": one, "title": "Updated", "details": "Retained"},
    ).json()
    assert updated["title"] == "Updated" and "revision" not in updated
    import json

    audit = json.loads(app.state.db.one("SELECT payload FROM events WHERE kind='todo.updated'")["payload"])
    assert audit["previous"]["details"] == a["details"]
    selected = [b["id"], "missing"]
    assert browser.post("/api/todos/archive", json={"selected": selected}).status_code == 409
    assert len(Todos(app.state.db).list()) == 2  # failed batch rolls back earlier entries
    assert browser.post("/api/todos/archive", json={"project_id": one, "selected": [selected[0]]}).status_code == 409
    assert browser.post("/api/todos/archive", json={"selected": [updated["id"]]}).status_code == 200
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
async def test_manager_future_recording_without_task_or_queue(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    scheduler = app.state.scheduler
    for name in ("first", "second", "third"):
        params = {
            "tool": "manager_action",
            "arguments": {"action": "save_todo", "title": "Later " + name, "details": "Deferred"},
        }
        entry = await scheduler.manager_action(project, params)
        assert entry["project_id"] == project
        params["arguments"].update(todo_id=entry["id"], details="Revised")
        assert (await scheduler.manager_action(project, params))["details"] == "Revised"
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
    Todos(migrated).archive([entry["id"]])
    assert Todos(Database(settings.db_path)).list(project) == []  # no resurrection


def test_handoff_atomic_replay_failure_and_retained_source(client, monkeypatch):
    import json
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / "one")["id"]
    todos, tasks, db = Todos(app.state.db), app.state.scheduler.tasks, app.state.db
    a = todos.save(project, "Start explicitly", "Original details and source")
    b = todos.save(project, "Other idea", "Leave active")
    selected = [a["id"]]
    with pytest.raises(ValueError, match="not found"):
        tasks.add(project, "Invalid start", todos=selected + ["missing"])
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
              "conversation_id": conversation, "arguments": {
                  "action": "delegate", "objective": "Explicit schedule", "todos": [a["id"]]}}
    task = await scheduler.manager_action(project, params)
    assert todos.list(project) == [b]
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET held=1 WHERE id=?", (task["id"],))
    params["arguments"] = {"action": "continue", "task_id": task["id"], "objective": "Bundle owner addition",
                           "todos": [b["id"]]}
    params["manager_execution_id"] = "fixture-resume"
    continued = await scheduler.manager_action(project, params)
    assert continued["id"] == task["id"] and continued["status"] == "queued"
    assert not todos.list(project)
    assert (await scheduler.manager_action(project, params))["id"] == task["id"]
    assert len(db.all("SELECT * FROM tasks")) == 1
    c = todos.save(project, "Still deferred")
    params["arguments"]["todos"] = [c["id"]]
    params["internal"] = True
    with pytest.raises(ValueError, match="explicit owner"):
        await scheduler.manager_action(project, params)
    assert todos.list(project) == [c]


@pytest.mark.asyncio
async def test_archived_handoff_read_is_scoped_and_failed_manager_dispatch_retains_todo(client, monkeypatch):
    _, app, settings = client
    one, two = [app.state.registry.add(settings.project_roots[0] / name)["id"] for name in ("one", "two")]
    todo = Todos(app.state.db).save(one, "Explicit start", "Original content")
    params = {"tool": "manager_action", "manager_execution_id": "failed-manager",
              "conversation_id": app.state.chat.conversation(one)["id"], "arguments": {
                  "action": "delegate", "objective": "Start", "todos": [todo["id"]]}}
    async def fail():
        raise RuntimeError("Catalog unavailable")
    with monkeypatch.context() as m:
        m.setattr(app.state.chat.server, "start", fail)
        with pytest.raises(RuntimeError):
            await app.state.scheduler.manager_action(one, params)
    assert Todos(app.state.db).list(one) == [todo]
    assert not app.state.db.all("SELECT * FROM tasks")
    task = await app.state.scheduler.manager_action(one, params)
    read = {"tool": "manager_action", "arguments": {"action": "list_todos", "todo_id": todo["id"]}}
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
    selected = [todo["id"]]
    conversation = app.state.chat.conversation(project)["id"]
    with pytest.raises(ValueError, match="requires an owner message"):
        await app.state.chat.submit(None, "Start", conversation, todos=selected)
    with pytest.raises(ValueError, match="changed"):
        await app.state.chat.submit(project, "Start", conversation, todos=["missing"])
    assert not app.state.db.all("SELECT * FROM executions")
    receipt = await app.state.chat.submit(project, "Explicit start", conversation, request_id="bound-start-000001", todos=selected)
    assert Todos(app.state.db).list(project) == [todo]
    assert not app.state.db.all("SELECT * FROM tasks")
    assert app.state.db.setting("execution_todo_sources:" + receipt["execution_id"]) == selected
    # The UI source is deterministic even if delegate omits the optional todos field.
    task = await app.state.scheduler.manager_action(project, {"tool": "manager_action",
        "conversation_id": conversation, "manager_execution_id": receipt["execution_id"],
        "arguments": {"action": "delegate", "objective": "Execute source objective"}})
    assert not Todos(app.state.db).list(project)
    assert app.state.db.setting("todo_handoff:" + todo["id"])["task_id"] == task["id"]
    assert await app.state.chat.submit(project, "Explicit start", conversation, request_id="bound-start-000001", todos=selected) == receipt
    assert len(app.state.db.all("SELECT * FROM tasks")) == 1


@pytest.mark.parametrize("details", ['Original details', 'x' * 24000, ' ' * 24000], ids=['normal', 'maximum', 'blank'])
def test_start_endpoint_queues_once_with_source_and_worker_route(client, details):
    browser, app, settings = client
    assert browser.post('/api/todos/missing/start').status_code == 401
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    todo = Todos(app.state.db).save(project, 'Start exactly once', details)
    path = '/api/todos/' + todo['id'] + '/start'
    assert browser.post(path, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    first = browser.post(path)
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt['result'] == 'accepted' and receipt['status'] == 'queued'
    task = app.state.scheduler.tasks.get(receipt['task_id'])
    assert task['project_id'] == project
    assert task['origin_conversation'] == app.state.chat.conversation(project)['id']
    assert task['description'] == todo['title'] + '\n\n' + details
    assert task['worker_route']['model'] == 'test-model'
    handoff = app.state.db.setting('todo_handoff:' + todo['id'])
    assert handoff['source'] == todo and handoff['task_id'] == task['id']
    assert browser.post(path).json() == receipt
    assert len(app.state.db.all('SELECT * FROM tasks')) == 1
    assert len(app.state.db.all("SELECT * FROM events WHERE kind='todo.consumed'")) == 1
    assert not Todos(app.state.db).list(project)
    assert not app.state.db.all('SELECT * FROM executions')  # no manager round-trip
    assert not app.state.db.all('SELECT * FROM messages')


@pytest.mark.smoke
def test_start_dispatches_maximum_multi_paragraph_brief_verbatim_to_worker(client):
    import json

    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')
    title = '1/3 Complete worker brief: ' + '界' * 173
    assert len(title) == 200
    paragraph = ('\n\nGoal: preserve all agreed decisions.\nObserved problem: thin instructions lose context.\n'
                 'Decision: preserve Unicode — ქართული — and concrete examples.\n'
                 'Acceptance: keep every paragraph, including the last one.\n')
    details = ('  \n' + paragraph * 150)[:23940] + '\n\nFinal acceptance: preserve this tail verbatim.\n  '
    details += ' ' * (24000 - len(details))
    todo = Todos(app.state.db).save(project['id'], title, details)
    response = browser.post('/api/todos/' + todo['id'] + '/start')
    assert response.status_code == 200, response.text
    task = app.state.scheduler.tasks.get(response.json()['task_id'])
    # Exercise the same prompt builder used at worker dispatch, not the handoff archive.
    prompt = app.state.core.prompt(task, project, settings.project_roots[0] / 'one', 'work')
    objective = json.loads(json.dumps(prompt, ensure_ascii=False))['objective']
    assert objective == title + '\n\n' + details
    assert details in objective
    assert len(objective) == 24202


def test_idea_cannot_start_until_rewritten_ready_and_kind_round_trips(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    idea = browser.post('/api/todos', json={'project_id': project, 'title': 'Rough thought', 'kind': 'idea'}).json()
    assert idea['kind'] == 'idea'
    assert Todos(app.state.db).save(project, 'Default kind', 'x')['kind'] == 'ready'
    assert browser.post('/api/todos', json={'project_id': project, 'title': 'Bad', 'kind': 'draft'}).status_code == 422
    path = '/api/todos/' + idea['id'] + '/start'
    response = browser.post(path)
    assert response.status_code >= 400 and 'is an Idea' in response.text
    assert not app.state.db.all('SELECT * FROM tasks')
    assert not app.state.db.setting('todo_handoff:' + idea['id'])
    kept = browser.patch('/api/todos/' + idea['id'], json={'project_id': project, 'title': 'Still rough'}).json()
    assert kept['kind'] == 'idea'  # an update without kind keeps it
    ready = browser.patch('/api/todos/' + idea['id'],
                          json={'project_id': project, 'title': 'Full brief', 'details': 'Goal', 'kind': 'ready'}).json()
    assert ready['kind'] == 'ready'
    assert browser.post(path).json()['task_id']
    assert not [t for t in Todos(app.state.db).list(project) if t['id'] == idea['id']]


@pytest.mark.asyncio
async def test_manager_dispatch_rejects_idea_and_save_todo_promotes_it(client, monkeypatch):
    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    monkeypatch.setattr(app.state.chat, 'launch', lambda *args: None)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    todos = Todos(app.state.db)
    idea = todos.save(project, 'Worker briefs', 'Keep chat decisions', kind='idea')
    params = {'tool': 'manager_action', 'manager_execution_id': 'manager-exec',
              'conversation_id': app.state.chat.conversation(project)['id'], 'arguments': {}}
    for action in ('delegate', 'continue'):
        params['arguments'] = {'action': action, 'objective': 'Thin idea', 'todos': [idea['id']], 'task_id': 'x'}
        with pytest.raises(ValueError, match='is an Idea'):
            await app.state.scheduler.manager_action(project, params)
    listed = await app.state.scheduler.manager_action(project, {**params, 'arguments': {'action': 'list_todos', 'kind': 'idea'}})
    assert [e['id'] for e in listed['entries']] == [idea['id']] and listed['entries'][0]['kind'] == 'idea'
    none = await app.state.scheduler.manager_action(project, {**params, 'arguments': {'action': 'list_todos', 'kind': 'ready'}})
    assert none['entries'] == []
    details = 'Goal: briefs.\nAcceptance: verify.\nOrder: independent.'
    params['arguments'] = {'action': 'save_todo', 'todo_id': idea['id'], 'title': 'Worker briefs', 'details': details,
                           'kind': 'ready'}
    assert (await app.state.scheduler.manager_action(project, params))['kind'] == 'ready'
    params['arguments'] = {'action': 'delegate', 'objective': 'Worker briefs\n\n' + details, 'todos': [idea['id']]}
    task = await app.state.scheduler.manager_action(project, params)
    assert task['description'] == 'Worker briefs\n\n' + details
    assert todos.list(project) == []


def test_consume_and_chat_selection_reject_idea(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    idea = Todos(app.state.db).save(project, 'Only a thought', kind='idea')
    task = app.state.scheduler.tasks.add(project, 'Some task')
    with pytest.raises(ValueError, match='is an Idea'), app.state.db.transaction() as conn:
        Todos(app.state.db).consume(conn, [idea['id']], project, task['id'])
    response = browser.post('/api/chat', json={'project_id': project, 'content': 'go', 'todos': [idea['id']],
                                               'request_id': 'req-idea', 'conversation_id': 'x'})
    assert response.status_code >= 400
    assert Todos(app.state.db).list(project)[0]['id'] == idea['id']


def test_kind_migration_turns_draft_prefix_into_ideas(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    db = app.state.db
    todos = Todos(db)
    draft = todos.save(project, 'Draft: Quick capture', 'Rough')
    spaced = todos.save(project, 'Draft:   Spaced', '')
    plain = todos.save(project, 'Draft notes are fine here', 'Not the prefix')
    other = todos.save(project, 'Complete brief', 'Goal')
    with db.transaction() as conn:
        conn.execute("DELETE FROM schema_version WHERE version=17")
        conn.execute("UPDATE todos SET kind='ready'")
    migrated = Database(settings.db_path)
    rows = {t['id']: t for t in Todos(migrated).list(project)}
    assert (rows[draft['id']]['kind'], rows[draft['id']]['title']) == ('idea', 'Quick capture')
    assert (rows[spaced['id']]['kind'], rows[spaced['id']]['title']) == ('idea', 'Spaced')
    assert rows[plain['id']]['kind'] == 'ready' and rows[plain['id']]['title'] == plain['title']
    assert rows[other['id']]['kind'] == 'ready'
    assert rows[draft['id']]['details'] == 'Rough'
    assert Database(settings.db_path).one('SELECT 1 FROM schema_version WHERE version=17')
    Database(settings.db_path)  # idempotent


def test_kind_migration_adds_column_to_pre_kind_database(client):
    _, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    db = app.state.db
    Todos(db).save(project, 'Draft: Old row', 'x')
    Todos(db).save(project, 'Old ready', 'y')
    with db.transaction() as conn:
        conn.execute("ALTER TABLE todos DROP COLUMN kind")
        conn.execute("DELETE FROM schema_version WHERE version=17")
    kinds = {t['title']: t['kind'] for t in Todos(Database(settings.db_path)).list(project)}
    assert kinds == {'Old row': 'idea', 'Old ready': 'ready'}


def test_start_endpoint_dispatch_failure_rolls_back_and_retry_succeeds(client, monkeypatch):
    from ai_manager.codex import CodexError

    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    todo = Todos(app.state.db).save(project, 'Keep on failure', 'Keep source')
    path = '/api/todos/' + todo['id'] + '/start'
    original = app.state.db.event

    def fail(conn, kind, *args, **kwargs):
        if kind == 'todo.consumed':
            raise CodexError('Dispatch persistence unavailable')
        return original(conn, kind, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(app.state.db, 'event', fail)
        response = browser.post(path)
        assert response.status_code == 503 and 'unavailable' in response.json()['detail']
    assert Todos(app.state.db).list(project) == [todo]
    assert not app.state.db.all('SELECT * FROM tasks')
    assert not app.state.db.setting('todo_handoff:' + todo['id'])
    assert browser.post(path).status_code == 200
    assert len(app.state.db.all('SELECT * FROM tasks')) == 1


def test_start_uses_source_project_worker_and_distinct_entries_are_distinct_tasks(client):
    from concurrent.futures import ThreadPoolExecutor

    from ai_manager.model_settings import current, save, set_lock

    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    pair = {'model': 'test-model', 'effort': 'high'}
    db, server = app.state.db, app.state.chat.server
    set_lock(db, project, {'revision': current(db)['revision'], 'locked': True}, server)
    save(db, server, {**current(db, server, project), 'worker': pair}, project)
    first, second = [Todos(app.state.db).save(project, 'Identical idea', 'Identical details') for _ in range(2)]
    path = '/api/todos/' + first['id'] + '/start'
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(lambda _: browser.post(path).json(), range(3)))
    assert all(receipt == receipts[0] for receipt in receipts)
    second_receipt = browser.post('/api/todos/' + second['id'] + '/start').json()
    assert second_receipt['task_id'] != receipts[0]['task_id']
    tasks = [app.state.scheduler.tasks.get(task['id']) for task in app.state.scheduler.tasks.list(project)]
    assert len(tasks) == 2 and all(task['worker_route'] == pair for task in tasks)
    assert not Todos(app.state.db).list(project)


@pytest.mark.parametrize('failure', ['catalog', 'worker'])
def test_start_catalog_or_worker_failure_preserves_source(client, monkeypatch, failure):
    from ai_manager.codex import CodexError

    browser, app, settings = client
    sign_in(browser, app, settings)
    browser.portal.call(app.state.scheduler.close)
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    todo = Todos(app.state.db).save(project, 'Try later', 'Full source')

    async def fail_catalog():
        raise CodexError('Catalog unavailable')

    def fail_worker(*args, **kwargs):
        raise ValueError('Selected worker unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(app.state.chat.server, 'start' if failure == 'catalog' else 'route',
                      fail_catalog if failure == 'catalog' else fail_worker)
        response = browser.post('/api/todos/' + todo['id'] + '/start')
    assert response.status_code == (503 if failure == 'catalog' else 409)
    assert 'unavailable' in response.json()['detail']
    assert Todos(app.state.db).list(project) == [todo]
    assert not app.state.db.all('SELECT * FROM tasks')
    assert not app.state.db.setting('todo_handoff:' + todo['id'])
    assert browser.post('/api/todos/' + todo['id'] + '/start').status_code == 200

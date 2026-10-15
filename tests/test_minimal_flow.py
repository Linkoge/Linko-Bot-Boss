import asyncio
import json
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer

from ai_manager.chat import Chat
from ai_manager.codex import ARCHITECTURE_GUIDE, MANAGER_INSTRUCTIONS, RULES, AppServer, safe_config
from ai_manager.config import Settings
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.orchestration import MANAGER_TOOL, Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry


@pytest.fixture
def system(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,))
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    for name in ("one", "two"):
        (root / name).mkdir()
        registry.add(root / name)
    chat = Chat(db, registry, FakeServer(settings))
    return db, registry, chat, settings


class NativeFixture(FakeServer):
    release = None

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.native = native
        self.process = SimpleNamespace(pid=os.getpid())

    async def request(self, method, params, timeout=30):
        if method == "thread/read":
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "finished-turn",
                            "status": "completed",
                            "items": [{"type": "agentMessage", "text": "Recovered outcome"}],
                        }
                    ]
                }
            }
        if method != "turn/start":
            return {}
        self.prompts.append(params["input"][0]["text"])
        queue = self.queues[params["threadId"]]

        async def emit():
            if self.release:
                await self.release.wait()
            queue.put_nowait(
                {"method": "item/agentMessage/delta", "params": {"delta": "Objective completed; tests passed."}}
            )
            queue.put_nowait(
                {"method": "turn/completed", "params": {"turn": {"id": "native-turn", "status": "completed"}}}
            )

        asyncio.create_task(emit())
        return {"turn": {"id": "native-turn"}}


async def settled(workers):
    await asyncio.gather(*list(workers.jobs.values()))


@pytest.mark.smoke
def test_idempotency_strict_order_one_worker_and_future_projects(system):
    db, registry, chat, settings = system
    tasks = Tasks(db)
    p, q = registry.list()
    a = tasks.add(p["id"], "first", chat.conversation(p["id"])["id"], "key")
    assert tasks.add(p["id"], "first", request_key="key")["id"] == a["id"]
    b = tasks.add(p["id"], "next")
    c = tasks.add(q["id"], "other project")
    claimed = tasks.claim()
    assert claimed["id"] == a["id"]
    assert tasks.claim() is None  # One global worker, even across projects.
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET state='done',lease_owner=NULL WHERE id=?", (a["id"],))
        conn.execute("UPDATE executions SET status='completed',finished_at=1 WHERE id=?", (claimed["lease_owner"],))
    directory = settings.project_roots[0] / "future"
    directory.mkdir()
    future = registry.add(directory)
    d = tasks.add(future["id"], "works without configuration")
    order = []
    while task := tasks.claim():
        order.append(task["id"])
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET state='done',lease_owner=NULL WHERE id=?", (task["id"],))
            conn.execute("UPDATE executions SET status='completed',finished_at=1 WHERE id=?", (task["lease_owner"],))
    assert order == [b["id"], c["id"], d["id"]]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_direct_delegation_native_execution_and_return(system):
    db, registry, chat, settings = system
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    p = registry.list()[0]
    origin = chat.conversation(p["id"])["id"]
    params = {
        "tool": "manager_action",
        "arguments": {"action": "delegate", "objective": "Implement and test the complete feature"},
        "conversation_id": origin,
        "manager_execution_id": "manager-fixture",
        "callId": "request-1",
    }
    task = await scheduler.manager_action(p["id"], params)
    assert (await scheduler.manager_action(p["id"], params))["id"] == task["id"]
    assert (await scheduler.manager_action(p["id"], {**params, "callId": "second-call"}))["id"] == task["id"]
    repeated = {**params, "manager_execution_id": "next-request", "callId": "new-call"}
    assert (await scheduler.manager_action(p["id"], repeated))["id"] == task["id"]
    await scheduler.tick()
    await settled(workers)
    result = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert result["worktree"] == p["path"]
    assert Tasks(db).get(task["id"])["status"] == "done"
    assert (await scheduler.manager_action(p["id"], repeated))["id"] == task["id"]
    assert result["delivery"] == "pending"
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    assert db.one("SELECT delivery FROM executions WHERE id=?", (result["id"],))["delivery"] == "delivered"
    assert [m["role"] for m in chat.history(p["id"])["messages"]] == ["assistant"]
    assert len(Tasks(db).list()) == 1
    assert not db.one("SELECT 1 FROM sqlite_master WHERE name='verification_results'")
    assert len(chat.server.prompts) == 1
    assert len(db.all("SELECT * FROM executions")) == 2


@pytest.mark.asyncio
async def test_pending_result_restart_archive_and_busy_conversation(system):
    db, registry, chat, settings = system
    p = registry.list()[0]
    origin = chat.conversation(p["id"])["id"]
    task = Tasks(db).add(p["id"], "done", origin)
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.launch(Tasks(db).claim())
    await settled(workers)
    with db.transaction() as conn:
        conn.execute("INSERT INTO messages VALUES('u',?,'user','prior','completed',NULL,?)", (origin, time.time()))
    chat.reset(p["id"], origin, "u", True, archive=True, title="Original")
    restored = Chat(Database(db.path), registry, FakeServer(settings))
    # A queued user turn must serialize result delivery, without losing it.
    current = restored.conversation(p["id"])["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO messages VALUES('busy',?,'assistant','','queued',NULL,?)", (origin, time.time()))
    await restored.deliver_results()
    assert not restored.jobs
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET status='completed' WHERE id='busy'")
    await restored.deliver_results()
    await asyncio.gather(*list(restored.jobs.values()))
    await restored.deliver_results()
    assert len(restored.server.prompts) == 1
    messages = restored.saved_history(origin, p["id"])["messages"]
    assert not any(m["role"] == "worker" for m in messages)
    assert messages[-1]["role"] == "assistant" and messages[-1]["status"] == "completed"
    assert restored.history(p["id"])["messages"] == []
    assert current != origin
    assert Tasks(db).get(task["id"])["status"] == "done"


@pytest.mark.asyncio
async def test_catastrophic_question_waits_for_exact_answer(system):
    db, registry, _, settings = system
    tasks = Tasks(db)
    task = tasks.add(registry.list()[0]["id"], "dangerous objective")
    task = tasks.claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    request = asyncio.create_task(
        workers.question(
            task["lease_owner"],
            "item/commandExecution/requestApproval",
            {"threadId": "worker", "command": "wipe exact fixture"},
        )
    )
    await asyncio.sleep(0.03)
    assert not request.done()
    question = db.one("SELECT * FROM questions")
    assert Tasks(db).get(task["id"])["status"] == "running"
    assert Tasks(db).get(task["id"])["blocker"] == "Waiting for your answer"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE questions SET status='answered',answer=? WHERE id=?",
            (json.dumps({"approve": False}), question["id"]),
        )
    assert await request == {"decision": "decline"}


@pytest.mark.smoke
def test_backup_restore_and_native_configuration(system, tmp_path):
    db, _, _, settings = system
    backup, restored = tmp_path / "backup.sqlite", tmp_path / "restored.sqlite"
    db.backup(backup)
    Database.restore(backup, restored)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 2
    native = AppServer(settings, native=True)
    assert native.native and safe_config(True)["features.unified_exec"] is True
    assert safe_config()["features.unified_exec"] is False


@pytest.mark.asyncio
async def test_manager_completion_after_crash_delivers_once(system):
    db, registry, chat, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "already done")
    task = Tasks(db).claim()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET delivery='running',delivery_execution='reply',finished_at=? WHERE id=?",
            (time.time(), task["lease_owner"]),
        )
        conn.execute(
            "INSERT INTO executions(id,agent_id,status,started_at,thread_id,turn_id) VALUES('reply','manager','running',?,'saved','finished-turn')",
            (time.time(),),
        )
    conversation = chat.conversation(task["project_id"])
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('answer',?,'assistant','','streaming','reply',?)",
            (conversation["id"], time.time()),
        )
    restored = Chat(db, registry, NativeFixture(settings))
    await restored.recover()
    assert db.one("SELECT delivery FROM executions WHERE id=?", (task["lease_owner"],))["delivery"] == "delivered"
    assert restored.history(task["project_id"])["messages"][-1]["content"] == "Recovered outcome"
    await restored.deliver_results()
    assert not restored.jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [("done", ""), ("failed", "Disk full")])
async def test_terminal_result_is_reasoning_input_and_finish_is_idempotent(system, status, error):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    tasks = Tasks(db)
    tasks.add(project["id"], "Create requested text file", origin)
    task = tasks.claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET input=?,thread_id='worker-thread',turn_id='worker-turn' WHERE id=?",
            (json.dumps({"objective": "Original objective"}), task["lease_owner"]),
        )
        conn.execute("UPDATE tasks SET description='Later objective' WHERE id=?", (task["id"],))
    workers.end(task["lease_owner"], status, error, "Actual worker result")
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    evidence = json.loads(chat.server.prompts[0])["worker_result"]
    scheduler = Scheduler(db, registry, settings, workers, chat)
    retained = await scheduler.manager_action(project["id"], {"tool": "manager_action", "arguments": {"action": "read_result", "execution_id": evidence["execution_id"]}})
    assert json.loads(retained["input"])["objective"] == "Original objective"
    assert retained["output"] == "Actual worker result"
    assert evidence["status"] == status
    assert evidence["reason"] == error
    assert "execution" not in evidence
    diagnostic = db.one("SELECT * FROM executions WHERE id=?", (task["lease_owner"],))
    assert diagnostic["thread_id"] == "worker-thread"
    assert diagnostic["turn_id"] == "worker-turn"
    before = db.one("SELECT * FROM executions WHERE id=?", (task["lease_owner"],))
    workers.end(task["lease_owner"], "failed", "Duplicate error", "Duplicate event")
    assert db.one("SELECT * FROM executions WHERE id=?", (task["lease_owner"],)) == before
    await chat.deliver_results()
    assert len(chat.server.prompts) == 1
    assert len(db.all("SELECT * FROM events WHERE kind='worker.finished'")) == 1


@pytest.mark.asyncio
async def test_failed_manager_delivery_retries_after_restart_without_duplicate_worker_message(system, monkeypatch):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], "Create a file", origin)
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.launch(Tasks(db).claim())
    await settled(workers)

    async def fail(*args, **kwargs):
        raise ConnectionError("Temporary manager outage")

    monkeypatch.setattr(chat.server, "thread", fail)
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    result = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert result["delivery"] == "running"
    await chat.deliver_results()
    assert not chat.jobs  # Persisted backoff prevents a model call every scheduler tick.
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET retry_at=0 WHERE id=?", (result["delivery_execution"],))
    restored = Chat(Database(db.path), registry, FakeServer(settings))
    await restored.recover()
    await restored.deliver_results()
    await asyncio.gather(*list(restored.jobs.values()))
    assert db.one("SELECT delivery FROM executions WHERE id=?", (result["id"],))["delivery"] == "delivered"
    assert len(db.all("SELECT id FROM messages WHERE role='worker'")) == 1
    assert len(db.all("SELECT id FROM messages WHERE role='assistant' AND status='completed'")) == 1
    await restored.deliver_results()
    assert len(restored.server.prompts) == 1


@pytest.mark.asyncio
async def test_results_stay_in_their_project_or_global_origin(system):
    db, registry, chat, settings = system
    projects = registry.list()
    origins = [chat.conversation(projects[0]["id"])["id"], chat.conversation()["id"]]
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    for project, origin in zip(projects, origins, strict=True):
        Tasks(db).add(project["id"], "Result for " + project["name"], origin)
        workers.launch(Tasks(db).claim())
        await settled(workers)
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    for project, origin in zip(projects, origins, strict=True):
        messages = db.all("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (origin,))
        assert [m["role"] for m in messages] == ["worker", "assistant"]
        notice = json.loads(messages[0]["content"])
        assert notice["project_id"] == project["id"]
        assert [m["role"] for m in chat.saved_history(origin, project["id"] if origin == origins[0] else None)["messages"]] == ["assistant"]
    assert chat.history(projects[1]["id"])["messages"] == []


@pytest.mark.asyncio
async def test_cleared_origin_does_not_receive_results_in_new_chat(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    Tasks(db).add(project["id"], "Original request", origin)
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.launch(Tasks(db).claim())
    await settled(workers)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('prior',?,'user','request','completed',NULL,?)", (origin, time.time())
        )
    chat.reset(project["id"], origin, "prior", True)
    await chat.deliver_results()
    await settled(chat)
    assert chat.history(project["id"])["messages"] == []
    assert db.one("SELECT delivery FROM executions WHERE task_id IS NOT NULL")["delivery"] == "delivered"
    assert db.one("SELECT id FROM messages WHERE conversation_id=? AND role='worker'", (origin,))
    assert db.one("SELECT context FROM conversations WHERE id=?", (origin,))["context"] == "trash:" + origin


@pytest.mark.asyncio
async def test_result_notice_cannot_start_new_work_but_owner_can_continue(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], "Create the requested file", origin)
    task = Tasks(db).claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.end(task["lease_owner"], "failed", "Temporary error", "File creation needs another attempt")
    attempts = []

    class ContinueServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                prompt = json.loads(params["input"][0]["text"])
                retained = await self.tool_handlers[params["threadId"]]({"tool": "manager_action", "arguments": {"action": "read_result", "execution_id": prompt["worker_result"]["execution_id"]}})
                assert retained["output"] == "File creation needs another attempt"
                for action in ("continue", "delegate"):
                    with pytest.raises(ValueError, match="explicit owner authorization"):
                        await self.tool_handlers[params["threadId"]](
                            {"tool": "manager_action", "arguments": {"action": action, "task_id": task["id"], "objective": "again"},
                             "callId": "continue-incomplete"})
                    attempts.append(action)
            return await super().request(method, params, timeout)

    chat.server = ContinueServer(settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    assert attempts == ["continue", "delegate"]
    assert Tasks(db).get(task["id"])["status"] == "failed" and len(Tasks(db).list()) == 1
    assert Tasks(db).control(task["id"], "continue")["status"] == "queued"  # the owner's explicit continue


@pytest.mark.asyncio
async def test_large_result_and_concurrent_delivery_claims(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    Tasks(db).add(project["id"], "a" * 24000, origin)
    task = Tasks(db).claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.end(task["lease_owner"], "done", "", "前" * 100000 + "Final result")
    await asyncio.gather(chat.deliver_results(), chat.deliver_results())
    await asyncio.gather(*list(chat.jobs.values()))
    assert len(chat.server.prompts) == 1
    prompt = json.loads(chat.server.prompts[0])
    assert prompt["worker_result"]["output_characters"] == 100012
    assert len(json.dumps(prompt["worker_result"])) < 1000
    assert "Final result" not in json.dumps(prompt)
    assert len(db.all("SELECT id FROM messages WHERE role='worker'")) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_manager_does_what_is_asked_with_no_chat_modes(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    (settings.project_roots[0] / project["name"] / "notes.txt").write_text("Project notes")
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action

    class AskServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                assert "mode" not in json.loads(params["input"][0]["text"])
                handler = self.tool_handlers[params["threadId"]]
                call = {"tool": "manager_action", "callId": "mode-call"}
                assert await handler({**call, "arguments": {"action": "read_document", "path": "notes.txt"}}) == {
                    "path": "notes.txt",
                    "content": "Project notes",
                    "next_offset": None,
                }
                for action in ("delegate", "pause_project", "resume_project"):
                    await handler({**call, "arguments": {"action": action, "objective": "Complete objective"}})
            return await super().request(method, params, timeout)

    chat.server = AskServer(settings)
    await chat.submit(project["id"], "Complete objective")
    await asyncio.gather(*list(chat.jobs.values()))
    assert len(Tasks(db).list()) == 1
    assert len(chat.server.prompts) == 1


@pytest.mark.smoke
def test_fresh_schema_contains_only_current_storage(system):
    db, _, _, _ = system
    tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {
        "schema_version",
        "projects",
        "tasks",
        "executions",
        "memory",
        "events",
        "settings",
        "owner",
        "sessions",
        "login_attempts",
        "conversations",
        "messages",
        "chat_archives",
        "history_archive",
        "chat_requests",
        "todos",
        "questions",
        "sqlite_sequence",
    }
    columns = {r["name"] for r in db.all("PRAGMA table_info(tasks)")}
    assert not columns & {
        "verification",
        "permissions",
        "kind",
        "verification_spec",
        "verification_status",
        "blocker_code",
        "recovery_identity",
        "lease_until",
        "priority",
        "lifecycle",
        "legacy",
    }
    assert {"state", "held", "rounds", "requeues", "retry_at"} <= columns
    assert not columns & {"round_limit", "checkpoints", "open_round", "merge_requested", "waits"}


@pytest.mark.asyncio
async def test_background_queue_runs_one_task_at_a_time_in_order(system):
    db, registry, chat, settings = system
    for project in registry.list():
        Tasks(db).add(project["id"], "Independent work", chat.conversation(project["id"])["id"])
    NativeFixture.release = asyncio.Event()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    try:
        await scheduler.tick()
        await asyncio.sleep(0.03)
        assert len(workers.jobs) == 1
        assert len([t for t in Tasks(db).list() if t["state"] == "running"]) == 1
        directory = settings.project_roots[0] / "new-project"
        directory.mkdir()
        future = registry.add(directory)
        Tasks(db).add(future["id"], "New project work")
        await scheduler.tick()
        assert len(workers.jobs) == 1
        NativeFixture.release.set()
        for _ in range(3):
            await settled(workers)
            await scheduler.tick()
        await settled(workers)
        assert all(t["status"] == "done" for t in Tasks(db).list())
        runs = db.all("SELECT started_at FROM executions WHERE task_id IS NOT NULL ORDER BY started_at")
        assert len(runs) == 3
    finally:
        NativeFixture.release.set()
        NativeFixture.release = None
        await workers.close()
        await chat.close()


@pytest.mark.asyncio
async def test_uploaded_files_and_photos_reach_delegated_worker(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    path = settings.data_dir / 'test-photo.png'
    path.write_bytes(b'fixture; native image bytes are validated by upload tests')
    refs = [{'id': 'a'*32, 'name': 'photo.png', 'mime': 'image/png', 'path': str(path), 'sha256': 'fixture', 'size': 1}]
    refs.extend([{'id': 'b'*32, 'name': 'active.svg', 'mime': 'image/svg+xml', 'path': str(path),
                  'native_image': False}, {'id': 'c'*32, 'name': 'bad.png', 'mime': 'image/png',
                  'path': str(path), 'native_image': False}])
    db.set_setting('execution_attachments:origin-manager', refs)
    captured = []
    class AttachmentFixture(NativeFixture):
        async def request(self, method, params, timeout=30):
            if method == 'turn/start':
                captured.append(params['input'])
            return await super().request(method, params, timeout)
    workers = Core(db, registry, settings, Pipeline(db, registry), AttachmentFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    conversation = chat.conversation(project['id'])
    params = {'tool': 'manager_action', 'arguments': {'action': 'delegate', 'objective': 'Inspect supplied file'},
              'manager_execution_id': 'origin-manager', 'conversation_id': conversation['id'], 'callId': 'attachment-call'}
    task = await scheduler.manager_action(project['id'], params)
    claimed = scheduler.tasks.claim()
    assert claimed['id'] == task['id']
    workers.launch(claimed)
    await settled(workers)
    assert len(captured[0]) == 2
    assert captured[0][1] == {'type': 'localImage', 'path': str(path)}
    assert json.loads(captured[0][0]['text'])['task_context']['attachments'] == refs


def test_manager_and_worker_instructions_describe_only_the_current_behaviour():
    actions = MANAGER_TOOL["inputSchema"]["properties"]["action"]["enum"]
    assert not {"update_brief", "record_delivery"} & set(actions)
    properties = MANAGER_TOOL["inputSchema"]["properties"]
    assert not {"mode", "brief", "retained", "resolved", "entry", "evidence", "deployment_receipt", "revision"} & set(properties)
    assert properties["todos"]["items"] == {"type": "string"} and {"model", "effort"} <= set(properties)
    # The advisory guide names removed machinery to explain it; operational instructions must not reintroduce it.
    text = MANAGER_INSTRUCTIONS.removesuffix(ARCHITECTURE_GUIDE) + MANAGER_TOOL["description"]
    for gone in ("Auto-deploy", "checkpoint", "Execute", "Conversation mode", "record_delivery", "update_brief", "revision"):
        assert gone not in text, gone
    assert "at most 5 rounds" in RULES and "checkpoint" not in RULES and "hard cap" not in RULES.lower()
    assert "automatically" in MANAGER_INSTRUCTIONS and "automatic" in MANAGER_INSTRUCTIONS

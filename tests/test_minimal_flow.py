import asyncio
import json
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer

from ai_manager.chat import Chat
from ai_manager.codex import AppServer, safe_config
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


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


def test_legacy_migration_preserves_rows_and_history(system):
    db, registry, _, _ = system
    project = registry.list()[0]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tasks(id,project_id,description,acceptance,status,created_at,updated_at) VALUES('old',?,'old objective','criteria','verifying',?,?)",
            (project["id"], time.time(), time.time()),
        )
        conn.execute("DELETE FROM schema_version WHERE version=7")
        db.event(conn, "historical.evidence", {"original": "unchanged"})
    before = db.all("SELECT * FROM events")
    reopened = Database(db.path)
    task = Tasks(reopened).get("old")
    assert task["status"] == "waiting" and task["legacy_status"] == "verifying"
    assert task["acceptance"] == "criteria"
    assert reopened.all("SELECT * FROM events") == before
    assert reopened.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert not reopened.all("PRAGMA foreign_key_check")
    assert Tasks(reopened).claim(2) is None


def test_idempotency_exclusive_project_and_future_projects(system):
    db, registry, chat, settings = system
    tasks = Tasks(db)
    p, q = registry.list()
    a = tasks.add(p["id"], "first", chat.conversation(p["id"])["id"], "key")
    assert tasks.add(p["id"], "first", request_key="key")["id"] == a["id"]
    tasks.add(p["id"], "next")
    tasks.add(q["id"], "parallel")
    claimed = [tasks.claim(2), tasks.claim(2)]
    assert {t["project_id"] for t in claimed} == {p["id"], q["id"]}
    assert tasks.claim(2) is None
    # Unknown surviving process holds its project even in waiting state.
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='waiting' WHERE id=?", (a["id"],))
    assert tasks.claim(3) is None
    directory = settings.project_roots[0] / "future"
    directory.mkdir()
    future = registry.add(directory)
    tasks.add(future["id"], "works without configuration")
    assert tasks.claim(3)["project_id"] == future["id"]


@pytest.mark.asyncio
async def test_direct_delegation_native_execution_and_return(system):
    db, registry, chat, settings = system
    workers = Workers(db, registry, settings, NativeFixture)
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
    await scheduler.tick()
    await settled(workers)
    result = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert result["worktree"] == p["path"]
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert result["delivery"] == "pending"
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    assert db.one("SELECT delivery FROM executions WHERE id=?", (result["id"],))["delivery"] == "delivered"
    assert [m["role"] for m in chat.history(p["id"])["messages"]] == ["worker", "assistant"]
    assert len(Tasks(db).list()) == 1
    assert db.all("SELECT * FROM verification_results") == []
    assert len(chat.server.prompts) == 1
    assert len(db.all("SELECT * FROM executions")) == 2


@pytest.mark.asyncio
async def test_pending_result_restart_archive_and_busy_conversation(system):
    db, registry, chat, settings = system
    p = registry.list()[0]
    origin = chat.conversation(p["id"])["id"]
    task = Tasks(db).add(p["id"], "done", origin)
    workers = Workers(db, registry, settings, NativeFixture)
    workers.launch(Tasks(db).claim(2))
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
    assert messages[-2]["role"] == "worker" and "Codex result" in messages[-2]["content"]
    assert messages[-1]["role"] == "assistant" and messages[-1]["status"] == "completed"
    assert restored.history(p["id"])["messages"] == []
    assert current != origin
    assert Tasks(db).get(task["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_catastrophic_question_waits_for_exact_answer(system):
    db, registry, _, settings = system
    tasks = Tasks(db)
    task = tasks.add(registry.list()[0]["id"], "dangerous objective")
    task = tasks.claim(2)
    workers = Workers(db, registry, settings, NativeFixture)
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
    assert Tasks(db).get(task["id"])["status"] == "waiting"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE questions SET status='answered',answer=? WHERE id=?",
            (json.dumps({"approve": False}), question["id"]),
        )
    assert await request == {"decision": "decline"}


@pytest.mark.asyncio
async def test_restart_recovers_completed_worker_without_replay(system):
    db, registry, _, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "completed before crash")
    task = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET thread_id='saved',turn_id='finished-turn',status='running' WHERE id=?",
            (task["lease_owner"],),
        )
    workers = Workers(Database(db.path), registry, settings, NativeFixture)
    await workers.recover()
    assert not workers.jobs
    assert Tasks(db).get(task["id"])["status"] == "completed"
    result = db.one("SELECT * FROM executions WHERE id=?", (task["lease_owner"],))
    assert result["output"] == "Recovered outcome" and result["delivery"] == "pending"


@pytest.mark.asyncio
async def test_launch_without_turn_id_is_not_blindly_replayed(system):
    db, registry, _, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "uncertain launch")
    task = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET thread_id='saved',status='starting' WHERE id=?", (task["lease_owner"],))
    workers = Workers(db, registry, settings, NativeFixture)
    await workers.recover()
    assert Tasks(db).get(task["id"])["status"] == "waiting"
    assert "uncertain" in Tasks(db).get(task["id"])["blocker"]
    assert not workers.jobs


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
async def test_shutdown_continues_active_work_but_preserves_user_wait(system):
    db, registry, _, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "long objective")
    task = Tasks(db).claim(2)
    NativeFixture.release = asyncio.Event()
    try:
        workers = Workers(db, registry, settings, NativeFixture)
        workers.launch(task)
        await asyncio.sleep(0.03)
        await workers.close()
        assert Tasks(db).get(task["id"])["status"] == "queued"
        assert Tasks(db).get(task["id"])["lease_owner"] is None
        assert Tasks(db).get(task["id"])["native_thread"]
        waiting = Tasks(db).add(registry.list()[1]["id"], "paused objective")
        Tasks(db).control(waiting["id"], "pause", waiting["revision"])
        await workers.recover()
        assert Tasks(db).get(waiting["id"])["status"] == "waiting"
    finally:
        NativeFixture.release.set()
        NativeFixture.release = None


@pytest.mark.asyncio
async def test_owned_detached_process_keeps_project_unavailable(system):
    from ai_manager.codex import process_start

    db, registry, _, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "detached")
    task = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET pid=?,process_start=? WHERE id=?",
            (os.getpid(), process_start(os.getpid()), task["lease_owner"]),
        )
    workers = Workers(db, registry, settings, NativeFixture)
    await workers.recover()
    assert Tasks(db).get(task["id"])["status"] == "waiting"
    assert Tasks(db).get(task["id"])["lease_owner"] == task["lease_owner"]
    Tasks(db).add(task["project_id"], "must not overlap")
    assert Tasks(db).claim(2) is None


@pytest.mark.asyncio
async def test_manager_completion_after_crash_delivers_once(system):
    db, registry, chat, settings = system
    task = Tasks(db).add(registry.list()[0]["id"], "already done")
    task = Tasks(db).claim(2)
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
@pytest.mark.parametrize("status,error", [("completed", ""), ("failed", "Disk full"), ("waiting", "Interrupted")])
async def test_terminal_result_is_reasoning_input_and_finish_is_idempotent(system, status, error):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    tasks = Tasks(db)
    tasks.add(project["id"], "Create requested text file", origin)
    task = tasks.claim(2)
    workers = Workers(db, registry, settings, NativeFixture)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET input=?,thread_id='worker-thread',turn_id='worker-turn' WHERE id=?",
            (json.dumps({"objective": "Original objective"}), task["lease_owner"]),
        )
        conn.execute("UPDATE tasks SET description='Later objective',lease_owner=NULL WHERE id=?", (task["id"],))
    workers.finish(task["lease_owner"], status, "Actual worker result", error)
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    content = json.loads(chat.server.prompts[0])["message"]
    evidence = json.loads(content.split("\n", 1)[1].split("\nReport", 1)[0])
    assert evidence["objective"] == "Original objective"
    assert evidence["task_status"] == status
    assert evidence["output"] == "Actual worker result"
    assert evidence["execution"]["status"] == status
    assert evidence["execution"]["error"] == error
    assert evidence["execution"]["thread_id"] == "worker-thread"
    assert evidence["execution"]["turn_id"] == "worker-turn"
    before = db.one("SELECT * FROM executions WHERE id=?", (task["lease_owner"],))
    workers.finish(task["lease_owner"], "failed", "Duplicate event", "Duplicate error")
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
    workers = Workers(db, registry, settings, NativeFixture)
    workers.launch(Tasks(db).claim(2))
    await settled(workers)

    async def fail(*args, **kwargs):
        raise RuntimeError("Temporary manager outage")

    monkeypatch.setattr(chat.server, "thread", fail)
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    result = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert result["delivery"] == "pending"
    await chat.deliver_results()
    assert not chat.jobs  # Persisted backoff prevents a model call every scheduler tick.
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET finished_at=? WHERE id=?", (time.time() - 31, result["delivery_execution"]))
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
    workers = Workers(db, registry, settings, NativeFixture)
    for project, origin in zip(projects, origins, strict=True):
        Tasks(db).add(project["id"], "Result for " + project["name"], origin)
        workers.launch(Tasks(db).claim(2))
    await settled(workers)
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    for project, origin in zip(projects, origins, strict=True):
        messages = db.all("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (origin,))
        assert [m["role"] for m in messages] == ["worker", "assistant"]
        assert "Result for " + project["name"] in messages[0]["content"]
    assert chat.history(projects[1]["id"])["messages"] == []


@pytest.mark.asyncio
async def test_cleared_origin_does_not_receive_results_in_new_chat(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    Tasks(db).add(project["id"], "Original request", origin)
    workers = Workers(db, registry, settings, NativeFixture)
    workers.launch(Tasks(db).claim(2))
    await settled(workers)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('prior',?,'user','request','completed',NULL,?)", (origin, time.time())
        )
    chat.reset(project["id"], origin, "prior", True)
    await chat.deliver_results()
    assert not chat.jobs
    assert chat.history(project["id"])["messages"] == []
    assert db.one("SELECT delivery FROM executions WHERE task_id IS NOT NULL")["delivery"] == "discarded"


def test_migration_recovers_previously_stranded_deliveries(system):
    db, registry, _, _ = system
    Tasks(db).add(registry.list()[0]["id"], "Stranded result")
    task = Tasks(db).claim(2)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET finished_at=?,delivery='waiting' WHERE id=?", (time.time(), task["lease_owner"])
        )
        conn.execute("DELETE FROM schema_version WHERE version=8")
    restored = Database(db.path)
    assert restored.one("SELECT delivery FROM executions WHERE id=?", (task["lease_owner"],))["delivery"] == "pending"


@pytest.mark.asyncio
async def test_manager_can_continue_incomplete_work_from_result(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], "Create the requested file", origin)
    task = Tasks(db).claim(2)
    workers = Workers(db, registry, settings, NativeFixture)
    workers.finish(task["lease_owner"], "failed", "File creation needs another attempt", "Temporary error")
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lease_owner=NULL WHERE id=?", (task["id"],))

    class ContinueServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                prompt = json.loads(params["input"][0]["text"])
                assert "File creation needs another attempt" in prompt["message"]
                await self.tool_handlers[params["threadId"]](
                    {
                        "tool": "manager_action",
                        "arguments": {"action": "continue", "task_id": task["id"]},
                        "callId": "continue-incomplete",
                    }
                )
            return await super().request(method, params, timeout)

    chat.server = ContinueServer(settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    assert Tasks(db).get(task["id"])["status"] == "queued"
    assert Tasks(db).get(task["id"])["origin_conversation"] == origin
    assert len(Tasks(db).list()) == 1
    assert not db.all("SELECT * FROM verification_results")


@pytest.mark.asyncio
async def test_large_result_and_concurrent_delivery_claims(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project["id"])["id"]
    Tasks(db).add(project["id"], "a" * 24000, origin)
    task = Tasks(db).claim(2)
    workers = Workers(db, registry, settings, NativeFixture)
    workers.finish(task["lease_owner"], "completed", "前" * 100000 + "Final result", "")
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lease_owner=NULL WHERE id=?", (task["id"],))
    await asyncio.gather(chat.deliver_results(), chat.deliver_results())
    await asyncio.gather(*list(chat.jobs.values()))
    assert len(chat.server.prompts) == 1
    prompt = json.loads(chat.server.prompts[0])
    assert '"output_truncated": true' in prompt["message"]
    assert "Final result" in prompt["message"]
    assert len(db.all("SELECT id FROM messages WHERE role='worker'")) == 1

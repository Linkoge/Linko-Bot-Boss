"""Accepted request and silent-turn failures use disposable storage/native fixtures."""

import asyncio
import json
import time

import pytest
from test_minimal_flow import system as system_fixture
from test_recovery import Faults, eligible, settle

from ai_manager.chat import Chat
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.recovery import SilentObserver
from ai_manager.workers import Workers

system = system_fixture


@pytest.mark.asyncio
async def test_lost_acceptance_retry_after_completion_restart_and_clear(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    conversation = chat.conversation(project["id"])
    faults = Faults()
    chat.server = faults.factory(settings)
    workers = Workers(db, registry, settings, faults.factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    request = dict(
        project_id=project["id"],
        content="Delegate exactly once",
        conversation_id=conversation["id"],
        request_id="owner-request-0001",
    )

    async def delegate(server, params, prompt):
        result = await scheduler.manager_action(
            project["id"],
            {
                "manager_execution_id": db.one("SELECT id FROM executions WHERE agent_id='manager'")["id"],
                "conversation_id": conversation["id"],
                "mode": "auto",
                "arguments": json.dumps({"action": "delegate", "objective": "Do this once"}),
                "callId": "call-one",
                "tool": "manager_action",
            },
        )
        assert result["description"] == "Do this once"

    faults.on_start = delegate
    # Discard the first HTTP-style acceptance; caller only has its persisted ID.
    await chat.submit(**request)
    original = json.loads(db.one("SELECT receipt FROM chat_requests")["receipt"])
    assert await chat.submit(**request) == original
    await settle(chat)
    faults.on_start = None
    restarted = Chat(Database(settings.db_path), registry, faults.factory(settings))
    assert await restarted.submit(**request) == original
    assert len(faults.calls) == 1
    assert len(db.all("SELECT * FROM messages WHERE role='user'")) == 1
    assert len(db.all("SELECT * FROM executions WHERE agent_id='manager'")) == 1
    assert len(db.all("SELECT * FROM tasks")) == 1
    for changed in (
        {"content": "Different"},
        {"mode": "conversation"},
        {"project_id": None},
        {"conversation_id": None},
    ):
        with pytest.raises(ValueError, match="different message or context"):
            await restarted.submit(**(request | changed))
    last = chat.history(project["id"])["messages"][-1]["id"]
    chat.reset(project["id"], conversation["id"], last, True)
    assert await restarted.submit(**request) == original
    assert not chat.history(project["id"])["messages"]


@pytest.mark.asyncio
@pytest.mark.parametrize("native_status", ["inProgress", "completed", "failed", "interrupted", "inspection_failed"])
async def test_silent_manager_observation_preserves_or_reconciles(system, native_status):
    db, registry, chat, settings = system
    faults = Faults(["hold"])
    chat.server = faults.factory(settings)
    receipt = await chat.submit(None, "Wait", request_id="silent-request-001")
    await asyncio.sleep(0.02)
    row = db.one("SELECT * FROM executions WHERE id=?", (receipt["execution_id"],))
    turn = faults.turns[row["thread_id"]][0]
    turn["status"] = "inProgress" if native_status == "inspection_failed" else native_status
    turn["items"] = [{"type": "agentMessage", "text": "Exact native final text"}]
    if native_status == "failed":
        turn["error"] = {"message": "Disconnected", "codexErrorInfo": "responseStreamDisconnected"}
    if native_status == "inspection_failed":

        async def broken(*args, **kwargs):
            raise TimeoutError("Cannot inspect")

        chat.server.request = broken
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET started_at=? WHERE id=?", (time.time() - 61, row["id"]))
    with db.transaction() as conn:
        conn.execute("UPDATE events SET created_at=? WHERE execution_id=?", (time.time() - 61, row["id"]))
    observer = SilentObserver(db)
    await observer.observe({row["id"]: chat.server})
    await asyncio.sleep(0.02)
    result = db.one("SELECT * FROM executions WHERE id=?", (row["id"],))
    if native_status in {"inProgress", "inspection_failed"}:
        assert result["status"] == "running" and result["finished_at"] is None
        assert result["retry_count"] == 0 and row["id"] in chat.jobs
        await observer.observe({row["id"]: chat.server})
        assert len(db.all("SELECT * FROM events WHERE kind='recovery.observed'")) == 1
        await chat.close()
    elif native_status == "completed":
        assert result["status"] == "completed" and result["output"] == "Exact native final text"
        assert chat.history()["messages"][-1]["content"] == result["output"]
    else:
        assert result["status"] == "recovering" and result["finished_at"] is None
        assert result["output"] == "Exact native final text"
        eligible(db)
        await chat.recover()
        await settle(chat)
        assert len(faults.calls) == 2
    assert len(db.all("SELECT * FROM messages WHERE role='user'")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("activity", ["item/commandExecution/outputDelta", "item/tool/call", "approval"])
async def test_recent_tools_and_pending_approval_are_not_silent_failures(system, activity):
    db, _, chat, settings = system
    faults = Faults(["hold"])
    chat.server = faults.factory(settings)
    receipt = await chat.submit(None, "Long tool")
    await asyncio.sleep(0.02)
    row = db.one("SELECT * FROM executions WHERE id=?", (receipt["execution_id"],))
    faults.turns[row["thread_id"]][0]["status"] = "inProgress"
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET started_at=? WHERE id=?", (time.time() - 61, row["id"]))
        if activity == "approval":
            conn.execute(
                "INSERT INTO questions VALUES('q',?,?,'item/commandExecution/requestApproval',NULL,'pending',?)",
                (row["id"], "{}", time.time() - 61),
            )
        else:
            db.event(conn, activity, {"turnId": row["turn_id"]}, execution_id=row["id"])
    await SilentObserver(db).observe({row["id"]: chat.server})
    assert not faults.metadata and len(faults.calls) == 1
    assert db.one("SELECT status FROM executions WHERE id=?", (row["id"],))["status"] == "running"
    await chat.close()


@pytest.mark.asyncio
async def test_silent_worker_completion_uses_owned_runner_cleanup(system):
    db, registry, chat, settings = system
    faults = Faults(["hold"])
    workers = Workers(db, registry, settings, faults.factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    project = registry.list()[0]
    task = scheduler.tasks.add(project["id"], "Silent worker", chat.conversation(project["id"])["id"])
    claimed = scheduler.tasks.claim(1)
    workers.launch(claimed)
    await asyncio.sleep(0.02)
    row = db.one("SELECT * FROM executions WHERE id=?", (claimed["lease_owner"],))
    turn = faults.turns[row["thread_id"]][0]
    turn["status"] = "completed"
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET started_at=? WHERE id=?", (time.time() - 61, row["id"]))
        conn.execute("UPDATE events SET created_at=? WHERE execution_id=?", (time.time() - 61, row["id"]))
    await scheduler.observer.observe(workers.servers)
    await settle(workers)
    assert db.one("SELECT lease_owner,lifecycle FROM tasks WHERE id=?", (task["id"],)) == {
        "lease_owner": None,
        "lifecycle": "completed",
    }
    assert len(faults.calls) == 1 and not workers.servers


@pytest.mark.asyncio
async def test_history_status_is_execution_authoritative_with_snapshot_cursor(system):
    db, _, chat, _ = system
    receipt = await chat.submit(None, "Status")
    await settle(chat)
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET status='streaming' WHERE role='assistant'")
    message = chat.history()["messages"][-1]
    assert message["status"] == "completed" and message["finished_at"] is not None
    assert (
        message["stream_cursor"]
        == db.one("SELECT max(seq) AS seq FROM events WHERE execution_id=?", (receipt["execution_id"],))["seq"]
    )


def test_schema10_additive_migration_preserves_all_existing_rows(system):
    db, _, _, settings = system
    with db.transaction() as conn:
        conn.execute("DROP TABLE chat_requests")
        conn.execute("DELETE FROM schema_version WHERE version=10")
        conn.execute("INSERT INTO owner VALUES(1,'owner','preserved-password-hash')")
        conn.execute("INSERT INTO sessions VALUES('preserved-session','preserved-csrf',9999999999)")
    tables = [
        r["name"]
        for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")
        if r["name"] not in {"schema_version", "sqlite_sequence"}
    ]
    before = {table: db.all(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}
    migrated = Database(settings.db_path)
    assert before == {table: migrated.all(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}
    assert migrated.one("SELECT max(version) AS version FROM schema_version")["version"] == 12
    assert migrated.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert not migrated.all("PRAGMA foreign_key_check")


def test_concurrent_same_request_has_one_transactional_acceptance(system):
    from concurrent.futures import ThreadPoolExecutor

    db, _, chat, _ = system
    launches = []
    chat.launch = lambda *args: launches.append(args[0])
    request = dict(project_id=None, content="Concurrent request", request_id="concurrent-request-1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: asyncio.run(chat.submit(**request)), range(2)))
    assert receipts[0] == receipts[1]
    assert len(launches) == 1 and len(db.all("SELECT * FROM executions")) == 1
    assert len(db.all("SELECT * FROM chat_requests")) == 1 and len(db.all("SELECT * FROM messages")) == 2


@pytest.mark.asyncio
async def test_accepted_transaction_survives_crash_before_runner_launch(system):
    db, registry, chat, settings = system
    chat.launch = lambda *args: None
    request = dict(project_id=None, content="Accepted before crash", request_id="prelaunch-request-1")
    original = await chat.submit(**request)
    restarted = Chat(Database(settings.db_path), registry, chat.server)
    assert await restarted.submit(**request) == original
    await restarted.recover()
    await settle(restarted)
    assert len(db.all("SELECT * FROM executions")) == 1
    assert restarted.history()["messages"][-1]["status"] == "completed"

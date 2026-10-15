"""Fault injection uses local native-history fixtures and a disposable stdio process."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer
from test_minimal_flow import system as system_fixture

from ai_manager.chat import Chat
from ai_manager.codex import AppServer, CodexError, process_start
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.recovery import classify, delay
from ai_manager.runner import usage_limit

system = system_fixture


class Faults:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.turns = {}
        self.calls = []
        self.metadata = []
        self.resumed = []
        self.reset = None
        self.on_start = None

    def factory(self, settings, native=False):
        return FaultServer(settings, self, native)


class FaultServer(FakeServer):
    def __init__(self, settings, faults, native=False):
        super().__init__(settings)
        self.faults, self.native = faults, native
        self.process = SimpleNamespace(pid=-1)

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.cwd = Path(cwd)
        if thread_id:
            self.faults.resumed.append(thread_id)
        return thread_id or f"thread-{len(self.faults.turns)}-{len(self.faults.calls)}"

    async def request(self, method, params, timeout=30):
        f = self.faults
        if method == "thread/read":
            f.metadata.append(method)
            return {"thread": {"turns": f.turns.get(params["threadId"], [])}}
        if method == "account/rateLimits/read":
            f.metadata.append(method)
            return {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": f.reset}}}
        if method != "turn/start":
            return {}
        prompt = json.loads(params["input"][0]["text"])
        f.calls.append(prompt)
        self.prompts.append(params["input"][0]["text"])
        if f.on_start:
            await f.on_start(self, params, prompt)
        outcome = f.outcomes.pop(0) if f.outcomes else "success"
        turn_id = f"turn-{len(f.calls)}"
        error = {
            "network": {
                "message": "Temporary disconnect",
                "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": None}},
            },
            "usage": {"message": "Account unavailable", "codexErrorInfo": "usageLimitExceeded"},
            "permanent": {"message": "Invalid model configuration", "codexErrorInfo": "badRequest"},
        }.get(outcome)
        text = f"Result {len(f.calls)}"
        turn = {
            "id": turn_id,
            "status": "failed" if error else "completed",
            "error": error,
            "items": [
                {"type": "userMessage", "clientId": params.get("clientUserMessageId"), "content": params["input"]},
                {"type": "agentMessage", "text": text},
            ],
        }
        f.turns.setdefault(params["threadId"], []).append(turn)
        if outcome == "lost_completed":
            raise TimeoutError("turn/start response lost")
        queue = self.queues[params["threadId"]]
        if outcome in {"transport", "hold"}:
            turn["status"] = "interrupted"
            if outcome == "transport":
                queue.put_nowait({"method": "transport/lost", "params": {}})
        else:
            queue.put_nowait({"method": "item/agentMessage/delta", "params": {"turnId": turn_id, "delta": text}})
            queue.put_nowait({"method": "turn/completed", "params": {"turn": turn}})
        return {"turn": {"id": turn_id}}


async def settle(*components):
    for component in components:
        if component.jobs:
            await asyncio.wait_for(asyncio.gather(*list(component.jobs.values())), 5)
    await asyncio.sleep(0)


def eligible(db):
    # Advance persisted eligibility, never actual account/network conditions.
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET retry_at=0 WHERE status='recovering'")


def task_in(system, objective="Work", project_index=0):
    db, registry, chat, _ = system
    project = registry.list()[project_index]
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], objective, origin)
    return task, project, origin


@pytest.mark.asyncio
async def test_manager_outage_before_and_after_worker_delivery_reuses_response(system):
    db, registry, chat, settings = system
    task, _, origin = task_in(system)
    workers = Core(db, registry, settings, Pipeline(db, registry), Faults().factory)
    workers.launch(Tasks(db).claim())
    await settle(workers)
    faults = Faults(["network", "success"])
    chat.server = faults.factory(settings)
    await chat.deliver_results()
    await settle(chat)
    result = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    reply_id = result["delivery_execution"]
    for _ in range(5):
        await chat.deliver_results()
        await chat.recover()
    assert len(faults.calls) == 1
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1
    assert faults.calls[0]["worker_result"]["execution_id"] == result["id"]
    queued = await chat.submit(task["project_id"], "Another request")
    assert queued["status"] == "queued" and len(faults.calls) == 1
    restored = Chat(Database(db.path), registry, faults.factory(settings))
    eligible(db)
    await restored.recover()
    await settle(restored)
    assert db.one("SELECT * FROM executions WHERE id=?", (result["id"],))["delivery"] == "delivered"
    assert db.one("SELECT * FROM executions WHERE id=?", (reply_id,))["status"] == "completed"
    assert len(db.all("SELECT * FROM messages WHERE role='assistant' AND status='completed'")) == 1
    assert len(db.all("SELECT * FROM conversations")) == 1
    assert restored.history(task["project_id"])["conversation"]["id"] == origin
    await restored.recover()
    await settle(restored)
    assert db.one("SELECT status FROM executions WHERE id=?", (queued["execution_id"],))["status"] == "completed"


@pytest.mark.asyncio
async def test_manager_lost_completed_reply_reconciles_without_second_model_call(system):
    db, registry, chat, settings = system
    faults = Faults(["lost_completed"])
    chat.server = faults.factory(settings)
    request = await chat.submit(registry.list()[0]["id"], "Authorized work")
    await settle(chat)
    eligible(db)
    await chat.recover()
    assert len(faults.calls) == 1 and not chat.jobs
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    assert row["status"] == "completed" and row["output"] == "Result 1"
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1


@pytest.mark.asyncio
async def test_five_queued_tasks_run_in_order_through_a_manager_interruption(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    faults = Faults(["success", "success", "network", "success", "success", "success", "success"])
    workers_faults = Faults()
    workers = Core(db, registry, settings, Pipeline(db, registry), workers_faults.factory)
    chat.server = faults.factory(settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action

    async def chain(server, params, prompt):
        # The whole ordered plan is queued at once; result notices cannot start more work.
        if "worker_result" not in prompt:
            handler = server.tool_handlers[params["threadId"]]
            for step in range(1, 6):
                await handler({"tool": "manager_action", "callId": f"call-{step}",
                               "arguments": {"action": "delegate", "objective": f"Step {step}"}})

    faults.on_start = chain
    request = await chat.submit(project["id"], "Run five authorized assignments in order")
    await settle(chat)
    origin = db.one("SELECT conversation_id FROM executions WHERE id=?", (request["execution_id"],))["conversation_id"]
    for _ in range(60):
        await scheduler.tick()
        await settle(workers, chat)
        if db.one("SELECT 1 FROM executions WHERE status='recovering'"):
            # Recover an actual backend restart between assignments, not a new user turn.
            eligible(db)
            chat = Chat(Database(db.path), registry, faults.factory(settings))
            chat.action_handler = scheduler.manager_action
            scheduler.chat = chat
        if db.one("SELECT count(*) AS n FROM executions WHERE delivery='delivered'")["n"] == 5:
            break
    assert [p["objective"] for p in workers_faults.calls] == [f"Step {i}" for i in range(1, 6)]
    assert len(Tasks(db).list()) == 5
    assert db.one("SELECT count(*) AS n FROM executions WHERE delivery='delivered'")["n"] == 5
    assert len(db.all("SELECT * FROM messages WHERE role='worker'")) == 5
    assert len(db.all("SELECT * FROM messages WHERE role='assistant' AND status='completed'")) == 6
    assert all(t["origin_conversation"] == origin for t in Tasks(db).list())
    assert len(db.all("SELECT * FROM executions")) == 11  # Healthy turns plus retry in the same execution.


@pytest.mark.asyncio
async def test_permanent_manager_error_preserves_completion_and_stops_inference(system):
    db, registry, chat, settings = system
    task, _, _ = task_in(system)
    workers = Core(db, registry, settings, Pipeline(db, registry), Faults().factory)
    workers.launch(Tasks(db).claim())
    await settle(workers)
    faults = Faults(["permanent"])
    chat.server = faults.factory(settings)
    await chat.deliver_results()
    await settle(chat)
    for _ in range(4):
        await chat.recover()
        await chat.deliver_results()
    assert len(faults.calls) == 1
    assert db.one("SELECT delivery FROM executions WHERE task_id=?", (task["id"],))["delivery"] == "blocked"
    assert chat.history(task["project_id"])["messages"][-1]["execution_error"] == "Invalid model configuration"


@pytest.mark.parametrize(
    "error,kind",
    [
        ({"codexErrorInfo": "usageLimitExceeded"}, None),
        ({"codexErrorInfo": "rateLimitExceeded"}, None),
        ({"status": 429, "retryAfter": 86400}, None),
        ({"message": "You have reached your usage limit; resets tomorrow"}, None),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}}, "connection"),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}, None),
        ({"codexErrorInfo": "badRequest", "message": "rate limit is not valid"}, None),
        ({"codexErrorInfo": "unauthorized"}, None),
        ({"codexErrorInfo": "contextWindowExceeded"}, None),
        ({"code": -32602}, None),
        (ValueError("Bad objective"), None),
        (TimeoutError(), "connection"),
        (CodexError("RPC quota", {"code": -32000, "data": {"codexErrorInfo": "usageLimitExceeded"}}), None),
    ],
)
@pytest.mark.smoke
def test_structured_classification(error, kind):
    failure = classify(error)
    assert (failure.kind if failure else None) == kind


def test_chat_backoff_bounds_authoritative_retry_after_and_usage_limit_detection():
    assert 4 <= delay(1, "connection") <= 5
    assert 240 <= delay(99, "connection") <= 300
    before = time.time()
    failure = classify({"codexErrorInfo": "httpConnectionFailed", "retryAfter": 30})
    assert before + 30 <= failure.retry_at <= time.time() + 30
    assert usage_limit(CodexError("limited", {"data": {"codexErrorInfo": "usageLimitExceeded", "retryAfter": 3600}}))
    assert usage_limit({"status": 429})
    assert usage_limit({"message": "You have reached your usage limit"})
    assert not usage_limit({"codexErrorInfo": "badRequest", "message": "rate limit is not valid"})
    assert not usage_limit(ValueError("Bad objective"))


@pytest.mark.asyncio
async def test_graceful_manager_shutdown_and_startup_resume_original_request(system):
    db, registry, chat, settings = system
    faults = Faults(["hold", "success"])
    chat.server = faults.factory(settings)
    request = await chat.submit(registry.list()[0]["id"], "Keep the original request")
    await asyncio.sleep(0.01)
    await chat.close()
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    assert row["status"] == "recovering" and row["finished_at"] is None
    restored = Chat(Database(db.path), registry, faults.factory(settings))
    eligible(db)
    await restored.recover()
    await settle(restored)
    assert db.one("SELECT status FROM executions WHERE id=?", (request["execution_id"],))["status"] == "completed"
    assert faults.calls[-1]["message"] == "Keep the original request"
    assert len(db.all("SELECT * FROM messages WHERE role='user'")) == 1
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1


@pytest.mark.asyncio
async def test_manager_failure_before_server_initialization_keeps_request_durable(system, monkeypatch):
    db, registry, chat, settings = system

    async def offline():
        raise TimeoutError("Connection timeout before model catalog")

    monkeypatch.setattr(chat.server, "start", offline)
    request = await chat.submit(registry.list()[0]["id"], "Original request")
    await settle(chat)
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    assert row["status"] == "recovering" and row["thread_id"] is None
    assert len(db.all("SELECT * FROM messages")) == 2
    chat.server = Faults().factory(settings)
    eligible(db)
    await chat.recover()
    await settle(chat)
    assert chat.history(registry.list()[0]["id"])["messages"][-1]["status"] == "completed"
    assert len(db.all("SELECT * FROM messages")) == 2


@pytest.mark.asyncio
async def test_migration_reconciles_older_launch(system):
    db, registry, chat, settings = system
    request = await chat.submit(registry.list()[0]["id"], "Discuss only")
    await settle(chat)
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    with db.transaction() as conn:
        conn.execute("DELETE FROM schema_version WHERE version=9")
        conn.execute(
            "UPDATE executions SET finished_at=NULL,status='starting',turn_id=NULL,launch_key=NULL,launch_input=NULL,pid=NULL WHERE id=?",
            (row["id"],),
        )
        conn.execute("UPDATE messages SET status='streaming' WHERE id=?", (request["message_id"],))
    faults = Faults()
    prompt = db.one(
        "SELECT payload FROM events WHERE execution_id=? AND kind='model.context' ORDER BY seq DESC LIMIT 1",
        (row["id"],),
    )
    faults.turns[row["thread_id"]] = [
        {
            "id": "old-completed",
            "status": "completed",
            "items": [
                {
                    "type": "userMessage",
                    "clientId": None,
                    "content": [{"type": "text", "text": json.dumps(json.loads(prompt["payload"])["prompt"])}],
                },
                {"type": "agentMessage", "text": "Actual saved discussion"},
            ],
        }
    ]
    restored_db = Database(db.path)
    restored = Chat(restored_db, registry, faults.factory(settings))
    await restored.recover()
    migrated = restored_db.one("SELECT * FROM executions WHERE id=?", (row["id"],))
    assert migrated["status"] == "completed"
    assert migrated["turn_id"] == "old-completed" and not faults.calls


@pytest.mark.asyncio
async def test_transport_preserves_rpc_details_and_account_notifications(system):
    _, _, _, settings = system
    server = AppServer(settings)
    future = asyncio.get_running_loop().create_future()
    server.pending[7] = future
    server.queues["native"] = asyncio.Queue()
    stream = asyncio.StreamReader()
    details = {
        "code": -32000,
        "message": "limited",
        "data": {"codexErrorInfo": "usageLimitExceeded", "retryAfter": 3600},
    }
    stream.feed_data(
        (
            json.dumps(
                {
                    "method": "account/rateLimits/updated",
                    "params": {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": time.time() + 3600}}},
                }
            )
            + "\n"
        ).encode()
    )
    stream.feed_data((json.dumps({"id": 7, "error": details}) + "\n").encode())
    stream.feed_eof()
    await server._read(SimpleNamespace(stdout=stream))
    with pytest.raises(CodexError) as error:
        await future
    assert error.value.details == details
    assert classify(error.value) is None
    assert server.rate_limits["rateLimits"]["primary"]["usedPercent"] == 100
    assert (await server.queues["native"].get())["method"] == "transport/lost"


@pytest.mark.asyncio
async def test_recovery_launch_markers_are_unique_across_hard_crashes(system):
    from ai_manager.recovery import launching

    db, _, _, _ = system
    task, _, _ = task_in(system)
    claim = Tasks(db).claim()
    first = launching(db, claim["lease_owner"], "same prompt")
    second = launching(db, claim["lease_owner"], "same prompt")
    assert first != second
    assert db.one("SELECT launch_key FROM executions WHERE id=?", (claim["lease_owner"],))["launch_key"] == second
    assert Tasks(db).get(task["id"])["lease_owner"] == claim["lease_owner"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager"])
async def test_delayed_completion_from_previous_native_turn_cannot_finish_retry(system, role):
    db, registry, chat, settings = system
    faults = Faults(["network", "success"])

    async def stale_completion(server, params, prompt):
        if len(faults.calls) == 2:
            server.queues[params["threadId"]].put_nowait(
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": "turn-1",
                            "status": "failed",
                            "error": {"codexErrorInfo": "badRequest", "message": "stale turn"},
                        }
                    },
                }
            )

    faults.on_start = stale_completion
    if role == "worker":
        task, _, _ = task_in(system)
        component = Core(db, registry, settings, Pipeline(db, registry), faults.factory)
        component.launch(Tasks(db).claim())
    else:
        chat.server = faults.factory(settings)
        component = chat
        await chat.submit(registry.list()[0]["id"], "Original request")
    await settle(component)
    eligible(db)
    await component.recover()
    await settle(component)
    assert len(faults.calls) == 2
    row = db.one("SELECT * FROM executions WHERE agent_id=?", ("execution" if role == "worker" else "manager",))
    assert row["status"] == "completed" and row["turn_id"] == "turn-2" and row["output"] == "Result 2"
    assert row["error"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager"])
async def test_notification_only_usage_error_is_reported_without_blocking_after_restart(system, role):
    db, registry, chat, settings = system
    faults = Faults(["usage", "success"])

    class NotificationOnly(FaultServer):
        async def request(self, method, params, timeout=30):
            result = await super().request(method, params, timeout)
            if method == "turn/start" and len(faults.calls) == 1:
                queue = self.queues[params["threadId"]]
                events = []
                while not queue.empty():
                    events.append(queue.get_nowait())
                for event in events:
                    if event["method"] == "turn/completed":
                        turn = event["params"]["turn"]
                        queue.put_nowait(
                            {
                                "method": "error",
                                "params": {"turnId": turn["id"], "error": turn["error"], "willRetry": False},
                            }
                        )
                        turn["error"] = None
                    queue.put_nowait(event)
            return result

    def factory(settings, native=False):
        return NotificationOnly(settings, faults, native)

    if role == "worker":
        task, _, _ = task_in(system)
        component = Core(db, registry, settings, Pipeline(db, registry), factory)
        component.launch(Tasks(db).claim())
    else:
        chat.server = factory(settings)
        component = chat
        await chat.submit(registry.list()[0]["id"], "Preserve original request")
    await settle(component)
    row = db.one("SELECT * FROM executions WHERE agent_id=?", ("execution" if role == "worker" else "manager",))
    assert row["status"] == "failed" and row["finished_at"] is not None
    eligible(db)
    component = (
        Core(Database(db.path), registry, settings, Pipeline(Database(db.path), registry), factory)
        if role == "worker"
        else Chat(Database(db.path), registry, factory(settings))
    )
    await component.recover()
    await settle(component)
    assert db.one("SELECT status FROM executions WHERE id=?", (row["id"],))["status"] == "failed"
    assert len(faults.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager"])
async def test_native_internal_retry_success_does_not_schedule_application_retry(system, role):
    db, registry, chat, settings = system
    faults = Faults(["success"])

    async def retry_notification(server, params, prompt):
        server.queues[params["threadId"]].put_nowait(
            {
                "method": "error",
                "params": {
                    "turnId": "turn-1",
                    "willRetry": True,
                    "error": {"message": "Native retry handled it", "codexErrorInfo": "internalServerError"},
                },
            }
        )

    faults.on_start = retry_notification
    if role == "worker":
        task, _, _ = task_in(system)
        component = Core(db, registry, settings, Pipeline(db, registry), faults.factory)
        component.launch(Tasks(db).claim())
    else:
        chat.server = faults.factory(settings)
        component = chat
        await chat.submit(registry.list()[0]["id"], "Healthy native retry")
    await settle(component)
    row = db.one("SELECT * FROM executions WHERE agent_id=?", ("execution" if role == "worker" else "manager",))
    assert row["status"] == "completed" and row["retry_count"] == 0 and row["error"] == ""
    assert len(faults.calls) == 1 and not db.all("SELECT * FROM events WHERE kind='recovery.scheduled'")


@pytest.mark.asyncio
async def test_close_reaps_owned_group_children_after_app_server_parent_death(system, tmp_path):
    import sys

    _, _, _, settings = system
    ready = tmp_path / "child.pid"
    program = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    server = AppServer(settings)
    server.process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", program, str(ready), start_new_session=True
    )
    server.process_identity = process_start(server.process.pid)
    try:
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        child_pid = int(ready.read_text())
        assert os.getpgid(child_pid) == server.process.pid
        os.kill(server.process.pid, signal.SIGKILL)
        await server.process.wait()
        assert process_start(child_pid) is not None
        await server.close()
        for _ in range(100):
            if process_start(child_pid) is None:
                break
            await asyncio.sleep(0.01)
        assert process_start(child_pid) is None, "Owned command child survived its App Server and could still write"
    finally:
        try:
            os.killpg(server.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await server.process.wait()


@pytest.mark.asyncio
async def test_close_never_signals_recycled_parent_pid(system, monkeypatch):
    _, _, _, settings = system
    server = AppServer(settings)
    server.process = SimpleNamespace(pid=12345, returncode=0)
    server.process_identity = "original-birth"
    monkeypatch.setattr("ai_manager.codex.process_start", lambda pid: "recycled-birth")

    def unexpected_signal(*args):
        raise AssertionError("Unrelated recycled process group was signalled")

    monkeypatch.setattr("ai_manager.codex.os.killpg", unexpected_signal)
    await server.close()

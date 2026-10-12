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
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.recovery import GATE, Failure, classify, defer, delay, reserve
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

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
        row = conn.execute("SELECT value FROM settings WHERE key=?", (GATE,)).fetchone()
        if row:
            gate = json.loads(row["value"])
            gate["retry_at"] = 0
            conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(gate), GATE))


def task_in(system, objective="Work", project_index=0):
    db, registry, chat, _ = system
    project = registry.list()[project_index]
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], objective, origin)
    return task, project, origin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes", [("network", "success"), ("transport", "success"), ("network", "network", "network", "success")]
)
async def test_network_recovery_same_task_thread_history_no_waiting_inference(system, outcomes):
    db, registry, chat, settings = system
    task, project, origin = task_in(system)
    faults = Faults(outcomes)
    workers = Workers(db, registry, settings, faults.factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    await scheduler.tick()
    await settle(workers)
    original = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert original["status"] == "recovering" and original["finished_at"] is None
    assert Tasks(db).get(task["id"])["blocker"] == "Waiting for connection"
    # Partial changes must survive every attempt and database reopen.
    source = Path(project["path"]) / "partial.txt"
    source.write_text("preserve owner and partial worker changes")
    for _ in range(4):
        await scheduler.tick()
    assert len(faults.calls) == 1 and not chat.jobs
    while faults.outcomes:
        eligible(db)
        await scheduler.tick()
        await settle(workers, chat)
    await scheduler.tick()
    await settle(chat)
    result = db.one("SELECT * FROM executions WHERE id=?", (original["id"],))
    assert result["delivery"] == "delivered"
    assert len(Tasks(db).list()) == 1 and Tasks(db).get(task["id"])["status"] == "completed"
    assert source.read_text() == "preserve owner and partial worker changes"
    assert len(set(faults.resumed)) == 1 and set(faults.resumed) == {original["thread_id"]}
    assert len(db.all("SELECT * FROM messages WHERE role='worker'")) == 1
    assert len(chat.server.prompts) == 1
    assert chat.history(project["id"])["conversation"]["id"] == origin
    assert all("recovery" in p for p in faults.calls[1:])


@pytest.mark.asyncio
async def test_usage_reset_persisted_shared_gate_one_probe_and_manager_wait(system):
    db, registry, chat, settings = system
    first, _, _ = task_in(system)
    faults = Faults(["usage", "hold"])
    faults.reset = time.time() + 3600
    workers = Workers(db, registry, settings, faults.factory)
    workers.launch(Tasks(db).claim(2))
    await settle(workers)
    row = db.one("SELECT * FROM executions WHERE task_id=?", (first["id"],))
    assert row["retry_at"] == faults.reset
    second, other, _ = task_in(system, "Other project", 1)
    assert Tasks(db).claim(2) is None
    response = await chat.submit(other["id"], "Authorized request while limited", mode="execute")
    await settle(chat)
    assert not chat.server.prompts
    for _ in range(5):
        await workers.recover()
        await chat.recover()
        assert Tasks(db).claim(2) is None
    assert len(faults.calls) == 1
    reopened = Database(db.path)
    assert reopened.setting(GATE)["retry_at"] == faults.reset
    eligible(db)
    await workers.recover()
    await asyncio.sleep(0.01)
    await chat.recover()
    assert len(faults.calls) == 2 and not chat.jobs and Tasks(db).claim(2) is None
    assert not chat.server.prompts  # The single probe is still active.
    # Complete the probe without an additional inference request.
    server = next(iter(workers.servers.values()))
    saved = faults.turns[row["thread_id"]][-1]
    saved["status"] = "completed"
    server.queues[row["thread_id"]].put_nowait({"method": "turn/completed", "params": {"turn": saved}})
    await settle(workers)
    assert db.setting(GATE) is None
    eligible(db)
    await chat.recover()
    await settle(chat)
    assert db.one("SELECT * FROM executions WHERE id=?", (response["execution_id"],))["status"] == "completed"
    assert db.one("SELECT * FROM executions WHERE id=?", (response["execution_id"],))["mode"] == "execute"
    assert Tasks(db).claim(2)["id"] == second["id"]


@pytest.mark.asyncio
async def test_usage_without_reset_increases_bounded_delay_without_claiming_reset(system):
    db, registry, chat, settings = system
    task, _, _ = task_in(system)
    faults = Faults(["usage", "usage", "success"])
    workers = Workers(db, registry, settings, faults.factory)
    workers.launch(Tasks(db).claim(2))
    await settle(workers)
    first = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert 230 < first["retry_at"] - time.time() <= 300
    assert db.setting(GATE)["known_reset"] is None
    eligible(db)
    await workers.recover()
    await settle(workers)
    second = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert second["retry_count"] > first["retry_count"]
    assert second["retry_at"] - time.time() > 450
    eligible(db)
    await workers.recover()
    await settle(workers)
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert db.setting(GATE) is None


@pytest.mark.asyncio
async def test_crash_reconciles_unfinished_native_turn_and_preserves_partial_work(system):
    db, registry, _, settings = system
    task, project, _ = task_in(system)
    claim = Tasks(db).claim(2)
    partial = Path(project["path"]) / "partial.txt"
    partial.write_text("completed before backend crash")
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET native_thread='native' WHERE id=?", (task["id"],))
        conn.execute(
            "UPDATE executions SET thread_id='native',turn_id='interrupted',status='running',pid=999999999,process_start='dead' WHERE id=?",
            (claim["lease_owner"],),
        )
    faults = Faults(["success"])
    faults.turns["native"] = [{"id": "interrupted", "status": "inProgress", "items": []}]
    restored = Workers(Database(db.path), registry, settings, faults.factory)
    await restored.recover()
    await settle(restored)
    assert partial.read_text() == "completed before backend crash"
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert faults.resumed == ["native"]
    assert len(db.all("SELECT * FROM executions WHERE task_id=?", (task["id"],))) == 1


@pytest.mark.asyncio
async def test_lost_start_reply_completed_native_outcome_is_not_executed_twice(system):
    db, registry, chat, settings = system
    task, _, _ = task_in(system)
    faults = Faults(["lost_completed"])
    workers = Workers(db, registry, settings, faults.factory)
    workers.launch(Tasks(db).claim(2))
    await settle(workers)
    original = db.one("SELECT * FROM executions WHERE task_id=?", (task["id"],))
    assert original["turn_id"] is None and original["launch_key"]
    eligible(db)
    await workers.recover()
    assert not workers.jobs and len(faults.calls) == 1
    assert Tasks(db).get(task["id"])["status"] == "completed"
    await asyncio.gather(chat.deliver_results(), chat.deliver_results())
    await settle(chat)
    workers.finish(original["id"], "failed", "duplicate completion", "ignored")
    await chat.deliver_results()
    assert len(chat.server.prompts) == 1
    assert len(db.all("SELECT * FROM messages WHERE role='worker'")) == 1


@pytest.mark.asyncio
async def test_manager_outage_before_and_after_worker_delivery_reuses_response(system):
    db, registry, chat, settings = system
    task, _, origin = task_in(system)
    workers = Workers(db, registry, settings, Faults().factory)
    workers.launch(Tasks(db).claim(2))
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
    request = await chat.submit(registry.list()[0]["id"], "Authorized work", mode="conversation")
    await settle(chat)
    eligible(db)
    await chat.recover()
    assert len(faults.calls) == 1 and not chat.jobs
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    assert row["status"] == "completed" and row["output"] == "Result 1"
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1


@pytest.mark.asyncio
async def test_five_sequential_assignments_resume_in_order_after_manager_interruption(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    faults = Faults(["success", "success", "network", "success", "success", "success", "success"])
    workers_faults = Faults()
    workers = Workers(db, registry, settings, workers_faults.factory)
    chat.server = faults.factory(settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action

    async def chain(server, params, prompt):
        handler = server.tool_handlers[params["threadId"]]
        if "worker_result" in prompt:
            notice = prompt["worker_result"]
            result = await handler({"tool": "manager_action", "arguments": {"action": "read_result", "execution_id": notice["execution_id"]}})
            step = int(json.loads(result["input"])["objective"].split()[-1]) + 1
        else:
            assert prompt["message"] == "Run five authorized assignments in order"
            step = 1
        if step <= 5:
            await handler(
                {
                    "tool": "manager_action",
                    "callId": f"call-{len(faults.calls)}",
                    "arguments": {"action": "delegate", "objective": f"Step {step}"},
                }
            )

    faults.on_start = chain
    request = await chat.submit(project["id"], "Run five authorized assignments in order")
    await settle(chat)
    origin = db.one("SELECT conversation_id FROM executions WHERE id=?", (request["execution_id"],))["conversation_id"]
    for _ in range(30):
        await scheduler.tick()
        await settle(workers, chat)
        if db.one("SELECT 1 FROM executions WHERE status='recovering'"):
            # Recover an actual backend restart between assignments, not a new user turn.
            eligible(db)
            chat = Chat(Database(db.path), registry, faults.factory(settings))
            chat.action_handler = scheduler.manager_action
            scheduler.chat = chat
        if len(Tasks(db).list()) == 5 and all(t["status"] == "completed" for t in Tasks(db).list()):
            await scheduler.tick()
            await settle(chat)
            break
    assert [p["objective"] for p in workers_faults.calls] == [f"Step {i}" for i in range(1, 6)]
    assert len(Tasks(db).list()) == 5
    assert db.one("SELECT count(*) AS n FROM executions WHERE delivery='delivered'")["n"] == 5
    assert len(db.all("SELECT * FROM messages WHERE role='worker'")) == 5
    assert len(db.all("SELECT * FROM messages WHERE role='assistant' AND status='completed'")) == 6
    assert all(t["origin_conversation"] == origin for t in Tasks(db).list())
    assert len(db.all("SELECT * FROM executions")) == 11  # Healthy turns plus retry in the same execution.


@pytest.mark.asyncio
async def test_permanent_failure_reports_once_without_retry_or_new_task(system):
    db, registry, chat, settings = system
    task, _, _ = task_in(system)
    faults = Faults(["permanent"])
    workers = Workers(db, registry, settings, faults.factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    await scheduler.tick()
    await settle(workers)
    for _ in range(4):
        await scheduler.tick()
        await settle(chat)
    assert Tasks(db).get(task["id"])["status"] == "failed"
    assert len(faults.calls) == 1 and len(chat.server.prompts) == 1
    assert len(Tasks(db).list()) == 1
    assert not db.all("SELECT * FROM executions WHERE status='recovering'")


@pytest.mark.asyncio
async def test_permanent_manager_error_preserves_completion_and_stops_inference(system):
    db, registry, chat, settings = system
    task, _, _ = task_in(system)
    workers = Workers(db, registry, settings, Faults().factory)
    workers.launch(Tasks(db).claim(2))
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


@pytest.mark.asyncio
async def test_waiting_project_does_not_consume_capacity_or_allow_second_writer(system):
    db, registry, chat, settings = system
    settings.max_workers = 1
    task, first, _ = task_in(system)
    faults = Faults(["network", "success", "success"])
    workers = Workers(db, registry, settings, faults.factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    await scheduler.tick()
    await settle(workers)
    Tasks(db).add(first["id"], "Later in same project")
    other_task, other, _ = task_in(system, "Independent", 1)
    await scheduler.tick()
    await settle(workers, chat)
    assert Tasks(db).get(other_task["id"])["status"] == "completed"
    assert Tasks(db).get(task["id"])["status"] == "waiting"
    assert len(faults.calls) == 2
    assert faults.calls[1]["objective"] == "Independent"
    eligible(db)
    await scheduler.tick()
    await settle(workers, chat)
    assert Tasks(db).get(task["id"])["status"] == "completed"
    await scheduler.tick()
    await settle(workers, chat)
    assert [p["objective"] for p in faults.calls] == ["Work", "Independent", "Work", "Later in same project"]
    assert other["id"] != first["id"]


@pytest.mark.parametrize(
    "error,kind",
    [
        ({"codexErrorInfo": "usageLimitExceeded"}, "usage"),
        ({"codexErrorInfo": "rateLimitExceeded"}, "usage"),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}}, "connection"),
        ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}, None),
        ({"codexErrorInfo": "badRequest", "message": "rate limit is not valid"}, None),
        ({"codexErrorInfo": "unauthorized"}, None),
        ({"codexErrorInfo": "contextWindowExceeded"}, None),
        ({"code": -32602}, None),
        (ValueError("Bad objective"), None),
        (TimeoutError(), "connection"),
        (CodexError("RPC quota", {"code": -32000, "data": {"codexErrorInfo": "usageLimitExceeded"}}), "usage"),
    ],
)
def test_structured_classification(error, kind):
    failure = classify(error)
    assert (failure.kind if failure else None) == kind


def test_backoff_bounds_and_authoritative_retry_after(system):
    assert 4 <= delay(1, "connection") <= 5
    assert 240 <= delay(99, "connection") <= 300
    assert 17280 <= delay(99, "usage") <= 21600
    before = time.time()
    failure = classify({"codexErrorInfo": "rateLimitExceeded", "retryAfter": 7200})
    assert before + 7200 <= failure.retry_at <= time.time() + 7200
    db, _, _, _ = system
    task, _, _ = task_in(system)
    claim = Tasks(db).claim(2)
    defer(db, claim["lease_owner"], Failure("usage", before + 30))
    assert Tasks(db).get(task["id"])["retry_at"] == before + 30  # Authoritative short reset wins over fallback.
    assert not reserve(db, "another-execution")


@pytest.mark.asyncio
async def test_actual_owned_app_server_process_termination_recovers(system, tmp_path):
    db, registry, chat, settings = system
    task, project, _ = task_in(system)
    # A local protocol-only process: no network, credentials or real model access.
    script = tmp_path / "fake-app-server"
    store = tmp_path / "native.json"
    partial = Path(project["path"]) / "partial.txt"
    script.write_text(
        """#!/usr/bin/env python3
import json, sys
from pathlib import Path
store = Path(STORE)
partial = Path(PARTIAL)
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method, params = message['method'], message['params']
    result = {}
    if method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'model/list':
        result = {'data': [{'model':'test-model','isDefault':True,'supportedReasoningEfforts': [{'reasoningEffort':'medium'}], 'defaultReasoningEffort':'medium'}]}
    elif method in ('thread/start', 'thread/resume'):
        result = {'thread': {'id': 'native-process'}}
    elif method == 'thread/read':
        result = {'thread': {'turns': json.loads(store.read_text()) if store.exists() else []}}
    elif method == 'turn/start':
        previous = json.loads(store.read_text()) if store.exists() else []
        turn = {'id':'turn-'+str(len(previous)), 'status':'inProgress', 'items': [{'type':'userMessage', 'clientId':params['clientUserMessageId'], 'content':params['input']}]}
        result = {'turn': turn}
        if not previous:
            partial.write_text('partial changes survive process death')
            store.write_text(json.dumps([turn]))
        else:
            assert partial.read_text() == 'partial changes survive process death'
            turn['status'] = 'completed'
            turn['items'].append({'type':'agentMessage','text':'Preserved partial work and completed'})
            store.write_text(json.dumps(previous+[turn]))
            send({'id':message['id'],'result':result})
            send({'method':'item/agentMessage/delta','params':{'threadId':'native-process','turnId':turn['id'],'delta':'Preserved partial work and completed'}})
            send({'method':'turn/completed','params':{'threadId':'native-process','turn':turn}})
            continue
    send({'id':message['id'], 'result':result})
""".replace("STORE", repr(str(store))).replace("PARTIAL", repr(str(partial)))
    )
    script.chmod(0o700)
    settings.codex_bin = str(script)
    workers = Workers(db, registry, settings)
    claim = Tasks(db).claim(2)
    workers.launch(claim)
    try:
        for _ in range(200):
            row = db.one("SELECT * FROM executions WHERE id=?", (claim["lease_owner"],))
            if row["turn_id"]:
                break
            await asyncio.sleep(0.01)
        assert row["turn_id"] and process_start(row["pid"]) == row["process_start"]
        os.killpg(row["pid"], signal.SIGTERM)  # Only the dedicated disposable fixture process.
        await settle(workers)
        assert Tasks(db).get(task["id"])["status"] == "waiting"
        eligible(db)
        await workers.recover()
        await settle(workers)
        assert Tasks(db).get(task["id"])["status"] == "completed"
        assert partial.read_text() == "partial changes survive process death"
        assert len(json.loads(store.read_text())) == 2
        await chat.deliver_results()
        await settle(chat)
        assert len(chat.server.prompts) == 1
    finally:
        await workers.close()


@pytest.mark.asyncio
async def test_graceful_manager_shutdown_and_startup_resume_original_request(system):
    db, registry, chat, settings = system
    faults = Faults(["hold", "success"])
    chat.server = faults.factory(settings)
    request = await chat.submit(registry.list()[0]["id"], "Keep the original request", mode="execute")
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
    assert faults.calls[-1]["mode"] == "execute"
    assert len(db.all("SELECT * FROM messages WHERE role='user'")) == 1
    assert len(db.all("SELECT * FROM messages WHERE role='assistant'")) == 1


@pytest.mark.asyncio
async def test_explicit_worker_stop_does_not_autoretry(system):
    db, registry, _, settings = system
    task, _, _ = task_in(system)
    faults = Faults(["network"])
    workers = Workers(db, registry, settings, faults.factory)
    claim = Tasks(db).claim(2)
    workers.launch(claim)
    await settle(workers)
    await workers.interrupt(claim["lease_owner"])
    eligible(db)
    await workers.recover()
    assert not workers.jobs and len(faults.calls) == 1
    assert Tasks(db).get(task["id"])["blocker"] == "Stopped by user"
    assert Tasks(db).get(task["id"])["lease_owner"] is None


@pytest.mark.asyncio
async def test_recovery_respects_paused_project_and_capacity(system):
    db, registry, chat, settings = system
    task, first, _ = task_in(system)
    faults = Faults(["network", "hold", "success"])
    workers = Workers(db, registry, settings, faults.factory)
    workers.launch(Tasks(db).claim(2))
    await settle(workers)
    registry.update(first["id"], first["revision"], {"status": "paused"})
    eligible(db)
    await workers.recover()
    assert not workers.jobs and len(faults.calls) == 1
    assert Tasks(db).get(task["id"])["status"] == "waiting"
    updated = registry.get(first["id"])
    registry.update(first["id"], updated["revision"], {"status": "active"})
    settings.max_workers = 1
    other, _, _ = task_in(system, "Occupies capacity", 1)
    workers.launch(Tasks(db).claim(1))
    await asyncio.sleep(0.01)
    eligible(db)
    await workers.recover()
    assert len(workers.jobs) == 1 and len(faults.calls) == 2
    assert Tasks(db).get(other["id"])["status"] == "running"
    claim = Tasks(db).get(other["id"])["lease_owner"]
    server = workers.servers[claim]
    turn = faults.turns[Tasks(db).get(other["id"])["native_thread"]][-1]
    turn["status"] = "completed"
    server.queues[Tasks(db).get(other["id"])["native_thread"]].put_nowait(
        {"method": "turn/completed", "params": {"turn": turn}}
    )
    await settle(workers)
    eligible(db)
    await workers.recover()
    await settle(workers)
    assert Tasks(db).get(task["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_manager_failure_before_server_initialization_keeps_request_durable(system, monkeypatch):
    db, registry, chat, settings = system

    async def offline():
        raise TimeoutError("Connection timeout before model catalog")

    monkeypatch.setattr(chat.server, "start", offline)
    request = await chat.submit(registry.list()[0]["id"], "Original request", mode="conversation")
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
async def test_migration_reconciles_older_launch_and_preserves_read_only_mode(system):
    db, registry, chat, settings = system
    request = await chat.submit(registry.list()[0]["id"], "Discuss only", mode="conversation")
    await settle(chat)
    row = db.one("SELECT * FROM executions WHERE id=?", (request["execution_id"],))
    with db.transaction() as conn:
        conn.execute("DELETE FROM schema_version WHERE version=9")
        conn.execute(
            "UPDATE executions SET finished_at=NULL,status='starting',turn_id=NULL,launch_key=NULL,launch_input=NULL,mode='auto',pid=NULL WHERE id=?",
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
    assert migrated["status"] == "completed" and migrated["mode"] == "conversation"
    assert migrated["turn_id"] == "old-completed" and not faults.calls


@pytest.mark.asyncio
async def test_continue_replayed_with_new_call_id_does_not_restart_finished_assignment(system):
    db, registry, chat, settings = system
    task, project, origin = task_in(system)
    Tasks(db).control(task["id"], "pause", task["revision"])
    workers = Workers(db, registry, settings, Faults().factory)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    call = {
        "tool": "manager_action",
        "arguments": {"action": "continue", "task_id": task["id"]},
        "manager_execution_id": "same-manager-response",
        "conversation_id": origin,
        "callId": "first-call",
    }
    await scheduler.manager_action(project["id"], call)
    workers.launch(Tasks(db).claim(2))
    await settle(workers)
    await scheduler.manager_action(project["id"], {**call, "callId": "regenerated-call"})
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert len(db.all("SELECT * FROM executions")) == 1


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
    assert classify(error.value).kind == "usage"
    assert server.rate_limits["rateLimits"]["primary"]["usedPercent"] == 100
    assert (await server.queues["native"].get())["method"] == "transport/lost"


@pytest.mark.asyncio
async def test_recovery_launch_markers_are_unique_across_hard_crashes(system):
    from ai_manager.recovery import launching

    db, _, _, _ = system
    task, _, _ = task_in(system)
    claim = Tasks(db).claim(2)
    first = launching(db, claim["lease_owner"], "same prompt")
    second = launching(db, claim["lease_owner"], "same prompt")
    assert first != second
    assert db.one("SELECT launch_key FROM executions WHERE id=?", (claim["lease_owner"],))["launch_key"] == second
    assert Tasks(db).get(task["id"])["lease_owner"] == claim["lease_owner"]


@pytest.mark.asyncio
async def test_completed_claim_is_retained_until_old_process_is_accounted_for(system):
    db, registry, _, settings = system
    task, project, _ = task_in(system)
    workers = Workers(db, registry, settings, Faults().factory)
    claim = Tasks(db).claim(2)
    workers.finish(claim["lease_owner"], "completed", "Saved completion", "")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE executions SET pid=?,process_start=? WHERE id=?",
            (os.getpid(), process_start(os.getpid()), claim["lease_owner"]),
        )
    await workers.recover()
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert Tasks(db).get(task["id"])["lease_owner"] == claim["lease_owner"]
    Tasks(db).add(project["id"], "Second writer must wait")
    assert Tasks(db).claim(2) is None
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET pid=NULL,retry_at=NULL WHERE id=?", (claim["lease_owner"],))
    await workers.recover()
    assert not workers.jobs
    assert Tasks(db).get(task["id"])["lease_owner"] is None
    assert Tasks(db).claim(2)["description"] == "Second writer must wait"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["worker", "manager"])
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
        component = Workers(db, registry, settings, faults.factory)
        component.launch(Tasks(db).claim(2))
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
@pytest.mark.parametrize("role", ["worker", "manager"])
async def test_notification_only_usage_error_recovers_after_restart(system, role):
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
        component = Workers(db, registry, settings, factory)
        component.launch(Tasks(db).claim(2))
    else:
        chat.server = factory(settings)
        component = chat
        await chat.submit(registry.list()[0]["id"], "Preserve original request")
    await settle(component)
    row = db.one("SELECT * FROM executions WHERE agent_id=?", ("execution" if role == "worker" else "manager",))
    assert row["status"] == "recovering" and row["recovery_reason"] == "usage"
    assert db.setting(GATE)
    eligible(db)
    component = (
        Workers(Database(db.path), registry, settings, factory)
        if role == "worker"
        else Chat(Database(db.path), registry, factory(settings))
    )
    await component.recover()
    await settle(component)
    assert db.one("SELECT status FROM executions WHERE id=?", (row["id"],))["status"] == "completed"
    assert len(faults.calls) == 2 and db.setting(GATE) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["worker", "manager"])
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
        component = Workers(db, registry, settings, faults.factory)
        component.launch(Tasks(db).claim(2))
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

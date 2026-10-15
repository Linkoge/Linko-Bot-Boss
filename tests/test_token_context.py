import asyncio
import json
import time

import pytest
from fake_codex import FakeServer
from test_minimal_flow import NativeFixture, settled
from test_minimal_flow import system as system_fixture

from ai_manager.chat import Chat
from ai_manager.codex import TurnUsage
from ai_manager.orchestration import Scheduler
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

system = system_fixture


def test_request_usage_deduplicates_and_excludes_previous_thread_turns():
    usage = TurnUsage()
    first = {"total": {"inputTokens": 10100}, "last": {"inputTokens": 100, "cachedInputTokens": 60, "outputTokens": 3}}
    second = {"total": {"inputTokens": 10220}, "last": {"inputTokens": 120, "cachedInputTokens": 90, "outputTokens": 4}}
    usage.record(first)
    usage.record(first)
    result = usage.record(second)
    assert result["turn"] == {"inputTokens": 220, "cachedInputTokens": 150, "outputTokens": 7}
    assert result["total"] == second["total"]
    assert result["usageReports"] == 2


@pytest.mark.asyncio
async def test_context_updates_survive_restart_and_clear_removed_constraints(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    db.set_setting("global_instructions", "Preserve customer data")
    await chat.submit(project["id"], "Hello")
    await asyncio.gather(*list(chat.jobs.values()))
    assert json.loads(chat.server.prompts[0])["context"]["instructions"] == "Preserve customer data"
    restored = Chat(db, registry, chat.server)
    await restored.submit(project["id"], "Keep talking")
    await asyncio.gather(*list(restored.jobs.values()))
    followup = json.loads(chat.server.prompts[-1])
    assert followup.pop('bot_selection')['mode'] == 'manual'
    assert followup.pop("request_execution")["parallel_permission"] == {}
    assert followup == {"mode": "auto", "message": "Keep talking"}
    db.set_setting("global_instructions", "")
    await restored.submit(project["id"], "The constraint changed")
    await asyncio.gather(*list(restored.jobs.values()))
    assert json.loads(chat.server.prompts[-1])["context_updates"] == {"instructions": ""}


@pytest.mark.asyncio
async def test_old_history_tasks_and_memory_are_retrievable_in_conversation_mode(system):
    db, registry, chat, settings = system
    project, other = registry.list()
    origin = chat.conversation(project["id"])["id"]
    task = Tasks(db).add(project["id"], "Older completed objective " + "x" * 1000)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task["id"],))
        conn.execute(
            "INSERT INTO memory VALUES(?,'DECISIONS.md',?,0,?)",
            (project["id"], "critical decision " + "x" * 13000, time.time()),
        )
        for i in range(30):
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (
                    f"message-{i}",
                    origin,
                    "user" if i == 0 else "assistant",
                    "original authorized goal" if i == 0 else "recent result " + str(i),
                    "completed",
                    None,
                    time.time() + i,
                ),
            )
    assert "assignments" not in chat.context(project["id"])
    prompt, _ = chat.prompt(chat.conversation(project["id"]), "New result", "auto", "new", "new-thread", None)
    assert prompt["conversation_history"][0]["content"] == "original authorized goal"
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings, NativeFixture), chat)

    async def read(action, **args):
        return await scheduler.manager_action(
            project["id"],
            {
                "tool": "manager_action",
                "arguments": {"action": action, **args},
                "conversation_id": origin,
                "mode": "conversation",
            },
        )

    history = await read("read_history", query="authorized goal")
    assert history["messages"][0]["id"] == "message-0"
    assert (await read("read_history", message_id="message-0"))["content"] == "original authorized goal"
    assert len((await read("read_task", task_id=task["id"]))["description"]) > 1000
    with db.transaction() as conn:
        db.event(conn, "task.evidence", {"report": "/private/report.md", "status": "prepared"}, project["id"], task["id"])
    assert (await read("read_task", task_id=task["id"]))["evidence"] == [
        {"report": "/private/report.md", "status": "prepared"}
    ]
    assert (await read("list_tasks"))["assignments"][0]["description_truncated"]
    assert (await read("read_memory", path="DECISIONS.md"))["next_offset"] == 12000
    assert (await read("read_memory", path="DECISIONS.md", offset=12000))["content"]
    other_task = Tasks(db).add(other["id"], "Other project")
    with pytest.raises(ValueError, match="another project"):
        await read("read_task", task_id=other_task["id"])
    other_origin = chat.conversation(other["id"])["id"]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('private-message',?,'user','private','completed',NULL,?)",
            (other_origin, time.time()),
        )
    with pytest.raises(ValueError, match="Unknown message"):
        await read("read_history", message_id="private-message")


@pytest.mark.asyncio
async def test_manager_usage_is_persisted_with_native_events(system):
    db, registry, chat, settings = system

    class UsageServer(FakeServer):
        async def request(self, method, params, timeout=30):
            response = await super().request(method, params, timeout)
            if method == "turn/start":
                usage = {
                    "total": {"inputTokens": 20000},
                    "last": {"inputTokens": 100, "cachedInputTokens": 50, "outputTokens": 2},
                }
                for _ in range(2):
                    self.queues[params["threadId"]].put_nowait(
                        {"method": "thread/tokenUsage/updated", "params": {"turnId": "turn-test", "tokenUsage": usage}}
                    )
            return response

    chat.server = UsageServer(settings)
    result = await chat.submit(registry.list()[0]["id"], "Conversation only", mode="conversation")
    await asyncio.gather(*list(chat.jobs.values()))
    row = db.one("SELECT * FROM executions WHERE id=?", (result["execution_id"],))
    assert json.loads(row["usage"])["turn"]["inputTokens"] == 100
    assert json.loads(row["usage"])["usageReports"] == 1
    assert row["latency_ms"] > 0
    assert (
        len(db.all("SELECT * FROM events WHERE execution_id=? AND kind='thread/tokenUsage/updated'", (row["id"],))) == 2
    )
    assert not Tasks(db).list()


@pytest.mark.asyncio
@pytest.mark.parametrize("used", [0, 3, 4])
async def test_worker_continuation_keeps_thread_and_only_updates_changed_context(system, used):
    db, registry, chat, settings = system
    project = registry.list()[0]
    task = Tasks(db).add(project["id"], "First objective")
    servers = []

    def factory(settings, native=False):
        server = NativeFixture(settings, native=native)
        servers.append(server)
        return server

    workers = Workers(db, registry, settings, factory)
    workers.launch(Tasks(db).claim(2))
    await settled(workers)
    first_thread = Tasks(db).get(task["id"])["native_thread"]
    workflow = db.setting('assignment_workflow:' + task['id'])
    db.set_setting('assignment_workflow:' + task['id'], {**workflow, 'repair_rounds': used})
    Tasks(db).control(task["id"], "continue", Tasks(db).get(task["id"])["revision"])
    workers.launch(Tasks(db).claim(2))
    await settled(workers)
    assert Tasks(db).get(task["id"])["native_thread"] == first_thread
    continued = json.loads(servers[-1].prompts[-1])
    assert set(continued) == {"objective", "verification"}
    assert continued["objective"] == "First objective"
    assert continued["verification"]["repair_rounds_used"] == used + 1
    assert continued["verification"]["repair_rounds_remaining"] == max(0, 5 - used - 1)
    assert "project" not in continued  # Unchanged project context remains in the retained native thread.
    assert (
        json.loads(
            db.all("SELECT input FROM executions WHERE task_id=? ORDER BY started_at", (task["id"],))[-1]["input"]
        )["project"]["name"]
        == project["name"]
    )

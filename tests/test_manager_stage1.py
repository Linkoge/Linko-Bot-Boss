import json
import time

import pytest
from fake_codex import FakeServer
from test_workers import worker_env as worker_env

from ai_manager.chat import Chat
from ai_manager.memory import END, START
from ai_manager.tasks import Tasks


def retain(db, conversation_id, content):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO messages VALUES('preceding',?,'assistant',?,'completed',NULL,?)",
            (conversation_id, content, time.time() - 1),
        )


def test_context_excludes_generated_duplicates_without_changing_memory(worker_env):
    settings, db, registry, project, _ = worker_env
    original = f"# Owner decisions\nUse SQLite WAL.\n{START}\nOld execution references and duplicated instructions\n{END}\nKeep this conclusion."
    memory = next(m for m in registry.memory(project["id"]) if m["name"] == "DECISIONS.md")
    registry.update_memory(project["id"], memory["name"], original, memory["revision"])
    chat = Chat(db, registry, FakeServer(settings))
    context = chat.context(project["id"])
    excerpt = context["memory"]["DECISIONS.md"]
    assert excerpt.count("Use SQLite WAL.") == 1
    assert "Keep this conclusion." in excerpt and "Old execution" not in excerpt
    assert next(m for m in registry.memory(project["id"]) if m["name"] == memory["name"])["content"] == original
    assert context["selected_context"]["path"] == project["path"]
    malformed = END + "Owner text with an incomplete marker" + START
    assert Chat.memory_excerpt(malformed) == malformed


@pytest.mark.parametrize("has_old_thread", [False, True])
async def test_followup_retains_complete_preceding_message_and_does_not_inject_current_twice(
    worker_env, has_old_thread
):
    settings, db, registry, project, _ = worker_env
    server = FakeServer(settings)
    chat = Chat(db, registry, server)
    conversation = chat.conversation(project["id"])
    proposal = "Details. " * 150 + "Implement keyboard navigation and test it."
    retain(db, conversation["id"], proposal)
    if has_old_thread:
        with db.transaction() as conn:
            conn.execute("UPDATE conversations SET thread_id='old-thread' WHERE id=?", (conversation["id"],))
    job = await chat.submit(project["id"], "do it", "auto")
    await chat.jobs[job["execution_id"]]
    prompt = json.loads(server.prompts[0])
    assert prompt["message"] == "do it"
    assert prompt["conversation_history"] == [{"role": "assistant", "content": proposal}]
    assert (
        "no executable task" not in db.one("SELECT output FROM executions WHERE id=?", (job["execution_id"],))["output"]
    )
    await chat.close()


async def test_native_resume_avoids_duplicate_database_history(worker_env):
    import hashlib

    from ai_manager.orchestration import MANAGER_TOOL

    settings, db, registry, project, _ = worker_env
    server = FakeServer(settings)
    chat = Chat(db, registry, server)
    conversation = chat.conversation(project["id"])
    retain(db, conversation["id"], "Native thread already contains this.")
    with db.transaction() as conn:
        conn.execute("UPDATE conversations SET thread_id='native-thread' WHERE id=?", (conversation["id"],))
    db.set_setting(
        "manager_tool_schema:" + conversation["id"],
        hashlib.sha256(json.dumps(MANAGER_TOOL, sort_keys=True).encode()).hexdigest(),
    )
    job = await chat.submit(project["id"], "Explain that.", "conversation")
    await chat.jobs[job["execution_id"]]
    assert "conversation_history" not in json.loads(server.prompts[0])
    await chat.close()


async def test_execute_followup_includes_history_and_preserves_structured_receipt(worker_env):
    settings, db, registry, project, _ = worker_env
    chat = Chat(db, registry, FakeServer(settings))
    conversation = chat.conversation(project["id"])
    retain(db, conversation["id"], "Implement keyboard navigation and test it.")
    calls = []
    record = {
        "task_id": "1234567890-full-task",
        "project": "sample",
        "status": "pending",
        "blocker": "Scheduler paused",
        "worker": {"id": "full-worker-id", "pid": 42, "thread_id": "full-thread", "turn_id": "full-turn"},
    }

    async def handler(selected, params):
        calls.append(params)
        return {"tasks": [record]}

    chat.action_handler = handler
    job = await chat.submit(project["id"], "do it", "execute")
    await chat.jobs[job["execution_id"]]
    goal = json.loads(calls[0]["arguments"]["goal"])
    assert goal["message"] == "do it"
    assert "keyboard navigation" in goal["conversation_history"][0]["content"]
    stored = json.loads(db.one("SELECT payload FROM events WHERE kind='chat.execution_records'")["payload"])
    assert stored["tasks"] == [record]
    output = db.one("SELECT output FROM executions WHERE id=?", (job["execution_id"],))["output"]
    assert "12345678" in output and "Scheduler paused" in output
    assert "full-worker-id" not in output and "full-thread" not in output
    await chat.close()


@pytest.mark.parametrize(
    "action",
    ["plan_goal", "prioritize", "pause_project", "resume_project", "run_queue", "freeze_projects", "owner_admin"],
)
async def test_conversation_rejects_every_mutation_independently_of_prompt(worker_env, action):
    settings, db, registry, project, _ = worker_env

    class AttemptServer(FakeServer):
        async def request(self, method, params, timeout=30):
            if method == "turn/start":
                with pytest.raises(ValueError, match="Conversation mode"):
                    await self.tool_handlers[params["threadId"]](
                        {
                            "tool": "manager_action",
                            "callId": "unapproved",
                            "arguments": {"action": action, "request": json.dumps({"action": "discover_verification"})},
                        }
                    )
            return await super().request(method, params, timeout)

    chat = Chat(db, registry, AttemptServer(settings))

    async def forbidden(*args):
        raise AssertionError("Mutation reached application handler")

    chat.action_handler = forbidden
    job = await chat.submit(project["id"], "Discuss options", "conversation")
    await chat.jobs[job["execution_id"]]
    assert not Tasks(db).list()
    await chat.close()


def test_failed_and_rejected_administration_summaries_keep_blockers():
    events = [
        {
            "seq": 1,
            "kind": "owner_admin.failed",
            "payload": json.dumps(
                {
                    "id": "failed-request",
                    "status": "failed",
                    "error": "Missing trusted commands",
                    "request": {"action": "retry_verification"},
                }
            ),
        },
        {
            "seq": 2,
            "kind": "owner_admin.rejected",
            "payload": json.dumps(
                {
                    "reason": "Missing/revoked/out-of-scope grant",
                    "request": {"id": "rejected-request", "action": "activate_profile"},
                }
            ),
        },
    ]
    text = Chat.administration_text(events)
    assert "failed-request · failed" in text and "Missing trusted commands" in text
    assert "rejected-request · rejected" in text and "out-of-scope grant" in text

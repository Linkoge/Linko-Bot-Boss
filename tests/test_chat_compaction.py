"""Chat compaction and two-tier chat memory: Compact runs in the background and applies itself, automatic threshold,
messages sent meanwhile kept, failures leave the chat unchanged, expiry, promotion, redaction, what the manager is fed,
and what a chat saves. Disposable data; the model is a protocol fixture."""
import asyncio
import json
import os
import time

import pytest
from fake_codex import FakeServer
from test_web import client as client_fixture
from test_web import sign_in

from ai_manager import chat_memory
from ai_manager.chat import Chat
from ai_manager.chat_memory import ACTIVE, ARCHIVE, INSTRUCTIONS, PROMOTE_AFTER, merge, scrub, sweep
from ai_manager.config import Settings
from ai_manager.db import Database, new_id
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.web import create_app

client = client_fixture


def default_reply(prompt):
    return json.dumps({"summary": "Deploy works; waiting for the Windows 10 PC to test the installer.", "items": [
        {"action": "new", "tier": "long", "text": "Decision: SQLite stays the store because backups are one file."},
        {"action": "new", "tier": "short", "text": "Waiting for the Windows 10 PC to test the installer."},
    ]})


class CompactServer(FakeServer):
    """FakeServer for manager turns; answers compaction threads (recognised by their instructions) with `reply`,
    after `gate` (an asyncio.Event) is set or `delay` seconds (COMPACT_DELAY for a subprocess server) have passed."""

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.reply, self.hang, self.compactions, self.interrupted = default_reply, False, [], []
        self.gate, self.delay = None, float(os.environ.get("COMPACT_DELAY", "0"))
        for name in ("claude-haiku-5-5", "claude-haiku-5-10", "claude-haiku-4-9", "claude-sonnet-9-9"):
            self.catalog.append({**self.catalog[0], "model": name, "isDefault": False})

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        if instructions == INSTRUCTIONS:
            return f"compact-{len(self.compactions)}-{new_id()[:6]}"
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)

    async def request(self, method, params, timeout=30):
        thread = (params or {}).get("threadId", "")
        if method == "turn/interrupt" and thread.startswith("compact-"):
            self.interrupted.append(thread)
            return {}
        if method != "turn/start" or not thread.startswith("compact-"):
            return await super().request(method, params, timeout)
        prompt = json.loads(params["input"][0]["text"])
        self.compactions.append({"prompt": prompt, "model": params.get("model"), "effort": params.get("effort")})
        if not self.hang:
            text = self.reply(prompt) if callable(self.reply) else self.reply
            queue = self.queues[thread]

            def emit():
                queue.put_nowait({"method": "item/agentMessage/delta", "params": {"turnId": "c-turn", "delta": text}})
                queue.put_nowait({"method": "turn/completed", "params": {"turn": {"id": "c-turn",
                                                                                   "status": "completed"}}})

            if self.gate or self.delay:
                async def later():
                    await (self.gate.wait() if self.gate else asyncio.sleep(self.delay))
                    emit()
                self.pending = asyncio.create_task(later())
            else:
                emit()
        return {"turn": {"id": "c-turn"}}


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
    return db, registry, Chat(db, registry, CompactServer(settings)), settings


def seed(db, conversation_id, pairs):
    now = time.time()
    with db.transaction() as conn:
        for index, (role, text) in enumerate(pairs):
            conn.execute("INSERT INTO messages VALUES(?,?,?,?,'completed',NULL,?)",
                         (new_id(), conversation_id, role, text, now + index * 0.001))


def items(db, scope, name=ACTIVE):
    row = db.one("SELECT content FROM memory WHERE project_id IS ? AND name=?", (scope, name))
    return json.loads(row["content"])["items"] if row else []


async def settle(compactor):
    while compactor.jobs:
        await asyncio.gather(*list(compactor.jobs.values()), return_exceptions=True)


async def test_compact_starts_in_the_background_and_applies_itself_archiving_the_full_chat(system):
    db, registry, chat, _ = system
    project = registry.list()[0]["id"]
    conversation = chat.conversation(project)
    seed(db, conversation["id"], [("user", "Let's keep SQLite?"), ("assistant", "Yes, backups stay one file."),
                                  ("user", "Hi again, how is the installer?"), ("assistant", "Waiting for the PC.")])
    with db.transaction() as conn:
        conn.execute("UPDATE conversations SET thread_id='old-thread' WHERE id=?", (conversation["id"],))
    before = chat.history(project)["messages"]
    chat.server.gate = asyncio.Event()
    started = chat.compactor.start(project)
    assert started["status"] == "running" and not started["already_running"]
    assert chat.history(project)["compaction_notice"]["text"].startswith("Compacting chat in the background")
    again = chat.compactor.start(project)  # a second click only says it is running
    assert again["already_running"] and "already running" in again["message"]
    await asyncio.sleep(0.05)
    assert len(chat.server.compactions) == 1 and chat.compactor.status(project)["running"]
    assert [m["id"] for m in chat.history(project)["messages"]] == [m["id"] for m in before]  # unchanged meanwhile
    chat.server.gate.set()
    await settle(chat.compactor)
    # The model read existing memory and the owner/manager chat only.
    sent = chat.server.compactions[0]["prompt"]
    assert [m["text"] for m in sent["chat"]] == [m["content"] for m in before]
    assert sent["existing_memory"] == {"long_term": [], "short_term": []}
    # Applied without any confirm step.
    current = chat.history(project)
    assert current["conversation"]["id"] == conversation["id"]  # pending worker results still reach this chat
    assert len(current["messages"]) == 1
    assert current["messages"][0]["content"].startswith("Chat compacted: 2 items kept (1 long-term, 1 short-term)")
    notice = current["compaction_notice"]
    assert notice["state"] == "done" and notice["text"].startswith("Chat compacted")
    assert chat.compactor.status(project) == {"running": False, "notice": notice}
    assert db.one("SELECT thread_id FROM conversations WHERE id=?", (conversation["id"],))["thread_id"] is None
    # The full previous chat is archived under an automatic name and retrievable through the history readers.
    archives = [c for c in chat.search_history(project)["conversations"] if c["archived_at"]]
    assert len(archives) == 1 and archives[0]["message_count"] == 4
    assert archives[0]["title"].startswith("one · ") and archives[0]["title"].endswith(" · before compaction")
    archive_id = db.one("SELECT json_extract(payload,'$.archive_conversation_id') AS id FROM events "
                        "WHERE kind='chat.compacted'")["id"]
    saved = chat.saved_history(archive_id, project)["messages"]
    assert [m["id"] for m in saved] == [m["id"] for m in before]
    assert {i["tier"] for i in items(db, project)} == {"long", "short"}
    # The next turn starts a fresh thread from the summary message plus active memory.
    prompt, _ = chat.prompt(chat.conversation(project), "next", "next-execution", None, None)
    assert [m["content"] for m in prompt["conversation_history"]] == [current["messages"][0]["content"]]
    assert [i["text"] for i in prompt["context"]["chat_memory"]["long_term"]] == [
        "Decision: SQLite stays the store because backups are one file."]


async def test_messages_sent_during_compaction_stay_after_the_summary(system):
    db, registry, chat, _ = system
    conversation = chat.conversation(None)
    seed(db, conversation["id"], [("user", "Remember: I prefer short reports."), ("assistant", "Noted.")])
    covered = [m["id"] for m in chat.history(None)["messages"]]
    chat.server.gate, chat.compactor.poll = asyncio.Event(), 0.01
    chat.compactor.start(None)
    # The owner keeps chatting: one finished exchange and one turn still running when the summary is ready.
    seed(db, conversation["id"], [("user", "One more thing"), ("assistant", "Sure.")])
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at,conversation_id,input,launch_key) "
                     "VALUES('turn','manager','running',?,?,'x','k')", (time.time(), conversation["id"]))
    seed(db, conversation["id"], [("user", "And another")])
    newer = [m["id"] for m in chat.history(None)["messages"]][2:]
    chat.server.gate.set()
    await asyncio.sleep(0.1)
    assert chat.compactor.status(None)["running"]  # waits for the turn instead of applying mid-turn
    assert len(chat.history(None)["messages"]) == 5
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET status='completed',finished_at=? WHERE id='turn'", (time.time(),))
    await settle(chat.compactor)
    current = chat.history(None)["messages"]
    assert current[0]["content"].startswith("Chat compacted: 2 items kept")
    assert "3 newer messages sent during compaction stay in the chat" in current[0]["content"]
    assert [m["id"] for m in current[1:]] == newer
    assert [m["content"] for m in current[1:]] == ["One more thing", "Sure.", "And another"]
    archive = [c for c in chat.search_history(None)["conversations"] if c["archived_at"]]
    assert [c["message_count"] for c in archive] == [2]
    assert [m["id"] for m in chat.saved_history(archive[0]["id"], None)["messages"]] == covered
    prompt, _ = chat.prompt(chat.conversation(None), "next", "next-execution", None, None)
    assert [m["content"] for m in prompt["conversation_history"]][1:] == ["One more thing", "Sure.", "And another"]


async def test_a_chat_cleared_during_compaction_is_left_alone(system):
    db, registry, chat, _ = system
    conversation = chat.conversation(None)
    seed(db, conversation["id"], [("user", "hello"), ("assistant", "hi")])
    last = chat.history(None)["messages"][-1]["id"]
    chat.server.gate = asyncio.Event()
    chat.compactor.start(None)
    chat.reset(None, conversation["id"], last, True, archive=True, title="Owner archive")
    chat.server.gate.set()
    await settle(chat.compactor)
    notice = chat.history(None)["compaction_notice"]
    assert notice["state"] == "failed" and "the chat is unchanged" in notice["text"]
    assert items(db, None) == [] and chat.history(None)["messages"] == []
    archives = [c["title"] for c in chat.search_history(None)["conversations"] if c["archived_at"]]
    assert archives == ["Owner archive"]


def test_a_running_notice_left_by_a_restart_reads_as_interrupted(system):
    db, registry, chat, _ = system
    chat.compactor.notice(None, chat_memory.RUNNING_TEXT, "running")
    notice = chat.history(None)["compaction_notice"]
    assert notice["state"] == "failed" and "interrupted" in notice["text"]
    with pytest.raises(ValueError, match="nothing to compact"):
        chat.compactor.start(None)


async def test_automatic_compaction_past_threshold_once_never_mid_turn_and_safe_on_failure(system):
    db, registry, chat, _ = system
    project = registry.list()[0]["id"]
    db.set_setting(chat_memory.SETTINGS_KEY, {**chat_memory.DEFAULTS, "threshold_tokens": 1250, "revision": 1})
    conversation = chat.conversation(project)
    compactor, clock = chat.compactor, iter(range(1000, 100000, 100))
    seed(db, conversation["id"], [("user", "short question"), ("assistant", "x" * 3000)])
    compactor.check(next(clock))
    assert compactor.jobs == {} and chat.server.compactions == []  # below the threshold
    seed(db, conversation["id"], [("assistant", "y" * 3000)])
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,project_id,agent_id,status,started_at,conversation_id,input,launch_key)"
                     " VALUES('turn',?,'manager','running',?,?,'x','k')", (project, time.time(), conversation["id"]))
    compactor.check(next(clock))
    assert compactor.jobs == {}  # never mid-turn
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET status='completed',finished_at=? WHERE id='turn'", (time.time(),))
    # First crossing fails: the chat is untouched, one notice, no retry at the same crossing.
    chat.server.reply = "not json at all"
    compactor.check(next(clock))
    await settle(compactor)
    assert len(chat.server.compactions) == 1
    history = chat.history(project)
    assert len(history["messages"]) == 3
    assert history["compaction_notice"]["text"].startswith("Automatic chat compaction failed; the chat is unchanged")
    for _ in range(3):
        compactor.check(next(clock))
        await settle(compactor)
    assert len(chat.server.compactions) == 1
    # A timeout is a failure too, bounded and abandoned.
    seed(db, conversation["id"], [("assistant", "z" * 5000)])
    chat.server.hang, compactor.timeout = True, 0.2
    compactor.check(next(clock))
    await settle(compactor)
    assert len(chat.server.compactions) == 2 and chat.server.interrupted
    assert "timed out" in chat.history(project)["compaction_notice"]["text"]
    assert len(chat.history(project)["messages"]) == 4
    # The next crossing succeeds without a confirm step and posts a visible notice.
    seed(db, conversation["id"], [("assistant", "w" * 5000)])
    chat.server.hang, chat.server.reply, compactor.timeout = False, default_reply, 30
    compactor.check(next(clock))
    await settle(compactor)
    history = chat.history(project)
    assert [m["content"].split(" (")[0] for m in history["messages"]] == ["Chat compacted: 2 items kept"]
    assert history["compaction_notice"]["state"] == "done"
    assert db.one("SELECT 1 FROM events WHERE kind='chat.compacted' AND json_extract(payload,'$.automatic')")
    compactor.check(next(clock))
    await settle(compactor)
    assert len(chat.server.compactions) == 3  # the short chat is below the threshold again


async def test_invalid_results_never_change_the_chat_or_memory(system):
    db, registry, chat, _ = system
    conversation = chat.conversation(None)
    seed(db, conversation["id"], [("user", "hello"), ("assistant", "hi")])
    for reply in ('{"summary": "", "items": []}', '{"summary": "s", "items": [{"action": "keep", "id": "missing"}]}',
                  '{"summary": "s", "items": [{"action": "new", "tier": "medium", "text": "x"}]}'):
        chat.server.reply = reply
        chat.compactor.start(None)
        await settle(chat.compactor)
        notice = chat.compactor.status(None)["notice"]
        assert notice["state"] == "failed" and notice["text"].startswith("Chat compaction failed; the chat is unchanged")
    assert len(chat.history(None)["messages"]) == 2 and items(db, None) == []


def test_short_term_items_expire_to_a_searchable_archive_and_reuse_refreshes_them(system):
    db, registry, chat, settings = system
    project = registry.list()[0]["id"]
    now, day = time.time(), 86400
    result = {"summary": "s", "items": [{"action": "new", "tier": "short", "text": "Bug X fixed by Y"},
                                        {"action": "new", "tier": "short", "text": "Tried Z, failed because W"},
                                        {"action": "new", "tier": "long", "text": "Owner prefers short reports"}]}
    active, archive, _ = merge([], [], result, now - 29 * day, 30)
    reused = next(i for i in active if i["text"] == "Bug X fixed by Y")
    active, archive, report = merge(active, archive, {"summary": "s", "items": [{"action": "keep", "id": reused["id"]}]},
                                    now - 20 * day, 30)
    assert report["reused"] == ["Bug X fixed by Y"]
    with db.transaction() as conn:
        chat_memory.write_row(conn, project, ACTIVE, {"items": active})
    kept, archived = sweep(db, project, now + 1.5 * day)
    assert {i["text"] for i in kept} == {"Bug X fixed by Y", "Owner prefers short reports"}  # refreshed, long-term
    assert archived == 1
    assert [(i["text"], i["archive_reason"]) for i in items(db, project, ARCHIVE)] == [
        ("Tried Z, failed because W", "inactive")]
    # Archived items are never fed to the manager but stay searchable through read_memory.
    context = chat.context(project)
    assert "Tried Z" not in json.dumps(context) and context["chat_memory"]["archived_items"] == 1
    scheduler = Scheduler(db, registry, settings, type("Core", (), {"pipeline": None})(), chat)
    found = asyncio.run(scheduler.manager_action(project, {"tool": "manager_action", "arguments": {
        "action": "read_memory", "path": ARCHIVE, "query": "tried z"}, "conversation_id": "c"}))
    assert [i["text"] for i in found["items"]] == ["Tried Z, failed because W"]


def test_a_short_term_item_reused_in_three_compactions_is_promoted(system):
    now = time.time()
    active, archive, _ = merge([], [], {"summary": "s", "items": [
        {"action": "new", "tier": "short", "text": "Deploy needs the VPN up"}]}, now, 30)
    item = active[0]["id"]
    for round_ in range(PROMOTE_AFTER):
        assert active[0]["tier"] == "short"
        active, archive, report = merge(active, archive, {"summary": "s", "items": [
            {"action": "keep" if round_ % 2 else "update", "id": item, "tier": "short",
             "text": "Deploy needs the VPN up"}]}, now + round_, 30)
    assert active[0]["tier"] == "long" and report["promoted"] == ["Deploy needs the VPN up"]
    assert active[0]["reuses"] == PROMOTE_AFTER


async def test_secrets_and_credentials_are_redacted_before_anything_is_stored(system):
    db, registry, chat, _ = system
    token, key = "ghp_" + "Q" * 36, "sk-" + "A1b2" * 6
    conversation = chat.conversation(None)
    seed(db, conversation["id"], [("user", f"Use {token} for the push"), ("assistant", "Done.")])
    chat.server.reply = json.dumps({"summary": f"Pushed with {token}.", "items": [
        {"action": "new", "tier": "long", "text": f"Push token is {token}; API key {key}; password: hunter2abc9"},
        {"action": "new", "tier": "short", "text": "Clone via https://bob:" + "pw" + "12345678@example.com/repo"}]})
    chat.compactor.start(None)
    await settle(chat.compactor)
    stored = json.dumps(items(db, None)) + chat.history(None)["messages"][0]["content"]
    for secret in (token, key, "hunter2abc9", "pw12345678"):
        assert secret not in stored
    assert "[redacted]" in stored
    assert scrub("Waiting for the Windows 10 PC; the token expires on Friday") == (
        "Waiting for the Windows 10 PC; the token expires on Friday")


async def test_saved_chat_has_owner_messages_and_final_replies_only_never_reasoning(system):
    db, registry, chat, settings = system

    class Reasoning(CompactServer):
        async def request(self, method, params, timeout=30):
            if method != "turn/start":
                return await super().request(method, params, timeout)
            self.prompts.append(params["input"][0]["text"])
            queue, thread = self.queues[params["threadId"]], params["threadId"]
            for event in ({"method": "item/reasoning/textDelta", "params": {"turnId": "t", "delta": "secret plan"}},
                          {"method": "item/agentMessage/delta", "params": {"turnId": "t", "delta": "Final reply"}},
                          {"method": "turn/completed", "params": {"threadId": thread, "turn": {
                              "id": "t", "status": "completed"}}}):
                queue.put_nowait(event)
            return {"turn": {"id": "t"}}

    chat = Chat(db, registry, Reasoning(settings))
    chat.action_handler = None
    await chat.submit(None, "What next?")
    await asyncio.gather(*list(chat.jobs.values()))
    saved = db.all("SELECT role,content FROM messages ORDER BY rowid")
    assert saved == [{"role": "user", "content": "What next?"}, {"role": "assistant", "content": "Final reply"}]
    transcript = chat.compactor.transcript(chat.conversation(None)["id"])
    assert [(m["role"], m["content"]) for m in transcript] == [("user", "What next?"), ("assistant", "Final reply")]


def test_compaction_model_setting_is_shown_saved_and_used(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    catalog = app.state.chat.server.catalog
    catalog += [{**catalog[0], "model": name, "isDefault": False}
                for name in ("claude-haiku-5-5", "claude-haiku-5-10", "claude-haiku-4-9", "claude-sonnet-9-9")]
    shown = browser.get("/api/chat-memory/settings").json()
    assert shown["settings"]["model"] == "latest-haiku" and shown["settings"]["effort"] == "low"
    assert shown["effective"] == {"model": "claude-haiku-5-10", "effort": "low"}  # newest Haiku, by version not text
    assert shown["settings"]["threshold_tokens"] == 150000 and shown["settings"]["short_term_days"] == 30
    body = {"revision": 0, "model": "unknown-model", "effort": None, "threshold_tokens": 20000, "short_term_days": 7}
    assert browser.put("/api/chat-memory/settings", json=body).status_code == 409
    assert browser.put("/api/chat-memory/settings", json={**body, "model": None, "threshold_tokens": 10}).status_code == 409
    assert browser.put("/api/chat-memory/settings", json={**body, "model": None, "threshold_tokens": 2000000}).status_code == 409
    saved = browser.put("/api/chat-memory/settings", json={**body, "model": "test-model", "effort": "high"}).json()
    assert saved["effective"] == {"model": "test-model", "effort": "high"}
    assert browser.get("/api/chat-memory/settings").json()["settings"] == {
        "model": "test-model", "effort": "high", "threshold_tokens": 20000, "short_term_days": 7, "revision": 1}
    assert chat_memory.route(app.state.db, app.state.chat.server) == {"model": "test-model", "effort": "high"}


def test_legacy_settings_migrate_to_tokens_latest_haiku_and_low_effort(system):
    db, _, chat, _ = system

    def migrated(**old):
        db.set_setting(chat_memory.SETTINGS_KEY, {"short_term_days": 30, "revision": 3, **old})
        return chat_memory.load_settings(db)

    for chars in (150000, 60000):
        assert migrated(model=None, effort=None, threshold_chars=chars)["threshold_tokens"] == 150000
    value = migrated(model="claude-haiku-4-9", effort="medium", threshold_chars=80000)
    assert (value["threshold_tokens"], value["model"], value["effort"], value["revision"]) == (
        20000, "latest-haiku", "low", 3) and "threshold_chars" not in value
    value = migrated(model="test-model", effort="high", threshold_chars=40000)
    assert (value["model"], value["effort"]) == ("test-model", "high")
    assert chat_memory.latest_haiku(chat.server) == "claude-haiku-5-10"


def test_compact_http_round_trip_uses_the_saved_model(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,), secure_cookie=False)
    server = CompactServer(settings)
    app = create_app(settings, server)
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://localhost") as browser:
        sign_in(browser, app, settings)
        db, chat = app.state.db, app.state.chat
        db.set_setting(chat_memory.SETTINGS_KEY, {**chat_memory.DEFAULTS, "model": "test-model", "effort": "medium"})
        conversation = chat.conversation(None)
        seed(db, conversation["id"], [("user", "Plan the release"), ("assistant", "Release on Friday.")])
        server.delay = 0.3
        started = browser.post("/api/chat/compact", json={}).json()
        assert started["status"] == "running" and not started["already_running"]
        assert browser.post("/api/chat/compact", json={}).json()["already_running"]
        assert browser.get("/api/history").json()["compaction_notice"]["state"] == "running"
        for _ in range(200):
            status = browser.get("/api/chat/compact/status").json()
            if not status["running"]:
                break
            time.sleep(0.02)
        assert status["notice"]["state"] == "done", status
        assert (server.compactions[0]["model"], server.compactions[0]["effort"]) == ("test-model", "medium")
        history = browser.get("/api/history").json()
        assert len(history["messages"]) == 1 and history["messages"][0]["content"].startswith("Chat compacted: 2 items")
        archived = [c for c in browser.get("/api/chat-history").json()["conversations"] if c["archived_at"]]
        assert [c["message_count"] for c in archived] == [2]
        # The proposal/confirm endpoints are gone.
        assert browser.post("/api/chat/compact/apply", json={}).status_code in (404, 405)
        assert browser.post("/api/chat/compact/discard", json={}).status_code in (404, 405)
        assert browser.post("/api/chat/compact/notice/dismiss", json={}).status_code == 200
        assert browser.get("/api/history").json()["compaction_notice"] is None

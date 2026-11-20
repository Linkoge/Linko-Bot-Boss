"""Chat compaction and two-tier chat memory: manual propose/confirm, automatic threshold, expiry, promotion,
redaction, what the manager is fed, and what a chat saves. Disposable data; the model is a protocol fixture."""
import asyncio
import json
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
    """FakeServer for manager turns; answers compaction threads (recognised by their instructions) with `reply`."""

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.reply, self.hang, self.compactions, self.interrupted = default_reply, False, [], []

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
            queue.put_nowait({"method": "item/agentMessage/delta", "params": {"turnId": "c-turn", "delta": text}})
            queue.put_nowait({"method": "turn/completed", "params": {"turn": {"id": "c-turn", "status": "completed"}}})
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


async def test_manual_compact_proposes_then_applies_only_on_confirm_and_archives_the_chat(system):
    db, registry, chat, _ = system
    project = registry.list()[0]["id"]
    conversation = chat.conversation(project)
    seed(db, conversation["id"], [("user", "Let's keep SQLite?"), ("assistant", "Yes, backups stay one file."),
                                  ("user", "Hi again, how is the installer?"), ("assistant", "Waiting for the PC.")])
    with db.transaction() as conn:
        conn.execute("UPDATE conversations SET thread_id='old-thread' WHERE id=?", (conversation["id"],))
    before = chat.history(project)["messages"]
    started = chat.compactor.start(project)
    assert started["status"] == "running"
    with pytest.raises(ValueError, match="already running"):
        chat.compactor.start(project)
    await settle(chat.compactor)
    proposal = chat.compactor.get(project, started["id"])
    assert proposal["status"] == "ready"
    assert proposal["proposal"]["long_term"] == ["Decision: SQLite stays the store because backups are one file."]
    assert proposal["proposal"]["short_term"] == ["Waiting for the Windows 10 PC to test the installer."]
    assert proposal["summary"].startswith("Deploy works")
    # The model read existing memory and the owner/manager chat only.
    sent = chat.server.compactions[0]["prompt"]
    assert [m["text"] for m in sent["chat"]] == [m["content"] for m in before]
    assert sent["existing_memory"] == {"long_term": [], "short_term": []}
    # Proposing changes nothing.
    assert [m["id"] for m in chat.history(project)["messages"]] == [m["id"] for m in before]
    assert items(db, project) == []
    with pytest.raises(ValueError, match="Confirm"):
        chat.compactor.confirm(project, started["id"], conversation["id"], before[-1]["id"], False)
    assert items(db, project) == []
    receipt = chat.compactor.confirm(project, started["id"], conversation["id"], before[-1]["id"], True, "Before compaction")
    assert receipt["message"].startswith("Chat compacted: 2 items kept (1 long-term, 1 short-term)")
    current = chat.history(project)
    assert current["conversation"]["id"] == conversation["id"]  # pending worker results still reach this chat
    assert [m["content"].split("\n")[0] for m in current["messages"]] == [receipt["message"]]
    assert db.one("SELECT thread_id FROM conversations WHERE id=?", (conversation["id"],))["thread_id"] is None
    # The full previous chat is archived and retrievable through the existing history readers.
    archives = [c for c in chat.search_history(project)["conversations"] if c["archived_at"]]
    assert [(c["title"], c["message_count"]) for c in archives] == [("Before compaction", 4)]
    saved = chat.saved_history(receipt["archive_conversation_id"], project)["messages"]
    assert [m["id"] for m in saved] == [m["id"] for m in before]
    assert {i["tier"] for i in items(db, project)} == {"long", "short"}
    # The next turn starts a fresh thread from the summary message plus active memory.
    prompt, _ = chat.prompt(chat.conversation(project), "next", "next-execution", None, None)
    assert [m["content"] for m in prompt["conversation_history"]] == [current["messages"][0]["content"]]
    assert [i["text"] for i in prompt["context"]["chat_memory"]["long_term"]] == [
        "Decision: SQLite stays the store because backups are one file."]
    with pytest.raises(ValueError, match="not ready"):
        chat.compactor.confirm(project, started["id"], conversation["id"], before[-1]["id"], True)


async def test_a_proposal_is_refused_after_new_messages_or_mid_turn(system):
    db, registry, chat, _ = system
    conversation = chat.conversation(None)
    seed(db, conversation["id"], [("user", "Remember: I prefer short reports."), ("assistant", "Noted.")])
    started = chat.compactor.start(None)
    await settle(chat.compactor)
    last = chat.history(None)["messages"][-1]["id"]
    seed(db, conversation["id"], [("user", "One more thing")])
    with pytest.raises(ValueError, match="New messages arrived"):
        chat.compactor.confirm(None, started["id"], conversation["id"], last, True)
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at,conversation_id,input,launch_key) "
                     "VALUES('turn','manager','running',?,?,'x','k')", (time.time(), conversation["id"]))
    with pytest.raises(ValueError, match="Wait for the response"):
        chat.compactor.start(None)
    assert len(chat.history(None)["messages"]) == 3
    assert items(db, None) == []


async def test_automatic_compaction_past_threshold_once_never_mid_turn_and_safe_on_failure(system):
    db, registry, chat, _ = system
    project = registry.list()[0]["id"]
    db.set_setting(chat_memory.SETTINGS_KEY, {**chat_memory.DEFAULTS, "threshold_chars": 5000, "revision": 1})
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
    assert history["compaction_notice"] is None
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
        started = chat.compactor.start(None)
        await settle(chat.compactor)
        assert chat.compactor.get(None, started["id"])["status"] == "failed"
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
    started = chat.compactor.start(None)
    await settle(chat.compactor)
    last = chat.history(None)["messages"][-1]["id"]
    chat.compactor.confirm(None, started["id"], conversation["id"], last, True)
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
    shown = browser.get("/api/chat-memory/settings").json()
    assert shown["settings"]["model"] is None and shown["effective"] == {"model": "test-model", "effort": "low"}
    assert shown["settings"]["threshold_chars"] == 60000 and shown["settings"]["short_term_days"] == 30
    body = {"revision": 0, "model": "unknown-model", "effort": None, "threshold_chars": 9000, "short_term_days": 7}
    assert browser.put("/api/chat-memory/settings", json=body).status_code == 409
    assert browser.put("/api/chat-memory/settings", json={**body, "model": None, "threshold_chars": 10}).status_code == 409
    saved = browser.put("/api/chat-memory/settings", json={**body, "model": "test-model", "effort": "high"}).json()
    assert saved["effective"] == {"model": "test-model", "effort": "high"}
    assert browser.get("/api/chat-memory/settings").json()["settings"] == {
        "model": "test-model", "effort": "high", "threshold_chars": 9000, "short_term_days": 7, "revision": 1}
    assert chat_memory.route(app.state.db, app.state.chat.server) == {"model": "test-model", "effort": "high"}


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
        proposal = browser.post("/api/chat/compact", json={}).json()
        for _ in range(100):
            proposal = browser.get(f"/api/chat/compact/{proposal['id']}").json()
            if proposal["status"] != "running":
                break
            time.sleep(0.02)
        assert proposal["status"] == "ready", proposal
        assert (server.compactions[0]["model"], server.compactions[0]["effort"]) == ("test-model", "medium")
        refused = browser.post("/api/chat/compact/apply", json={
            "proposal_id": proposal["id"], "conversation_id": proposal["conversation_id"],
            "last_message_id": proposal["last_message_id"]})
        assert refused.status_code == 409 and len(chat.history(None)["messages"]) == 2
        applied = browser.post("/api/chat/compact/apply", json={
            "proposal_id": proposal["id"], "conversation_id": proposal["conversation_id"],
            "last_message_id": proposal["last_message_id"], "confirmed": True}).json()
        assert applied["message"].startswith("Chat compacted: 2 items kept")
        history = browser.get("/api/history").json()
        assert len(history["messages"]) == 1 and history["compaction_notice"] is None
        archived = [c for c in browser.get("/api/chat-history").json()["conversations"] if c["archived_at"]]
        assert [c["message_count"] for c in archived] == [2]

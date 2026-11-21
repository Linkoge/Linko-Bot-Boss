"""Office mode multi-chat: conversations CRUD, pin, server-side search, title once, memory on leaving a chat
(OR rule: message threshold, or a few messages after a cool-down; only the new part; the chat itself unchanged; one job
per instance), per-chat files, and Main unchanged. Disposable data; the model is a protocol fixture."""
import asyncio
import json
import secrets
import time

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient

from ai_manager import chat_memory, office_chats
from ai_manager.chat import Chat
from ai_manager.chat_memory import ACTIVE, INSTRUCTIONS
from ai_manager.config import Settings
from ai_manager.db import Database, new_id
from ai_manager.registry import Registry
from ai_manager.web import create_app


def memory_reply(prompt):
    return json.dumps({"summary": "s", "items": [
        {"action": "new", "tier": "long", "text": "Owner prefers short letters: " + prompt["chat"][-1]["text"][:40]}]})


class OfficeServer(FakeServer):
    """Manager turns as FakeServer; title threads answer `title`, memory threads answer `memory` (callable)."""

    def __init__(self, settings, native=False):
        super().__init__(settings)
        self.title, self.memory, self.gate = "Quarterly report draft", memory_reply, None
        self.titles, self.memories, self.counter = [], [], 0
        for name in ("claude-haiku-4-9", "claude-haiku-5-10", "claude-haiku-5-5"):
            self.catalog.append({**self.catalog[0], "model": name, "isDefault": False})

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.counter += 1
        if instructions == office_chats.TITLE_INSTRUCTIONS:
            return f"title-{self.counter}"
        if instructions.startswith(INSTRUCTIONS):
            return f"memory-{self.counter}"
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)

    async def request(self, method, params, timeout=30):
        thread = (params or {}).get("threadId", "")
        if method != "turn/start" or not thread.startswith(("title-", "memory-")):
            return await super().request(method, params, timeout)
        text = params["input"][0]["text"]
        if thread.startswith("title-"):
            self.titles.append({"text": text, "model": params.get("model"), "effort": params.get("effort")})
            reply = self.title
        else:
            prompt = json.loads(text)
            self.memories.append({"prompt": prompt, "model": params.get("model"), "effort": params.get("effort")})
            reply = self.memory(prompt) if callable(self.memory) else self.memory
        queue = self.queues[thread]

        async def emit():
            if self.gate:
                await self.gate.wait()
            if isinstance(reply, Exception):
                queue.put_nowait({"method": "turn/completed", "params": {"turn": {"id": "o-turn", "status": "failed"}}})
                return
            queue.put_nowait({"method": "item/agentMessage/delta", "params": {"turnId": "o-turn", "delta": reply}})
            queue.put_nowait({"method": "turn/completed", "params": {"turn": {"id": "o-turn", "status": "completed"}}})

        asyncio.create_task(emit())
        return {"turn": {"id": "o-turn"}}


@pytest.fixture
def office(tmp_path):
    """A Chat in office mode on one project, without HTTP."""
    root = tmp_path / "work"
    (root / "folder").mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,))
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    project = registry.add(root / "folder")["id"]
    chat = Chat(db, registry, OfficeServer(settings))
    chat.restricted = True
    return db, chat, project


def seed(db, conversation_id, pairs, at=None):
    now = time.time() if at is None else at
    with db.transaction() as conn:
        for index, (role, text) in enumerate(pairs):
            conn.execute("INSERT INTO messages VALUES(?,?,?,?,'completed',NULL,?)",
                         (new_id(), conversation_id, role, text, now + index * 0.001))


def chat_rows(db, conversation_id):
    return db.all("SELECT id,role,content,status,rowid FROM messages WHERE conversation_id=? ORDER BY rowid",
                  (conversation_id,))


def memory_items(db, scope):
    row = db.one("SELECT content FROM memory WHERE project_id IS ? AND name=?", (scope, ACTIVE))
    return json.loads(row["content"])["items"] if row else []


async def settle(office):
    while office.memory_job or office.title_jobs:
        await asyncio.gather(*[j for j in [office.memory_job, *office.title_jobs.values()] if j],
                             return_exceptions=True)


def pairs(count, label="m"):
    return [("user" if i % 2 == 0 else "assistant", f"{label} {i}") for i in range(count)]


# ---- conversations: create, open, list, pin, rename, delete, search ------------------------------------------

async def test_new_open_list_pin_rename_delete_and_search(office):
    db, chat, project = office
    first = chat.conversation(project)["id"]
    # + New on an empty open chat keeps it: no pile of empty chats.
    assert chat.office.new(project)["conversation_id"] == first
    seed(db, first, [("user", "Write the invoice for ACME please"), ("assistant", "Here is the invoice")])
    second = chat.office.new(project)["conversation_id"]
    assert second != first and chat.conversation(project)["id"] == second
    assert db.one("SELECT context FROM conversations WHERE id=?", (first,))["context"] == "chat:" + first
    seed(db, second, [("user", "Plan the team offsite in May"), ("assistant", "Venue ideas")], at=time.time() + 5)
    listed = chat.office.list(project)
    assert [c["id"] for c in listed["chats"]] == [second, first]  # most recently active first
    assert listed["open_id"] == second and [c["open"] for c in listed["chats"]] == [True, False]
    # Placeholder until a title exists: the first words of the first owner message.
    assert listed["chats"][1]["title"] == "Write the invoice for ACME please" and not listed["chats"][1]["titled"]
    # Pinned chats come first even when older.
    chat.office.pin(project, first, True)
    assert [c["id"] for c in chat.office.list(project)["chats"]] == [first, second]
    assert chat.office.list(project)["chats"][0]["pinned"]
    chat.office.pin(project, first, False)
    assert [c["id"] for c in chat.office.list(project)["chats"]] == [second, first]
    chat.office.rename(project, first, "  ACME   invoice ")
    assert chat.office.list(project)["chats"][1]["title"] == "ACME invoice"
    with pytest.raises(ValueError):
        chat.office.rename(project, first, "   ")
    # Search is server-side over titles and message text, case-insensitive.
    assert [c["id"] for c in chat.office.list(project, "acme")["chats"]] == [first]
    assert [c["id"] for c in chat.office.list(project, "VENUE")["chats"]] == [second]
    assert chat.office.list(project, "nothing like this")["chats"] == []
    # Opening swaps which chat is open; the history endpoint then shows it.
    chat.office.open(project, first)
    assert chat.conversation(project)["id"] == first
    assert [m["content"] for m in chat.history(project)["messages"]][0] == "Write the invoice for ACME please"
    # Delete needs a confirm and moves the chat to the archive; the open chat falls back to the other one.
    with pytest.raises(ValueError, match="Confirm"):
        chat.office.delete(project, first, False)
    result = chat.office.delete(project, first, True)
    assert result["opened_conversation_id"] == second and chat.conversation(project)["id"] == second
    archived = db.one("SELECT c.context,a.title FROM conversations c JOIN chat_archives a ON a.conversation_id=c.id "
                      "WHERE c.id=?", (first,))
    assert archived == {"context": "archive:" + first, "title": "ACME invoice"}
    assert len(chat_rows(db, first)) == 2  # never hard-deleted
    assert [c["id"] for c in chat.office.list(project)["chats"]] == [second]
    with pytest.raises(ValueError, match="not found"):
        chat.office.open(project, first)
    # Deleting the last chat leaves a fresh empty one open.
    fresh = chat.office.delete(project, second, True)["opened_conversation_id"]
    assert fresh not in (first, second) and chat.conversation(project)["id"] == fresh
    assert chat_rows(db, fresh) == []
    await settle(chat.office)


async def test_a_reply_running_in_a_left_chat_keeps_writing_to_that_chat(office):
    db, chat, project = office
    first = chat.conversation(project)["id"]
    await chat.submit(project, "Draft a letter", first)
    second = chat.office.new(project)["conversation_id"]
    while chat.jobs:
        await asyncio.gather(*list(chat.jobs.values()), return_exceptions=True)
    assert [m["content"] for m in chat_rows(db, first)] == ["Draft a letter", "Saved streamed answer"]
    assert chat_rows(db, second) == []
    with pytest.raises(ValueError, match="Chat changed"):
        await chat.submit(project, "to the parked chat", first)  # the owner writes only into the open chat
    await settle(chat.office)


# ---- title: once, newest Haiku, low effort, placeholder on failure -------------------------------------------

async def test_title_is_written_once_with_the_newest_haiku_at_low_effort(office):
    db, chat, project = office
    conversation = chat.conversation(project)["id"]
    await chat.submit(project, "Please help me write the quarterly report for the board", conversation)
    await settle(chat.office)
    server = chat.server
    assert len(server.titles) == 1
    assert server.titles[0]["model"] == "claude-haiku-5-10" and server.titles[0]["effort"] == "low"
    assert "quarterly report for the board" in server.titles[0]["text"]
    assert chat.office.list(project)["chats"][0]["title"] == "Quarterly report draft"
    server.title = "Something else entirely"
    await chat.submit(project, "Second message", conversation)
    await settle(chat.office)
    assert len(server.titles) == 1 and chat.office.list(project)["chats"][0]["title"] == "Quarterly report draft"
    # The owner can rename; that title stays.
    chat.office.rename(project, conversation, "Board report")
    assert chat.office.list(project)["chats"][0]["title"] == "Board report"
    while chat.jobs:
        await asyncio.gather(*list(chat.jobs.values()), return_exceptions=True)


async def test_a_failed_title_call_keeps_the_placeholder_and_never_blocks_the_chat(office):
    db, chat, project = office
    chat.server.title = RuntimeError("model down")
    conversation = chat.conversation(project)["id"]
    receipt = await chat.submit(project, "Book flights to Lisbon for the conference next week", conversation)
    assert receipt["result"] == "accepted"
    await settle(chat.office)
    listed = chat.office.list(project)["chats"][0]
    assert listed["title"] == "Book flights to Lisbon for the…" and not listed["titled"]
    assert listed["title_state"] == "failed"
    while chat.jobs:
        await asyncio.gather(*list(chat.jobs.values()), return_exceptions=True)
    assert chat_rows(db, conversation)[-1]["content"] == "Saved streamed answer"
    await chat.submit(project, "again", conversation)
    await settle(chat.office)
    assert len(chat.server.titles) == 1  # set once: a failure is not retried automatically
    while chat.jobs:
        await asyncio.gather(*list(chat.jobs.values()), return_exceptions=True)


async def test_an_owner_rename_during_the_title_call_wins(office):
    db, chat, project = office
    chat.server.gate = asyncio.Event()
    conversation = chat.conversation(project)["id"]
    seed(db, conversation, [("user", "Summarise the contract")])
    assert chat.office.owner_message(project, conversation)
    await asyncio.sleep(0.05)
    chat.office.rename(project, conversation, "Contract")
    chat.server.gate.set()
    await settle(chat.office)
    assert chat.office.list(project)["chats"][0]["title"] == "Contract"


def test_title_cleanup_and_placeholder():
    assert office_chats.clean_title('Title: "Plan the May offsite."\nextra') == "Plan the May offsite"
    assert office_chats.placeholder("") == "New chat"
    assert office_chats.placeholder("one two three four five six seven") == "one two three four five six…"


# ---- memory on leaving a chat ----------------------------------------------------------------------------------

async def test_leaving_with_ten_new_messages_updates_memory_from_only_the_new_part(office):
    db, chat, project = office
    first = chat.conversation(project)["id"]
    seed(db, first, pairs(10, "old"))
    second = chat.office.new(project)
    assert second["memory_update"]["started"] and second["memory_update"]["new_messages"] == 10
    await settle(chat.office)
    server = chat.server
    assert len(server.memories) == 1
    assert [m["text"] for m in server.memories[0]["prompt"]["chat"]] == [f"old {i}" for i in range(10)]
    route = chat_memory.route(db, server)
    assert (server.memories[0]["model"], server.memories[0]["effort"]) == (route["model"], route["effort"])
    assert [i["text"] for i in memory_items(db, project)] == ["Owner prefers short letters: old 9"]
    before = chat_rows(db, first)
    meta = chat.office.meta(first)
    assert meta["memory_mark"] == before[-1]["rowid"] and meta["memory_at"]
    # The chat itself is unchanged: no archive, no summary message, no compaction.
    assert len(before) == 10 and not db.one("SELECT 1 FROM chat_archives")
    # Back in the first chat, 10 more messages, leave again: only the 10 new ones are read.
    chat.office.open(project, first)
    seed(db, first, pairs(10, "new"))
    chat.office.open(project, second["conversation_id"])
    await settle(chat.office)
    assert len(server.memories) == 2
    assert [m["text"] for m in server.memories[1]["prompt"]["chat"]] == [f"new {i}" for i in range(10)]
    assert "Owner prefers short letters: old 9" in server.memories[1]["prompt"]["existing_memory"]
    assert len(chat_rows(db, first)) == 20 and chat.conversation(project)["id"] == second["conversation_id"]
    # Every chat, including a new one, gets the instance's shared chat memory as context.
    fresh = chat.office.new(project)["conversation_id"]
    assert chat.conversation(project)["id"] == fresh
    assert "Owner prefers short letters: new 9" in chat.context(project)["chat_memory"]


async def test_the_or_rule_threshold_or_a_few_messages_after_the_cool_down(office):
    db, chat, project = office
    first = chat.conversation(project)["id"]
    seed(db, first, pairs(9))
    # 9 new messages and the chat is brand new: neither rule holds.
    left = chat.office.left(project, first)
    assert not left["started"] and left["new_messages"] == 9
    # 15 minutes after the chat started (no memory update yet): 2+ new messages are enough.
    later = time.time() + 15 * 60 + 1
    assert chat.office.left(project, first, now=later)["started"]
    await settle(chat.office)
    mark_at = chat.office.meta(first)["memory_at"]
    # Right after an update: 2 new messages are not enough before the cool-down passes ...
    seed(db, first, pairs(2, "two"))
    assert not chat.office.left(project, first, now=mark_at + 60)["started"]
    # ... one new message is not enough even after it ...
    assert chat.office.left(project, first, now=mark_at + 15 * 60)["started"]
    await settle(chat.office)
    seed(db, first, pairs(1, "one"))
    at = chat.office.meta(first)["memory_at"]
    assert not chat.office.left(project, first, now=at + 3600)["started"]
    # ... while 10 new messages start one at once, cool-down or not.
    seed(db, first, pairs(9, "ten"))
    assert chat.office.left(project, first, now=at + 1)["started"]
    await settle(chat.office)
    assert len(chat.server.memories) == 3


async def test_leave_numbers_are_chat_memory_settings(office):
    db, chat, project = office
    saved = chat_memory.load_settings(db)
    assert (saved["leave_messages"], saved["leave_min_messages"], saved["leave_minutes"]) == (10, 2, 15)
    await chat.server.start()
    chat_memory.save_settings(db, chat.server, {**saved, "leave_messages": 3, "leave_min_messages": 1,
                                                "leave_minutes": 0})
    with pytest.raises(ValueError, match="leave_minutes"):
        chat_memory.save_settings(db, chat.server, {**chat_memory.load_settings(db), "leave_minutes": -1})
    first = chat.conversation(project)["id"]
    seed(db, first, pairs(1))
    assert chat.office.left(project, first)["started"]  # 1 message, 0 minutes
    await settle(chat.office)
    # An older client that omits the leave fields keeps the saved values.
    current = chat_memory.load_settings(db)
    chat_memory.save_settings(db, chat.server, {k: current[k] for k in ("revision", "model", "effort",
                                                                         "threshold_tokens")})
    assert chat_memory.load_settings(db)["leave_messages"] == 3


async def test_only_one_memory_job_per_instance_and_a_failure_keeps_the_mark(office):
    db, chat, project = office
    chat.server.gate = asyncio.Event()
    first = chat.conversation(project)["id"]
    seed(db, first, pairs(10, "a"))
    second = chat.office.new(project)["conversation_id"]
    seed(db, second, pairs(10, "b"))
    third = chat.office.new(project)
    assert third["memory_update"] == {"conversation_id": second, "new_messages": 10, "started": False,
                                      "reason": "a memory job is already running"}
    chat.server.gate.set()
    await settle(chat.office)
    assert len(chat.server.memories) == 1
    chat.server.gate = None
    chat.server.memory = "not json"
    chat.office.open(project, second)
    chat.office.open(project, first)
    await settle(chat.office)
    assert chat.office.meta(second).get("memory_mark", 0) == 0  # failed: the same messages are read next time
    assert db.one("SELECT 1 FROM events WHERE kind='chat_memory.update_failed'")
    assert len(chat_rows(db, second)) == 10


async def test_automatic_compaction_still_works_in_a_chat_parked_meanwhile(office):
    db, chat, project = office
    first = chat.conversation(project)["id"]
    seed(db, first, [("user", "x" * 100), ("assistant", "y" * 100)])
    messages = chat.compactor.transcript(first)
    result = chat_memory.validate(json.loads(memory_reply({"chat": [{"text": "z"}]})), {})
    chat.office.new(project)
    applied = chat.compactor.apply(project, first, messages[-1]["id"], result, automatic=True)
    assert applied["conversation_id"] == first
    assert db.one("SELECT context FROM conversations WHERE id=?", (first,))["context"] == "chat:" + first
    await settle(chat.office)


# ---- files stay with their chat, as references ------------------------------------------------------------------

def owner_app(tmp_path, restricted=True):
    project = tmp_path / "work" / "folder"
    project.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "data", project_roots=(tmp_path / "work",), restricted=restricted,
                        project_path=project if restricted else None, secure_cookie=False)
    return settings, create_app(settings, OfficeServer(settings))


def sign_in(browser, settings):
    token, owner = (settings.data_dir / "setup-token").read_text(), {"username": "owner",
                                                                       "password": secrets.token_urlsafe(16)}
    assert browser.post("/api/setup", json={**owner, "token": token}).status_code == 200
    browser.headers["X-CSRF-Token"] = browser.post("/api/login", json=owner).json()["csrf"]


def wait_idle(app):
    for _ in range(200):
        if not app.state.chat.jobs and not app.state.chat.office.title_jobs and not app.state.chat.office.memory_job:
            return
        time.sleep(0.02)


def test_http_round_trip_and_files_stay_with_their_chat(tmp_path):
    settings, app = owner_app(tmp_path)
    with TestClient(app, base_url="http://localhost") as browser:
        sign_in(browser, settings)
        first = browser.get("/api/history").json()["conversation"]["id"]
        uploaded = browser.post("/api/attachments", params={"name": "budget.txt", "conversation_id": first},
                                content=b"Budget 2027: travel 4000", headers={"Content-Type": "text/plain"}).json()
        sent = browser.post("/api/chat", json={"content": "Check the travel budget", "conversation_id": first,
                                               "attachment_ids": [uploaded["id"]]})
        assert sent.status_code == 200
        wait_idle(app)
        # The history shows a small card (name, type), never the file content.
        owner = next(m for m in browser.get("/api/history").json()["messages"] if m["role"] == "user")
        assert owner["content"] == "Check the travel budget"
        assert [(a["name"], a["mime"]) for a in owner["attachments"]] == [("budget.txt", "text/plain")]
        assert "travel 4000" not in json.dumps(owner)
        listed = browser.get("/api/office/chats").json()
        assert listed["open_id"] == first and listed["chats"][0]["title"] == "Quarterly report draft"
        new = browser.post("/api/office/chats").json()["conversation_id"]
        assert new != first and browser.get("/api/history").json()["conversation"]["id"] == new
        # A file of another chat cannot be sent from this one.
        refused = browser.post("/api/chat", json={"content": "x", "conversation_id": new,
                                                  "attachment_ids": [uploaded["id"]]})
        assert refused.status_code == 409 and "different chat" in refused.text
        assert browser.patch(f"/api/office/chats/{first}", json={"pinned": True}).json()["pinned"]
        assert browser.patch(f"/api/office/chats/{first}", json={"title": "Budget"}).json()["title"] == "Budget"
        assert browser.patch(f"/api/office/chats/{first}", json={}).status_code == 422
        assert [c["title"] for c in browser.get("/api/office/chats", params={"search": "travel"}).json()["chats"]] \
            == ["Budget"]
        # Reopening the chat later keeps its file: the card, the download, and the model's short reference.
        assert browser.post(f"/api/office/chats/{first}/open").json()["conversation_id"] == first
        owner = next(m for m in browser.get("/api/history").json()["messages"] if m["role"] == "user")
        assert browser.get("/api/attachments/" + owner["attachments"][0]["id"]).content == b"Budget 2027: travel 4000"
        # The model got a short reference to the file (name, type, path), never its content.
        prompt = json.loads(app.state.chat.server.prompts[0])
        files = prompt["context"]["chat_files"]["files"]
        assert [(f["name"], f["mime"]) for f in files] == [("budget.txt", "text/plain")] and files[0]["path"]
        assert "travel 4000" not in json.dumps(prompt)
        assert [f["name"] for f in app.state.chat.office.files(first)] == ["budget.txt"]
        assert browser.post(f"/api/office/chats/{first}/delete", json={"confirmed": False}).status_code == 409
        assert browser.post(f"/api/office/chats/{first}/delete", json={"confirmed": True}).status_code == 200
        assert [c["id"] for c in browser.get("/api/office/chats").json()["chats"]] == [new]


def test_main_has_no_office_chats_and_its_settings_and_chat_are_unchanged(tmp_path):
    settings, app = owner_app(tmp_path, restricted=False)
    with TestClient(app, base_url="http://localhost") as browser:
        sign_in(browser, settings)
        assert browser.get("/api/auth-status").json()["office"] is False
        for method, path in (("get", "/api/office/chats"), ("post", "/api/office/chats"),
                             ("post", "/api/office/chats/x/open"), ("patch", "/api/office/chats/x"),
                             ("post", "/api/office/chats/x/delete")):
            response = getattr(browser, method)(path, **({"json": {}} if method != "get" else {}))
            assert response.status_code == 403, path
        first = browser.get("/api/history").json()["conversation"]["id"]
        assert browser.post("/api/chat", json={"content": "Hello main", "conversation_id": first}).status_code == 200
        wait_idle(app)
        assert app.state.chat.server.titles == [] and not app.state.db.one(
            "SELECT 1 FROM settings WHERE key LIKE 'office_chat:%'")
        assert browser.get("/api/history").json()["conversation"]["id"] == first
    html = (office_chats.__file__.rsplit("/", 1)[0] + "/static/")
    style = open(html + "style.css").read()
    # Every office element is hidden outside office mode.
    assert "body:not(.office) .office-only{display:none!important}" in style
    page = open(html + "index.html").read()
    for element in ('id="chat-sidebar" class="office-only"', 'id="office-bar" class="office-only"',
                    'id="chat-sidebar-backdrop" class="office-only"', 'id="chat-memory-leave"',
                    'id="office-confirm" class="office-only"'):
        assert element in page
    assert 'fieldset class="office-only" id="chat-memory-leave"' in page

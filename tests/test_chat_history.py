import asyncio
import time

import pytest
from fake_codex import FakeServer

from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry


@pytest.fixture
def chat(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", project_roots=(), secure_cookie=False)
    settings.prepare()
    db = Database(settings.db_path)
    return Chat(db, Registry(db, ()), FakeServer(settings))


def seed(chat, project_id=None, count=2):
    conversation = chat.conversation(project_id)
    with chat.db.transaction() as conn:
        conn.execute(
            "UPDATE conversations SET thread_id=?,summary=? WHERE id=?",
            ("saved-thread", "old summary", conversation["id"]),
        )
        for i in range(count):
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (
                    f"{conversation['id']}-{i}",
                    conversation["id"],
                    "user" if i % 2 == 0 else "assistant",
                    f"message {i} searchable needle",
                    "completed",
                    None,
                    time.time(),
                ),
            )
    return conversation["id"], f"{conversation['id']}-{count - 1}"


def test_archive_retains_all_messages_thread_and_search_across_restart(chat):
    old, last = seed(chat, count=425)
    before = chat.db.all("SELECT * FROM messages ORDER BY rowid")
    result = chat.reset(None, old, last, True, archive=True, title="Sprint decisions")
    fresh = chat.history()
    assert fresh["messages"] == [] and fresh["conversation"]["id"] == result["conversation_id"] != old
    assert fresh["conversation"]["thread_id"] is None and fresh["conversation"]["summary"] == ""
    assert chat.db.all("SELECT * FROM messages ORDER BY rowid") == before
    archived = chat.saved_history(old)
    assert archived["conversation"]["thread_id"] == "saved-thread"
    assert archived["conversation"]["summary"] == "old summary"
    restored = Chat(Database(chat.db.path), chat.registry, chat.server)
    for query in ("decisions", "NEEDLE"):
        assert restored.search_history(search=query)["conversations"][0]["title"] == "Sprint decisions"
    ids = []
    page = restored.saved_history(old)
    while True:
        ids = [m["id"] for m in page["messages"]] + ids
        if not page["has_more"]:
            break
        page = restored.saved_history(old, before=page["messages"][0]["id"])
    assert ids == [m["id"] for m in before]
    matching = restored.saved_history(old, search="message 0 ")
    assert [m["id"] for m in matching["messages"]] == [before[0]["id"]]
    assert not matching["has_more"]
    assert not restored.search_history(search="missing")["conversations"]


def test_clear_preserves_archives_and_other_contexts_and_resets_thread(chat, tmp_path):
    old, last = seed(chat)
    chat.reset(None, old, last, True, archive=True)
    project_path = tmp_path / "project"
    project_path.mkdir()
    chat.registry.roots = (tmp_path,)
    project = chat.registry.add(project_path)
    other, _ = seed(chat, project["id"])
    current, last = seed(chat)
    chat.reset(None, current, last, True)
    assert chat.history()["messages"] == []
    assert chat.history()["conversation"]["thread_id"] is None
    assert chat.saved_history(old)["messages"]
    assert chat.history(project["id"])["messages"]
    assert not chat.db.one("SELECT id FROM conversations WHERE id=?", (current,))
    assert len(chat.search_history()["conversations"]) == 2
    assert [c["id"] for c in chat.search_history(project["id"])["conversations"]] == [other]
    with pytest.raises(ValueError, match="selected context"):
        chat.saved_history(old, project["id"])
    with pytest.raises(ValueError, match="Message was not found"):
        chat.saved_history(old, before=chat.history(project["id"])["messages"][0]["id"])


@pytest.mark.parametrize("archive", [False, True])
def test_confirmation_stale_and_running_guards_leave_chat_unchanged(chat, archive):
    old, last = seed(chat)
    before = chat.db.all("SELECT * FROM messages")
    for confirmed, identity, tail, reason in [
        (False, old, last, "Confirm"),
        (True, "wrong", last, "Chat changed"),
        (True, old, "stale", "New messages"),
    ]:
        with pytest.raises(ValueError, match=reason):
            chat.reset(None, identity, tail, confirmed, archive)
        assert chat.db.all("SELECT * FROM messages") == before
    with chat.db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,agent_id,status,started_at) VALUES('busy','manager','running',?)", (time.time(),)
        )
        conn.execute("UPDATE messages SET execution_id=? WHERE id=?", ("busy", last))
    with pytest.raises(ValueError, match="Wait for the response"):
        chat.reset(None, old, last, True, archive)
    with chat.db.transaction() as conn:
        conn.execute("UPDATE executions SET finished_at=?,status='completed' WHERE id='busy'", (time.time(),))
    chat.reset(None, old, last, True, archive)
    with pytest.raises(ValueError, match="Chat changed"):
        chat.reset(None, old, last, True, archive)


def test_archive_transaction_rolls_back_if_recording_fails(chat, monkeypatch):
    old, last = seed(chat)
    before = chat.db.all("SELECT * FROM conversations")

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(chat.db, "event", fail)
    with pytest.raises(RuntimeError):
        chat.reset(None, old, last, True, archive=True, title="Atomic archive")
    assert chat.db.all("SELECT * FROM conversations") == before
    assert chat.db.all("SELECT * FROM chat_archives") == []
    assert len(chat.history()["messages"]) == 2


@pytest.mark.asyncio
async def test_submit_waiting_for_routes_cannot_append_to_archived_chat(chat, monkeypatch):
    old, last = seed(chat)
    ready, resume = asyncio.Event(), asyncio.Event()

    async def routes():
        ready.set()
        await resume.wait()
        return {"interactive": {"model": "test-model", "effort": "low"}}

    monkeypatch.setattr(chat, "routes", routes)
    pending = asyncio.create_task(chat.submit(None, "new message"))
    await ready.wait()
    chat.reset(None, old, last, True, archive=True)
    resume.set()
    with pytest.raises(ValueError, match="Chat changed"):
        await pending
    assert len(chat.saved_history(old)["messages"]) == 2
    assert chat.history()["messages"] == []

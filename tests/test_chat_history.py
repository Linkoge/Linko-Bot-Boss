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


@pytest.mark.smoke
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
    assert chat.db.one("SELECT context FROM conversations WHERE id=?", (current,))["context"] == "trash:" + current
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
async def test_durable_submission_blocks_archive_while_model_setup_is_waiting(chat, monkeypatch):
    old, _ = seed(chat)
    ready, resume = asyncio.Event(), asyncio.Event()

    async def start():
        ready.set()
        await resume.wait()

    monkeypatch.setattr(chat.server, "start", start)
    request = await chat.submit(None, "new message")
    await ready.wait()
    with pytest.raises(ValueError, match="response to finish"):
        chat.reset(None, old, request["message_id"], True, archive=True)
    assert len(chat.history()["messages"]) == 4
    resume.set()
    await asyncio.gather(*list(chat.jobs.values()))
    chat.reset(None, old, request["message_id"], True, archive=True)
    assert len(chat.saved_history(old)["messages"]) == 4
    assert chat.history()["messages"] == []


def test_trash_single_slot_swap_empty_and_restart_preserve_evidence(chat):
    old, last = seed(chat)
    old_messages = chat.db.all("SELECT * FROM messages")
    chat.reset(None, old, last, True)
    second, tail = seed(chat)
    chat.reset(None, second, tail, True)
    assert chat.history()["trash"]["conversation_id"] == second
    assert chat.db.one("SELECT context FROM conversations WHERE id=?", (old,))["context"] == "evidence:" + old
    assert chat.db.all("SELECT * FROM messages WHERE conversation_id=?", (old,)) == old_messages
    current, last = seed(chat)
    chat.change_trash(None, current, last, second, True, restore=True)
    assert chat.history()["conversation"]["id"] == second
    assert chat.history()["trash"]["conversation_id"] == current
    assert chat.history()["conversation"]["thread_id"] == "saved-thread"
    chat.change_trash(None, second, tail, current, True, restore=True)
    assert chat.history()["conversation"]["id"] == current
    restarted = Chat(Database(chat.db.path), chat.registry, chat.server)
    restarted.change_trash(None, current, last, second, True)
    assert restarted.history()["trash"] is None
    assert len(chat.db.all("SELECT * FROM conversations")) == 3
    assert len(chat.db.all("SELECT * FROM messages")) == 6
    assert len(chat.search_history()["conversations"]) == 1
    with pytest.raises(ValueError, match="selected context"):
        chat.saved_history(second)


def test_trash_restore_empty_current_stale_running_and_atomic_failure(chat, monkeypatch):
    old, last = seed(chat)
    chat.reset(None, old, last, True)
    fresh = chat.history()["conversation"]["id"]
    for identity, tail, trash, confirmed, reason in [
        (fresh, None, old, False, "Confirm"), (fresh, None, "wrong", True, "Trash changed"),
        ("wrong", None, old, True, "Chat changed"), (fresh, "stale", old, True, "New messages")]:
        with pytest.raises(ValueError, match=reason):
            chat.change_trash(None, identity, tail, trash, confirmed, restore=True)
    def fail(*args, **kwargs):
        raise RuntimeError("fixture persistence failed")
    with monkeypatch.context() as m:
        m.setattr(chat.db, "event", fail)
        with pytest.raises(RuntimeError):
            chat.change_trash(None, fresh, None, old, True, restore=True)
    assert chat.history()["conversation"]["id"] == fresh
    assert chat.history()["trash"]["conversation_id"] == old
    with chat.db.transaction() as conn:
        conn.execute("UPDATE messages SET status='streaming' WHERE id=?", (last,))
    with pytest.raises(ValueError, match="finish"):
        chat.change_trash(None, fresh, None, old, True, restore=True)
    with chat.db.transaction() as conn:
        conn.execute("UPDATE messages SET status='completed' WHERE id=?", (last,))
    chat.change_trash(None, fresh, None, old, True, restore=True)
    assert chat.history()["conversation"]["id"] == old and chat.history()["trash"] is None


def test_trash_context_isolation_keeps_unrelated_chat_and_task_evidence(chat, tmp_path):
    project_path = tmp_path / "other"
    project_path.mkdir()
    chat.registry.roots = (tmp_path,)
    project = chat.registry.add(project_path)["id"]
    other, other_last = seed(chat, project)
    old, last = seed(chat)
    chat.reset(None, old, last, True)
    chat.reset(project, other, other_last, True)
    before = chat.db.all("SELECT * FROM conversations WHERE project_id=?", (project,))
    assert chat.history(project)["trash"]["conversation_id"] == other
    fresh = chat.history()["conversation"]["id"]
    with pytest.raises(ValueError, match="Trash changed"):
        chat.change_trash(None, fresh, None, other, True)
    chat.change_trash(None, fresh, None, old, True)
    assert chat.db.all("SELECT * FROM conversations WHERE project_id=?", (project,)) == before
    assert chat.db.one("SELECT id FROM messages WHERE conversation_id=?", (old,))


@pytest.mark.smoke
@pytest.mark.parametrize('count', [11, 24, 425])
def test_partial_archive_keeps_latest_ten_and_is_searchable_after_restart(chat, count):
    current, last = seed(chat, count=count)
    before = chat.db.all('SELECT * FROM messages ORDER BY rowid')
    chat.db.set_setting('manager_context:' + current, {'thread_id': 'saved-thread'})
    result = chat.reset(None, current, last, True, archive=True, title='Earlier', keep_last=10)
    assert result['conversation_id'] == current
    restarted = Chat(Database(chat.db.path), chat.registry, chat.server)
    active = restarted.history()
    assert [m['id'] for m in active['messages']] == [m['id'] for m in before[-10:]]
    assert active['conversation']['thread_id'] is None
    assert active['conversation']['summary'] == ''
    assert restarted.db.setting('manager_context:' + current) is None
    archive = next(c for c in restarted.search_history()['conversations'] if c['archived_at'])
    assert archive['message_count'] == count - 10
    assert archive['title'] == 'Earlier'
    assert restarted.saved_history(archive['id'])['conversation']['thread_id'] == 'saved-thread'
    assert restarted.search_history(search='message 0 ')['conversations'][0]['id'] == archive['id']
    after = restarted.db.all('SELECT * FROM messages ORDER BY rowid')
    assert [{**m, 'conversation_id': current} for m in after] == before
    with pytest.raises(ValueError, match='no earlier'):
        restarted.reset(None, current, last, True, archive=True, keep_last=10)


@pytest.mark.parametrize('count', [0, 1, 10])
def test_partial_archive_short_chat_is_unchanged(chat, count):
    current, last = seed(chat, count=count)
    before = chat.db.all('SELECT * FROM conversations')
    with pytest.raises(ValueError, match='empty|no earlier'):
        chat.reset(None, current, last, True, archive=True, keep_last=10)
    assert chat.db.all('SELECT * FROM conversations') == before
    assert not chat.db.all('SELECT * FROM chat_archives')


def test_partial_archive_hidden_messages_and_atomic_guards(chat, monkeypatch):
    current, last = seed(chat, count=24)
    chat.delete_message(None, current, last, True)
    last = current + '-22'
    original = chat.db.all('SELECT * FROM messages ORDER BY rowid')
    for identity, tail, confirmed, reason in [
        (current, last, False, 'Confirm'), ('wrong', last, True, 'Chat changed'),
        (current, 'stale', True, 'New messages'),
    ]:
        with pytest.raises(ValueError, match=reason):
            chat.reset(None, identity, tail, confirmed, archive=True, keep_last=10)
    chat.db.run("UPDATE messages SET status='streaming' WHERE id=?", (last,))
    with pytest.raises(ValueError, match='finish'):
        chat.reset(None, current, last, True, archive=True, keep_last=10)
    chat.db.run("UPDATE messages SET status='completed' WHERE id=?", (last,))
    def fail(*args, **kwargs):
        raise RuntimeError('fixture persistence failure')
    with monkeypatch.context() as patch:
        patch.setattr(chat.db, 'event', fail)
        with pytest.raises(RuntimeError):
            chat.reset(None, current, last, True, archive=True, keep_last=10)
    assert chat.db.all('SELECT * FROM messages ORDER BY rowid') == original
    assert not chat.db.all('SELECT * FROM chat_archives')
    chat.reset(None, current, last, True, archive=True, keep_last=10)
    assert [m['id'] for m in chat.history()['messages']] == [current + f'-{i}' for i in range(13, 23)]


def test_active_history_latest_older_since_and_reset(chat):
    conversation, last = seed(chat, count=75)
    latest = chat.history()
    assert [m['id'] for m in latest['messages']] == [f'{conversation}-{i}' for i in range(45, 75)]
    assert latest['has_more'] and latest['cursor'] == last
    older = chat.history(before=latest['messages'][0]['id'])
    assert [m['id'] for m in older['messages']] == [f'{conversation}-{i}' for i in range(15, 45)]
    with chat.db.transaction() as conn:
        for i in range(75, 146):
            conn.execute("INSERT INTO messages VALUES(?,?,'assistant',?,'completed',NULL,?)",
                         (f'{conversation}-{i}', conversation, str(i), time.time()))
    ids, cursor = [], last
    while True:
        page = chat.history(since=cursor)
        assert page['incremental'] and len(page['messages']) <= 30
        ids.extend(m['id'] for m in page['messages'])
        cursor = page['cursor']
        if not page['sync_has_more']:
            break
    assert ids == [f'{conversation}-{i}' for i in range(75, 146)]
    assert not chat.history(since=cursor)['messages']
    assert not chat.history(since=cursor)['updates']
    chat.delete_message(None, conversation, f'{conversation}-100', True)
    assert f'{conversation}-100' in chat.history(since=cursor)['deleted_message_ids']
    chat.reset(None, conversation, cursor, True, archive=True, keep_last=10)
    partial = chat.history(since=cursor)
    assert partial['first_position'] == chat.db.one('SELECT rowid AS p FROM messages WHERE id=?',
                                                   (f'{conversation}-136',))['p']
    chat.reset(None, conversation, cursor, True, archive=True)
    fresh = chat.history(since=cursor)
    assert fresh['conversation']['id'] != conversation and fresh['messages'] == [] and not fresh['incremental']
    with pytest.raises(ValueError, match='older messages or newer'):
        chat.history(before=last, since=last)


@pytest.mark.asyncio
async def test_since_refreshes_watched_reply_that_finished_without_new_rows(chat):
    receipt = await chat.submit(None, 'Mutable reply')
    await asyncio.wait_for(asyncio.gather(*chat.jobs.values()), 5)
    initial = chat.history()
    assistant = next(m for m in initial['messages'] if m['role'] == 'assistant')
    # A later, unrelated row advances the cursor past this execution.
    chat.db.run("INSERT INTO messages VALUES('later',?,'assistant','Later','completed',NULL,?)",
                (initial['conversation']['id'], time.time()))
    chat.db.run('UPDATE executions SET output=? WHERE id=?', ('Final corrected output', receipt['execution_id']))
    chat.db.run('UPDATE messages SET content=? WHERE id=?', ('Final corrected output', assistant['id']))
    page = chat.history(since='later', watch=[assistant['id']])
    assert page['messages'] == []
    updated = next(m for m in page['updates'] if m['id'] == assistant['id'])
    assert updated['content'] == 'Final corrected output' and updated['status'] == 'completed'
    assert updated['finished_at'] is not None

"""Global navigation recency shares meaningful event transactions, not identity."""

import json

import pytest
from test_minimal_flow import system as system

from ai_manager.db import Database
from ai_manager.tasks import Tasks


def test_global_backfill_is_once_preserves_rows_and_survives_history_removal(system):
    db, registry, chat, _ = system
    global_chat = chat.conversation(None)
    project_chat = chat.conversation(registry.list()[0]['id'])
    with db.transaction() as conn:
        conn.execute("DELETE FROM settings WHERE key='global_last_used_at'")
        for kind, project, at in [('chat.queued', None, 100), ('chat.queued', None, 200),
                                  ('chat.finished', None, 900), ('settings.changed', None, 1000),
                                  ('chat.queued', registry.list()[0]['id'], 1100)]:
            conn.execute("INSERT INTO events(kind,project_id,payload,created_at) VALUES(?,?,'{}',?)", (kind, project, at))
    before = {table: db.all(f'SELECT * FROM {table}') for table in ('projects', 'conversations', 'events')}
    reopened = Database(db.path)
    assert reopened.setting('global_last_used_at') == 200
    assert {table: reopened.all(f'SELECT * FROM {table}') for table in before} == before
    assert global_chat['project_id'] is None and global_chat['context'] == 'global'
    assert global_chat['id'] != project_chat['id']
    with reopened.transaction() as conn:
        conn.execute("DELETE FROM events WHERE project_id IS NULL")
        conn.execute("DELETE FROM conversations WHERE id=?", (global_chat['id'],))
    assert Database(db.path).setting('global_last_used_at') == 200
    assert len(registry.list()) == 2  # No synthetic project was registered.


async def test_only_accepted_submissions_and_assignments_update_recency(system, monkeypatch):
    db, registry, chat, _ = system
    monkeypatch.setattr(chat, 'launch', lambda *args: None)
    clock = [1000]
    monkeypatch.setattr('ai_manager.db.time.time', lambda: clock[0])
    project = registry.list()[0]
    chat.history(None)
    chat.context(None)
    registry.update(project['id'], project['revision'], {'display_name': 'Renamed'})
    db.set_setting('unrelated', 'preserved')
    assert db.setting('global_last_used_at') == 0
    renamed = registry.get(project['id'])
    assert renamed['last_used_at'] == 0
    for field in ('id', 'name', 'path', 'repository', 'agent_id', 'created_at'):
        assert renamed[field] == project[field]
    with pytest.raises(ValueError):
        await chat.submit(None, ' ')
    await chat.submit(None, 'Portfolio discussion', mode='conversation')
    assert db.setting('global_last_used_at') == 1000
    assert db.one("SELECT project_id FROM executions WHERE agent_id='manager'")['project_id'] is None
    clock[0] = 1100
    with pytest.raises(ValueError, match='already running'):
        await chat.submit(None, 'Rejected overlapping request')
    assert db.setting('global_last_used_at') == 1000
    await chat.submit(project['id'], 'Project discussion')
    assert registry.get(project['id'])['last_used_at'] == 1100
    assert db.setting('global_last_used_at') == 1000
    clock[0] = 1200
    task = Tasks(db).add(project['id'], 'Accepted assignment')
    assert registry.get(project['id'])['last_used_at'] == 1200
    clock[0] = 1300
    claimed = Tasks(db).claim(2)
    assert claimed['id'] == task['id']
    assert registry.get(project['id'])['last_used_at'] == 1300
    assert Database(db.path).setting('global_last_used_at') == 1000
    assert db.setting('unrelated') == 'preserved'


def test_global_recency_rolls_back_and_is_monotonic(system, monkeypatch):
    db, _, _, _ = system
    monkeypatch.setattr('ai_manager.db.time.time', lambda: 300)
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            db.event(conn, 'chat.queued', {}, None)
            raise RuntimeError('Rejected transaction')
    assert db.setting('global_last_used_at') == 0
    with db.transaction() as conn:
        db.event(conn, 'chat.queued', {}, None)
    monkeypatch.setattr('ai_manager.db.time.time', lambda: 200)
    with db.transaction() as conn:
        db.event(conn, 'chat.queued', {}, None)
        db.event(conn, 'chat.finished', {}, None)
    assert Database(db.path).setting('global_last_used_at') == 300
    assert json.loads(db.one("SELECT value FROM settings WHERE key='global_last_used_at'")['value']) == 300

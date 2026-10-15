import asyncio
import hashlib
import json
import time

import pytest
from fake_codex import FakeServer
from test_minimal_flow import NativeFixture
from test_minimal_flow import system as system_fixture

from ai_manager.chat import Chat
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.orchestration import MANAGER_TOOL, Scheduler
from ai_manager.pipeline import Pipeline

system = system_fixture


@pytest.mark.asyncio
async def test_historical_transport_hidden_without_rewriting_user_or_audit(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project['id'])['id']
    task = Tasks(db).add(project['id'], 'secret bulk objective ' * 1000, origin)
    claimed = Tasks(db).claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.end(claimed['lease_owner'], 'done', '', 'internal report ' * 5000)
    with db.transaction() as conn:
        conn.execute("INSERT INTO messages VALUES(?,?,'worker',?,'completed',NULL,?)", ('result:' + claimed['lease_owner'], origin, 'Codex result:\n' + json.dumps({'objective': task['description']}), time.time()))
        for identity, role, content in [('owner-json', 'user', 'Codex result:\n{"task_id":"example","objective":"literal code"}'), ('real-answer', 'assistant', 'A real conversation answer'), ('unproven', 'worker', 'Keep unproven historical content')]:
            conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,NULL,?)', (identity, origin, role, content, 'completed', time.time()))
    before = db.all('SELECT * FROM messages ORDER BY rowid')
    reopened = Chat(Database(db.path), registry, chat.server)
    for rows in [reopened.history(project['id'])['messages'], reopened.saved_history(origin, project['id'])['messages']]:
        assert [m['id'] for m in rows] == ['owner-json', 'real-answer', 'unproven']
    prompt, _ = reopened.prompt(reopened.conversation(project['id']), 'Hello', 'auto', 'fresh', 'fresh-thread', None)
    assert [m['id'] for m in prompt['conversation_history']] == ['owner-json', 'real-answer', 'unproven']
    assert 'secret bulk objective' not in json.dumps(prompt)
    assert db.all('SELECT * FROM messages ORDER BY rowid') == before
    scheduler = Scheduler(db, registry, settings, workers, reopened)
    result = await scheduler.manager_action(project['id'], {'tool': 'manager_action', 'arguments': {'action': 'read_result', 'execution_id': claimed['lease_owner']}, 'mode': 'conversation'})
    assert result['next_offset'] == 12000
    second = await scheduler.manager_action(project['id'], {'tool': 'manager_action', 'arguments': {'action': 'read_result', 'execution_id': claimed['lease_owner'], 'offset': 12000}})
    assert second['output'] == ('internal report ' * 5000)[12000:24000]
    with pytest.raises(ValueError, match='another project'):
        await scheduler.manager_action(registry.list()[1]['id'], {'tool': 'manager_action', 'arguments': {'action': 'read_result', 'execution_id': claimed['lease_owner']}})
    archived = reopened.reset(project['id'], origin, 'unproven', True, archive=True)
    fresh, _ = reopened.prompt(reopened.conversation(project['id']), 'New chat', 'auto', 'new', 'new-thread', None)
    assert fresh['conversation_history'] == []
    assert archived['conversation_id'] != origin
    assert db.all('SELECT * FROM messages ORDER BY rowid') == before


@pytest.mark.asyncio
async def test_instruction_upgrade_rotates_native_thread_and_compact_failure_preserves_status(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    origin = chat.conversation(project['id'])['id']
    old_schema = hashlib.sha256(json.dumps(MANAGER_TOOL, sort_keys=True).encode()).hexdigest()
    db.set_setting('manager_tool_schema:' + origin, old_schema)
    db.set_setting('global_instructions', 'Brief routine replies; preserve blockers')
    with db.transaction() as conn:
        conn.execute("UPDATE conversations SET thread_id='contaminated-old-thread' WHERE id=?", (origin,))
    task = Tasks(db).add(project['id'], 'large objective ' * 1000, origin)
    claimed = Tasks(db).claim()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    workers.end(claimed['lease_owner'], 'failed', 'error' * 1000, 'release_id=example; NOT deployed. full report retained')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET blocker=? WHERE id=?', ('blocker' * 1000, task['id']))
    await chat.deliver_results()
    await asyncio.gather(*list(chat.jobs.values()))
    prompt = json.loads(chat.server.prompts[0])
    notice = prompt['worker_result']
    assert notice['status'] == 'failed' and notice['reason_truncated']
    assert notice['deployment'].startswith('unverified')
    assert 'large objective' not in json.dumps(prompt)
    assert prompt['context']['instructions'] == 'Brief routine replies; preserve blockers'
    assert chat.conversation(project['id'])['thread_id'] != 'contaminated-old-thread'
    assert [m['role'] for m in chat.history(project['id'])['messages']] == ['assistant']
    reopened = Chat(Database(db.path), registry, FakeServer(settings))
    # New native session for the same conversation retains actual owner-facing content only.
    fresh, _ = reopened.prompt(reopened.conversation(project['id']), 'Status', 'auto', 'next', 'next-thread', None)
    assert all(m['role'] == 'assistant' for m in fresh['conversation_history'])
    assert 'large objective' not in json.dumps(fresh)

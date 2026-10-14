"""FIFO acceptance, native thread ownership and recovery with no provider calls."""
import asyncio

import pytest
from test_minimal_flow import system as system_fixture
from test_recovery import Faults, eligible, settle

from ai_manager.chat import Chat
from ai_manager.db import Database

system = system_fixture


def complete(chat, faults, execution_id):
    row = chat.db.one('SELECT * FROM executions WHERE id=?', (execution_id,))
    turn = faults.turns[row['thread_id']][-1]
    turn['status'] = 'completed'
    chat.server.queues[row['thread_id']].put_nowait({'method': 'turn/completed', 'params': {'turn': turn}})


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_busy_fifo_modes_history_and_completion_race(system):
    db, registry, chat, settings = system
    faults = Faults(['hold', 'hold'])
    chat.server = faults.factory(settings)
    project = registry.list()[0]['id']
    conversation = chat.conversation(project)
    first = await chat.submit(project, 'First', conversation['id'], mode='conversation', request_id='queue-first-00001')
    await asyncio.sleep(.02)
    requests = [dict(project_id=project, content=text, conversation_id=conversation['id'], mode=mode, request_id=key)
                for text, mode, key in [('Second', 'auto', 'queue-second-0001'), ('Third', 'execute', 'queue-third-00001')]]
    second, third = [await chat.submit(**request) for request in requests]
    assert await chat.submit(**requests[0]) == second
    await asyncio.gather(chat.recover(), chat.recover())
    assert [p['message'] for p in faults.calls] == ['First']
    assert len(chat.jobs) == 1
    assert [m['status'] for m in chat.history(project)['messages'] if m['role'] == 'user'] == ['processing', 'queued', 'queued']
    # Cold history reconstruction must never reveal later owner requests.
    prompt, _ = chat.prompt(conversation, 'First', 'conversation', first['execution_id'], 'fresh', None)
    assert prompt['conversation_history'] == []
    complete(chat, faults, first['execution_id'])
    await settle(chat)
    # Racing recovery and a new send during the completion boundary cannot overtake Second.
    fourth, _ = await asyncio.gather(chat.submit(project, 'Fourth', conversation['id']), chat.recover())
    await asyncio.sleep(.02)
    assert [p['message'] for p in faults.calls] == ['First', 'Second']
    assert len(chat.jobs) == 1
    row = db.one('SELECT * FROM executions WHERE id=?', (second['execution_id'],))
    assert row['thread_id'] == db.one('SELECT thread_id FROM executions WHERE id=?', (first['execution_id'],))['thread_id']
    complete(chat, faults, second['execution_id'])
    await settle(chat)
    for _ in range(3):
        await asyncio.gather(chat.recover(), chat.recover())
        await settle(chat)
    assert [p['message'] for p in faults.calls] == ['First', 'Second', 'Third', 'Fourth']
    assert [p['mode'] for p in faults.calls] == ['conversation', 'auto', 'execute', 'auto']
    assert all(db.one('SELECT status FROM executions WHERE id=?', (r['execution_id'],))['status'] == 'completed'
               for r in [first, second, third, fourth])
    assert len(db.all("SELECT * FROM messages WHERE role='user'")) == 4


@pytest.mark.asyncio
async def test_queued_restart_failure_recovery_and_context_guards(system):
    db, registry, chat, settings = system
    faults = Faults(['network'])
    chat.server = faults.factory(settings)
    first = await chat.submit(None, 'Recover first')
    await settle(chat)
    second = await chat.submit(None, 'Wait second', request_id='restart-second-01')
    await chat.recover()
    assert len(faults.calls) == 1  # A deferred head retains its position.
    restarted = Chat(Database(settings.db_path), registry, faults.factory(settings))
    assert await restarted.submit(None, 'Wait second', request_id='restart-second-01') == second
    eligible(db)
    for _ in range(3):
        await restarted.recover()
        await settle(restarted)
    assert [p['message'] for p in faults.calls] == ['Recover first', 'Recover first', 'Wait second']
    assert db.one('SELECT status FROM executions WHERE id=?', (first['execution_id'],))['status'] == 'completed'
    project = registry.list()[0]['id']
    with pytest.raises(ValueError, match='Chat changed'):
        await restarted.submit(project, 'Wrong context', chat.conversation()['id'])
    assert len(db.all('SELECT * FROM executions')) == 2


@pytest.mark.asyncio
async def test_queue_commits_before_launch_and_permanent_failure_does_not_lose_tail(system):
    db, registry, chat, settings = system
    faults = Faults(['permanent'])
    chat.launch = lambda *args: None
    receipts = [await chat.submit(None, text, request_id=f'crash-queue-{i:05d}') for i, text in enumerate(['One', 'Two', 'Three'])]
    restarted = Chat(Database(settings.db_path), registry, faults.factory(settings))
    for _ in range(4):
        await restarted.recover()
        await settle(restarted)
    assert [p['message'] for p in faults.calls] == ['One', 'Two', 'Three']
    assert [db.one('SELECT status FROM executions WHERE id=?', (r['execution_id'],))['status'] for r in receipts] == ['failed', 'completed', 'completed']
    assert len(db.all('SELECT * FROM chat_requests')) == 3


@pytest.mark.asyncio
async def test_runner_cleanup_blocks_next_native_attachment(system):
    db, _, chat, settings = system
    faults = Faults(['hold'])
    chat.server = faults.factory(settings)
    first = await chat.submit(None, 'One')
    await asyncio.sleep(.02)
    second = await chat.submit(None, 'Two')
    # Model a durable terminal receipt while old native handlers still belong to the runner.
    chat._finish(first['execution_id'], first['message_id'], 'completed', '', 'One done')
    await chat.recover()
    assert len(faults.calls) == 1
    complete(chat, faults, first['execution_id'])
    await settle(chat)
    await chat.recover()
    await settle(chat)
    assert len(faults.calls) == 2
    assert db.one('SELECT status FROM executions WHERE id=?', (second['execution_id'],))['status'] == 'completed'


def test_concurrent_fifo_acceptance_and_duplicate_transaction(system):
    from concurrent.futures import ThreadPoolExecutor

    db, _, chat, _ = system
    chat.launch = lambda *args: None  # Simulate crash after durable acceptance.
    requests = [dict(project_id=None, content=f'Concurrent {i}', request_id=f'concurrent-queue-{i:04d}') for i in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda request: asyncio.run(chat.submit(**request)), requests * 2))
    assert receipts[:6] == receipts[6:]
    rows = db.all('SELECT * FROM executions ORDER BY rowid')
    assert len(rows) == 6 and len(db.all('SELECT * FROM chat_requests')) == 6
    assert [chat.is_head(row['id']) for row in rows] == [True, False, False, False, False, False]
    # Acceptance order is SQLite's committed insertion sequence, even for equal timestamps.
    with db.transaction() as conn:
        conn.execute('UPDATE messages SET created_at=1')
    assert [m['content'] for m in chat.history()['messages'] if m['role'] == 'user'] == [r['input'] for r in rows]


@pytest.mark.asyncio
async def test_other_conversation_runs_independently_and_stop_tail_is_explicit(system):
    db, registry, chat, settings = system
    faults = Faults(['hold', 'hold'])
    chat.server = faults.factory(settings)
    project = registry.list()[0]['id']
    first = await chat.submit(None, 'Global active')
    await asyncio.sleep(.02)
    second = await chat.submit(None, 'Global queued')
    other = await chat.submit(project, 'Project active', mode='conversation')
    await asyncio.sleep(.02)
    assert len(faults.calls) == 2 and len(chat.jobs) == 2
    await chat.interrupt(second['execution_id'])
    assert db.one('SELECT status FROM executions WHERE id=?', (second['execution_id'],))['status'] == 'interrupted'
    assert db.one('SELECT status FROM executions WHERE id=?', (first['execution_id'],))['status'] == 'running'
    complete(chat, faults, first['execution_id'])
    complete(chat, faults, other['execution_id'])
    await settle(chat)
    assert len(faults.calls) == 2


@pytest.mark.asyncio
async def test_active_history_tool_cannot_consume_future_queued_request(system):
    from ai_manager.orchestration import Scheduler
    from ai_manager.workers import Workers

    db, registry, chat, settings = system
    chat.launch = lambda *args: None
    first = await chat.submit(None, 'Current instruction', mode='conversation')
    second = await chat.submit(None, 'Future instruction', mode='execute')
    scheduler = Scheduler(db, registry, settings, Workers(db, registry, settings), chat)
    params = dict(tool='manager_action', manager_execution_id=first['execution_id'],
                  conversation_id=chat.conversation()['id'], mode='conversation')
    result = await scheduler.manager_action(None, params | {'arguments': {'action': 'read_history'}})
    assert [m['content'] for m in result['messages']] == ['Current instruction']
    future = db.one("SELECT id FROM messages WHERE execution_id=? AND role='user'", (second['execution_id'],))['id']
    with pytest.raises(ValueError, match='Unknown message'):
        await scheduler.manager_action(None, params | {'arguments': {'action': 'read_history', 'message_id': future}})
    with pytest.raises(ValueError, match='Auto or Execute'):
        await scheduler.manager_action(None, params | {'arguments': {'action': 'deploy_release', 'release_id': 'unrequested'}})
    assert not scheduler.deployment.gated()

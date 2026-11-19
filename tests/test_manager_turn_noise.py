"""Manager chat turns are not broken by another provider's process, a deploy restart, or a quick recovery.

Live evidence (2026-11-19): with Claude active and Codex logged out, every tool call that restarted the providers
spawned and killed a Codex App Server whose reader signalled "transport lost" to the running Claude turn.
"""

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from test_recovery import Faults, eligible, settle
from test_minimal_flow import system as system_fixture

from ai_manager import fence
from ai_manager.chat import HELD, RECOVERY_QUIET_SECONDS
from ai_manager.codex import AppServer, CodexError
from ai_manager.config import Settings
from ai_manager.providers import ClaudeServer, ProviderServer

system = system_fixture


def eof_stream():
    stream = asyncio.StreamReader()
    stream.feed_eof()
    return stream


@pytest.mark.asyncio
async def test_a_dying_codex_process_does_not_break_a_running_claude_turn(tmp_path):
    settings = Settings(data_dir=tmp_path)
    (tmp_path / 'claude-threads').mkdir()
    (tmp_path / 'claude-threads' / 'claude-thread.json').write_text('{}')
    server = ProviderServer(settings, active=lambda: 'claude',
                            backends={'codex': AppServer(settings), 'claude': ClaudeServer(settings)})
    server.queues['claude-thread'], server.queues['codex-thread'] = asyncio.Queue(), asyncio.Queue()
    await server.backends['codex']._read(SimpleNamespace(stdout=eof_stream()))
    assert (await server.queues['codex-thread'].get())['method'] == 'transport/lost'
    assert server.queues['claude-thread'].empty()
    await server.backends['claude']._read(SimpleNamespace(stdout=eof_stream()))
    assert (await server.queues['claude-thread'].get())['method'] == 'transport/lost'
    assert server.queues['codex-thread'].empty()


@pytest.mark.asyncio
async def test_a_standalone_app_server_still_signals_every_thread(tmp_path):
    server = AppServer(Settings(data_dir=tmp_path))
    server.queues['t'] = asyncio.Queue()
    await server._read(SimpleNamespace(stdout=eof_stream()))
    assert (await server.queues['t'].get())['method'] == 'transport/lost'


class Failing:
    def __init__(self, fail):
        self.fail, self.starts = fail, 0

    async def start(self):
        self.starts += 1
        if self.fail:
            raise CodexError('A Codex ChatGPT login is required. API-key providers are disabled.')


@pytest.mark.asyncio
async def test_a_failing_unused_provider_is_not_restarted_on_every_start(tmp_path, monkeypatch):
    from ai_manager import providers

    clock = [1000.0]
    monkeypatch.setattr(providers.time, 'monotonic', lambda: clock[0])
    active = ['claude']
    server = ProviderServer(Settings(data_dir=tmp_path), active=lambda: active[0],
                            backends={'codex': Failing(True), 'claude': Failing(False)})
    for _ in range(5):
        await server.start()  # e.g. each delegate / repair_status tool call
    assert server.backends['codex'].starts == 1 and server.backends['claude'].starts == 5
    clock[0] += providers.INACTIVE_RETRY_SECONDS + 1
    await server.start()
    assert server.backends['codex'].starts == 2
    active[0] = 'codex'  # The active provider is always tried and its failure reported.
    with pytest.raises(CodexError):
        await server.start()
    assert server.backends['codex'].starts == 3


def history_message(chat, project_id, execution_id, role='assistant'):
    return next(m for m in chat.history(project_id)['messages']
                if m['execution_id'] == execution_id and m['role'] == role)


@pytest.mark.asyncio
async def test_a_quickly_resumed_turn_shows_no_recovery_status(system):
    db, registry, chat, settings = system
    project = registry.list()[0]['id']
    faults = Faults(['transport', 'success'])
    chat.server = faults.factory(settings)
    request = await chat.submit(project, 'Answer me')
    await settle(chat)
    execution = request['execution_id']
    assert db.one('SELECT status FROM executions WHERE id=?', (execution,))['status'] == 'recovering'
    reply = history_message(chat, project, execution)
    assert reply['status'] == 'streaming' and reply['recovery_reason'] is None and reply['retry_at'] is None
    assert history_message(chat, project, execution, 'user')['status'] == 'processing'
    # Still not resumed after the quiet window: now the owner sees why.
    db.run("UPDATE events SET created_at=created_at-? WHERE execution_id=? AND kind='recovery.scheduled'",
           (RECOVERY_QUIET_SECONDS + 1, execution))
    assert history_message(chat, project, execution)['status'] == 'waiting'
    db.run("UPDATE events SET created_at=? WHERE execution_id=? AND kind='recovery.scheduled'", (time.time(), execution))
    # A retry scheduled far ahead (a long backoff) is shown at once.
    db.run('UPDATE executions SET retry_at=? WHERE id=?', (time.time() + RECOVERY_QUIET_SECONDS + 60, execution))
    assert history_message(chat, project, execution)['status'] == 'waiting'
    eligible(db)
    await chat.recover()
    await settle(chat)
    reply = history_message(chat, project, execution)
    assert reply['status'] == 'completed' and reply['content'] == 'Result 2'


@pytest.mark.asyncio
async def test_a_usage_limit_wait_is_always_shown(system):
    db, registry, chat, settings = system
    project = registry.list()[0]['id']
    chat.server = Faults(['usage']).factory(settings)
    request = await chat.submit(project, 'Answer me')
    await settle(chat)
    reply = history_message(chat, project, request['execution_id'])
    assert reply['status'] == 'waiting' and reply['recovery_reason'] == 'usage_limit'


def deploy_busy(settings):
    """The deploy script's own idle check (scripts/deploy.py IDLE) against this database."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('deploy_idle', Path(__file__).parents[1] / 'scripts/deploy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    IDLE = module.IDLE
    with sqlite3.connect(settings.db_path) as conn:
        return {name: n for name, sql in IDLE.items() if (n := conn.execute(sql).fetchone()[0])}


@pytest.mark.asyncio
async def test_owner_message_during_a_deploy_restart_is_held_then_runs_normally(system):
    db, registry, chat, settings = system
    project = registry.list()[0]['id']
    faults = Faults()
    chat.server = faults.factory(settings)
    with fence.hold(settings.data_dir, True):  # the deploy, from its final idle check through the restart
        first = await chat.submit(project, 'First while deploying', request_id='r' * 20)
        second = await chat.submit(project, 'Second while deploying')
        assert first['result'] == 'accepted' and not chat.jobs and not faults.calls
        assert deploy_busy(settings) == {}  # the held messages never block or fail the deploy
        assert await chat.submit(project, 'First while deploying', request_id='r' * 20) == first  # retry: same receipt
        statuses = [(m['role'], m['status']) for m in chat.history(project)['messages']]
        assert statuses == [('user', 'queued'), ('assistant', 'queued')] * 2
        await chat.recover()  # the fence is still held: nothing starts
        assert not chat.jobs and db.one("SELECT count(*) AS n FROM settings WHERE key LIKE ?", (HELD + '%',))['n'] == 2
    await chat.recover()  # the restarted service's first scheduler tick
    await settle(chat)
    await chat.recover()
    await settle(chat)
    rows = {r['id']: r for r in db.all("SELECT * FROM executions WHERE agent_id='manager'")}
    assert rows[first['execution_id']]['status'] == rows[second['execution_id']]['status'] == 'completed'
    assert rows[first['execution_id']]['retry_count'] == 0 and not rows[first['execution_id']]['recovery_reason']
    assert [p['message'] for p in faults.calls] == [
        'First while deploying', 'Second while deploying']
    messages = chat.history(project)['messages']
    assert [m['status'] for m in messages] == ['completed'] * 4
    assert not db.one("SELECT 1 FROM settings WHERE key LIKE ?", (HELD + '%',))


@pytest.mark.asyncio
async def test_a_worker_delivery_during_a_deploy_restart_is_still_refused(system):
    db, registry, chat, settings = system
    with fence.hold(settings.data_dir, True):
        with pytest.raises(ValueError, match='deploy is restarting'):
            with chat.deploy_fence():
                pass

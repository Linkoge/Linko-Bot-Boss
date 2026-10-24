"""Host repair requests record the app's saved worker selection (shown = saved = dispatched)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from test_model_settings import SOL, SONNET, pool
from test_model_settings import system as system_fixture
from test_project_worker_models import GLOBAL, pick, toggle, two_projects

from scripts.emergency_repair import Repair, atomic

system = system_fixture
CLAUDE = {'model': SONNET, 'effort': 'high'}
ROOT = Path(__file__).resolve().parents[1]


async def ask(system, key, project=None):
    db, _, settings, _, chat, _, scheduler = system
    atomic(settings.data_dir / 'repair/installed.json', {'repo': str(settings.data_dir), 'provider': 'codex',
                                                         'model': 'fallback-model'})
    await scheduler.manager_action(project, {
        'tool': 'manager_action', 'callId': key, 'manager_execution_id': key,
        'conversation_id': chat.conversation(project)['id'],
        'arguments': {'action': 'emergency_repair', 'objective': 'Install a VM'}})
    return next(r for r in Repair(settings.data_dir).records() if r['key'] == 'manager:' + key)


def finish(system):
    repair = Repair(system[2].data_dir)
    for row in repair.records():
        row['status'] = 'succeeded'
        repair.save(row)


@pytest.mark.asyncio
async def test_request_follows_saved_selection_and_launcher_builds_matching_command(system):
    db, _, settings, server, *_ = system
    pick(db, server, manager=CLAUDE, worker=CLAUDE)
    row = await ask(system, 'one')
    assert (row['provider'], row['model'], row['effort']) == ('claude', SONNET, 'high') and not row['fallback_provider']
    command = Repair(settings.data_dir).command(row, '/tmp/out')
    assert command[0].endswith('claude') and command[command.index('--model') + 1] == SONNET
    finish(system)

    pick(db, server, manager=GLOBAL, worker=GLOBAL)  # switching in the app changes the next request
    row = await ask(system, 'two')
    assert (row['provider'], row['model'], row['effort']) == ('codex', SOL, 'medium')
    command = Repair(settings.data_dir).command(row, '/tmp/out')
    assert command[1] == 'exec' and SOL in command and 'model_reasoning_effort="medium"' in command


@pytest.mark.asyncio
async def test_repo_lock_applies_to_project_requests(system):
    db, _, _, server, *_ = system
    one, _ = two_projects(system)
    pick(db, server, manager=GLOBAL, worker=GLOBAL)
    toggle(db, one, True)
    pick(db, server, one, manager=GLOBAL, worker=CLAUDE)
    assert (await ask(system, 'locked', one))['provider'] == 'claude'
    finish(system)
    assert (await ask(system, 'global'))['provider'] == 'codex'


@pytest.mark.asyncio
async def test_automatic_mode_uses_provider_and_first_allowed_pair(system):
    db, _, _, server, *_ = system
    pick(db, server, manager=CLAUDE, worker=pool([SONNET], ['medium', 'high'], 'claude'))
    row = await ask(system, 'auto')
    assert (row['provider'], row['model'], row['effort']) == ('claude', SONNET, 'medium')


def test_request_without_provider_uses_installed_fallback(tmp_path):
    repair = Repair(tmp_path / 'data', {'repo': str(tmp_path), 'provider': 'claude', 'model': 'fb'})
    row = repair.submit('k', 'x', 'origin')
    assert row['provider'] == 'claude' and row['model'] == 'fb' and row['fallback_provider']
    assert 'fallback provider' in row['notices'][0]
    other = Repair(tmp_path / 'd2', {'repo': str(tmp_path), 'provider': 'claude', 'model': 'fb'})
    assert other.submit('k', 'x', 'origin', 'codex', 'm')['model'] == 'm'  # a recorded provider never takes the fallback model


def test_limit_switch_is_visible_in_the_chat_result(tmp_path, monkeypatch):
    from scripts.emergency_repair import ProviderFailure
    repair = Repair(tmp_path / 'data', {'repo': str(tmp_path), 'conversation_id': 'o'})
    monkeypatch.setattr(repair, 'healthy', lambda: False)
    monkeypatch.setattr(repair, 'alternative', lambda row: 'codex')
    repair.submit('k', 'x', 'o', 'claude', 'm', 'high')
    calls = []

    def provider(row, lock):
        calls.append(row['provider'])
        if len(calls) == 1:
            raise ProviderFailure('usage limit', True, limit=True)
        return 'done'
    repair.run(provider)
    repair.run(provider)
    row = repair.records()[0]
    assert calls == ['claude', 'codex'] and row['effort'] is None
    assert 'on Codex after switching from Claude' in row['notices'][-1]


def installer():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('install_repair', ROOT / 'scripts/install_repair.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_installer_default_prefers_installed_claude_and_status_shows_fallback(tmp_path, monkeypatch):
    module = installer()
    home, data = tmp_path / 'home', tmp_path / 'data'
    (home / '.local/bin').mkdir(parents=True)
    for name in (home / '.local/bin/claude', tmp_path / 'codex'):
        name.write_text('#!/bin/sh\n')
        name.chmod(0o755)
    monkeypatch.setenv('AI_MANAGER_CODEX', str(tmp_path / 'codex'))
    monkeypatch.delenv('AI_MANAGER_CLAUDE', raising=False)
    (home / '.codex').mkdir()
    (home / '.codex/auth.json').write_text('{}')
    assert module.detect_provider(home) == 'codex'  # only codex is logged in
    (home / '.claude').mkdir()
    (home / '.claude/.credentials.json').write_text('{}')
    assert module.detect_provider(home) == 'claude'  # both logged in: claude
    monkeypatch.setattr(module, 'systemctl', lambda *a, check=True: subprocess.CompletedProcess(a, 0, 'enabled', ''))
    module.install(Path(ROOT), data, home, 'origin', activate=False)
    assert module.read(data / 'repair/installed.json')['provider'] == 'claude'
    assert module.status(data, home)['fallback_provider'] == 'claude'
    module.install(Path(ROOT), data, home, 'origin', provider='codex', activate=False)
    assert module.status(data, home)['fallback_provider'] == 'codex'

import asyncio
import os
import pwd
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from ai_manager import core, scratch

KEYS = ('TMPDIR', 'TMP', 'TEMP', 'CLAUDE_CODE_TMPDIR')


def test_env_points_every_variable_at_a_private_folder(tmp_path):
    base = tmp_path / 'scratch'
    values = scratch.env('task one/../x', {'AI_MANAGER_TMPDIR': str(base)})
    assert set(values) == set(KEYS) and len(set(values.values())) == 1
    folder = Path(values['TMPDIR'])
    assert folder.parent == base and folder.is_dir() and (base.stat().st_mode & 0o777) == 0o700
    assert '/' not in folder.name


def test_default_root_is_under_home_cache():
    assert scratch.root({}) == Path('~/.cache/linko-tmp').expanduser()


def test_cleanup_removes_only_old_inactive_entries_inside_the_folder(tmp_path):
    base, outside = tmp_path / 'scratch', tmp_path / 'outside'
    base.mkdir()
    outside.mkdir()
    (outside / 'keep').write_text('x')
    for name in ('old', 'active', 'fresh'):
        (base / name).mkdir()
        (base / name / 'f').write_text('x')
    (base / 'oldfile').write_text('x')
    (base / 'link').symlink_to(outside)
    old = time.time() - 3 * 86400
    for name in ('old', 'active', 'oldfile'):
        os.utime(base / name, (old, old))
    os.utime(base / 'link', (old, old), follow_symlinks=False)
    removed = scratch.cleanup(['active'], directory=base)
    assert sorted(removed) == ['link', 'old', 'oldfile']
    assert sorted(p.name for p in base.iterdir()) == ['active', 'fresh']
    assert (outside / 'keep').exists()  # the symlink was removed, never followed


def test_cleanup_on_missing_folder_is_quiet(tmp_path):
    assert scratch.cleanup(directory=tmp_path / 'absent') == []


def test_gate_runs_children_with_scratch_env(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_MANAGER_TMPDIR', str(tmp_path / 'scratch'))
    seen = []
    monkeypatch.setattr(core, 'secret_check', lambda *a: None)
    monkeypatch.setattr(core, 'changed_tests', lambda *a: ['tests/x.py'])
    monkeypatch.setattr(core, 'suite', lambda name, argv, root, env, seconds: seen.append(env) or None)
    (tmp_path / 'tests').mkdir()
    assert core.run_gate(tmp_path, 'base')['ok']
    assert seen and all(env[k].startswith(str(tmp_path / 'scratch')) for env in seen for k in KEYS)


def test_codex_spawn_gets_scratch_env(tmp_path, monkeypatch):
    from ai_manager.codex import AppServer
    from ai_manager.config import Settings
    monkeypatch.setenv('AI_MANAGER_TMPDIR', str(tmp_path / 'scratch'))
    captured = {}

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError('stop')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake)
    server = AppServer(Settings())
    server.scratch = 'exec1'
    try:
        asyncio.run(server.start())
    except RuntimeError:
        pass
    assert all(captured['env'][k] == str(tmp_path / 'scratch' / 'exec1') for k in KEYS)


def test_launcher_gives_worker_a_run_folder_on_its_own_disk(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import worker_launcher
    user = SimpleNamespace(pw_dir=str(tmp_path), pw_name='w')
    env = worker_launcher.environment({}, 'run-1', user)
    assert env['TMPDIR'] == str(tmp_path / '.cache' / 'linko-tmp' / 'run-1') == env['CLAUDE_CODE_TMPDIR']
    assert Path(env['TMP']).is_dir()


def test_scheduler_cleanup_spares_running_executions_and_runs_hourly(tmp_path, monkeypatch):
    from ai_manager.orchestration import Scheduler
    base = tmp_path / 'scratch'
    monkeypatch.setenv('AI_MANAGER_TMPDIR', str(base))
    base.mkdir()
    for name in ('run1', 'done1'):
        (base / name).mkdir()
        os.utime(base / name, (1, 1))
    db = SimpleNamespace(all=lambda sql, params=(): [{'id': 'run1'}])
    sched = SimpleNamespace(db=db, scratch_at=0.0)
    assert Scheduler.clean_scratch(sched) == ['done1']
    assert (base / 'run1').exists()
    assert Scheduler.clean_scratch(sched) == []  # within the hour

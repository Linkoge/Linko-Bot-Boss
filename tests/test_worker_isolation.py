"""Workers as a separate unprivileged Linux user. No real users, sudoers or ACLs are created: sudo, setfacl and the
installer's commands are mocked. The opt-in probe at the end checks a real installation (LINKO_ISOLATION_PROBE=1)."""
import importlib.util
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_core import run_next, world  # noqa: F401  (fixture)

from ai_manager import core as core_module
from ai_manager import doctor, isolation, recovery
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.isolation import Isolation
from ai_manager.providers import ClaudeServer, ProviderServer

ROOT = Path(__file__).resolve().parents[1]
ME = pwd.getpwuid(os.getuid()).pw_name


def script(name):
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder(Isolation):
    """Real argv building; recorded instead of executed signals and ACL changes."""

    def __init__(self, toolchain=()):
        super().__init__('linko-worker', '/usr/local/lib/linko/worker-launcher.py', ME, toolchain=toolchain)
        object.__setattr__(self, 'calls', [])

    def signal(self, tag, sig, wait=True):
        self.calls.append(('signal', tag, int(sig)))
        return True

    def setfacl(self, *args):
        self.calls.append(('setfacl', *args))

    def granted(self, path, writable):
        return False


PREFIX = ['/usr/bin/sudo', '-n', '-u', 'linko-worker', '/usr/bin/python3', '-I', '/usr/local/lib/linko/worker-launcher.py']


# ---- launching through the fixed-argv helper ----------------------------------------------------------------------
@pytest.mark.smoke
def test_isolated_codex_worker_runs_through_the_fixed_launcher_as_the_worker_user(tmp_path):
    server = AppServer(Settings(data_dir=tmp_path), native=True)
    plain = server.command()
    assert plain[0] == server.settings.codex_bin and 'sudo' not in plain[0]  # same-user mode is unchanged
    server.isolate(Recorder(), 'exec-1')
    argv = server.command()
    assert argv[:len(PREFIX)] == PREFIX
    assert argv[len(PREFIX):len(PREFIX) + 6] == ['run', '--tag', 'exec-1', 'codex', '--', 'app-server']
    assert argv[len(PREFIX) + 5:] == plain[1:]  # the configured binary replaces argv[0]; no shell anywhere


def test_isolated_claude_turns_run_as_the_worker_user_while_the_host_keeps_its_files(tmp_path):
    provider = ProviderServer(Settings(data_dir=tmp_path), native=True)
    provider.isolate(Recorder(), 'exec-2')
    assert provider.isolation and all(b.worker_tag == 'exec-2' for b in provider.backends.values())
    claude = provider.backends['claude']
    assert isinstance(claude, ClaudeServer)
    argv = claude.command()
    assert argv[0] == sys.executable and 'sudo' not in argv[0]  # the host process stays the service user's
    assert json.loads(argv[argv.index('--worker-prefix') + 1]) == PREFIX
    assert argv[argv.index('--worker-tag') + 1] == 'exec-2'

    from ai_manager.claude_host import Host
    host = Host(tmp_path, '/opt/claude', True, PREFIX, 'exec-2')
    assert host.command(['/opt/claude', '-p', '--model', 'm'], '/work') == [
        *PREFIX, 'run', '--tag', 'exec-2', '--cwd', '/work', 'claude', '--', '-p', '--model', 'm']
    assert Host(tmp_path / 'b', '/opt/claude', True).command(['/opt/claude', '-p'], '/work') == ['/opt/claude', '-p']


def test_isolated_worker_is_signalled_through_the_launcher_because_its_processes_belong_to_another_user(
        tmp_path, monkeypatch):
    server = AppServer(Settings(data_dir=tmp_path), native=True)
    recorder = Recorder()
    server.isolate(recorder, 'exec-3')
    server.process = SimpleNamespace(pid=4242)

    def denied(pid, sig):
        raise PermissionError
    monkeypatch.setattr(os, 'killpg', denied)
    server._signal(signal.SIGTERM)
    assert recorder.calls == [('signal', 'exec-3', int(signal.SIGTERM))]


def test_kill_helper_argv_is_fixed(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kw: seen.append(argv) or SimpleNamespace(returncode=0))
    assert Isolation('linko-worker', '/usr/local/lib/linko/worker-launcher.py', ME).signal('exec-4', signal.SIGKILL)
    assert seen == [[*PREFIX, 'kill', '--tag', 'exec-4', '--signal', str(int(signal.SIGKILL))]]


def test_orphan_recovery_signals_an_isolated_worker_by_its_execution_tag(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(recovery.isolation, 'detect', lambda: recorder)
    monkeypatch.setattr(recovery, 'owned_alive', lambda row: True)
    pid = os.getpid()
    row = {'id': 'exec-5', 'pid': pid, 'process_start': 'x', 'recovery_reason': None, 'retry_count': 0}
    cmdline = b'\0'.join([b'/usr/bin/sudo', b'run', b'--tag', b'exec-5', b'codex', b'--', b'app-server'])
    real = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda self: cmdline if self == Path(f'/proc/{pid}/cmdline') else real(self))
    monkeypatch.setattr(os, 'getpgid', lambda p: p + 1)  # not a group we own: never killpg it
    assert recovery.reap_orphan(row, force=True) is True
    assert recorder.calls == [('signal', 'exec-5', int(signal.SIGKILL))]


# ---- core: isolated when installed, warns and still runs when not --------------------------------------------------
@pytest.mark.smoke
def test_core_runs_the_worker_as_the_worker_user_and_opens_only_its_workspace(world, monkeypatch):  # noqa: F811
    recorder = Recorder()
    monkeypatch.setattr(core_module.isolation, 'detect', lambda: recorder)
    task = world.tasks.add(world.project['id'], 'isolated work')
    import asyncio
    done = asyncio.run(run_next(world))
    assert done['state'] == 'done'
    execution = world.db.one("SELECT id FROM executions WHERE task_id=?", (task['id'],))['id']
    event = world.db.one("SELECT payload FROM events WHERE task_id=? AND kind='worker.isolation'", (task['id'],))
    assert json.loads(event['payload']) == {'user': 'linko-worker'}
    workspace = Path(done['workspace'])
    gates = [c for c in recorder.calls if c[0] == 'setfacl' and c[1] == '-m' and c[2].count(':') == 2]
    opened = [(c[2], c[3]) for c in gates if not c[2].endswith('--x')]
    # Opened read/write for the run, then closed again; the project checkout is readable and its .git writable.
    assert (f'u:linko-worker:rwx', str(workspace)) in opened
    assert (f'u:linko-worker:r-x', str(world.app)) in opened
    assert opened[-2:] == [('u:linko-worker:---', str(workspace)), ('u:linko-worker:---', str(world.app))]
    assert not world.core.gates and execution


def test_core_falls_back_to_same_user_mode_with_a_visible_warning(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(core_module.isolation, 'detect', lambda: None)
    task = world.tasks.add(world.project['id'], 'plain work')
    import asyncio
    assert asyncio.run(run_next(world))['state'] == 'done'  # never stalls the queue
    event = world.db.one("SELECT payload FROM events WHERE task_id=? AND kind='worker.isolation'", (task['id'],))
    assert json.loads(event['payload']) == {'warning': isolation.WARNING}
    assert "your user's rights" in isolation.WARNING


def test_settings_api_shows_the_same_user_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation, 'detect', lambda path=isolation.CONFIG: None)
    assert isolation.status() == {'active': False, 'user': None, 'warning': isolation.WARNING}
    html = (ROOT / 'ai_manager/static/index.html').read_text()
    assert 'id="worker-isolation-warning"' in html
    assert 'worker_isolation' in (ROOT / 'ai_manager/static/app.js').read_text()


def test_detect_requires_a_root_owned_unwritable_launcher_and_this_service_user(tmp_path, monkeypatch):
    launcher = tmp_path / 'worker-launcher.py'
    launcher.write_text('#')
    launcher.chmod(0o755)
    config = tmp_path / 'worker.json'
    worker = next(p.pw_name for p in pwd.getpwall() if p.pw_uid != os.getuid())
    config.write_text(json.dumps({'worker_user': worker, 'service_user': ME, 'launcher': str(launcher),
                                  'toolchain': ['/opt/tools']}))
    found = isolation.detect(config, root_uid=os.getuid())
    assert found and found.user == worker and found.toolchain == ('/opt/tools',)
    assert isolation.detect(config) is None  # owned by this user, not root: no boundary
    assert isolation.detect(tmp_path / 'missing.json', root_uid=os.getuid()) is None
    launcher.chmod(0o775)
    assert isolation.detect(config, root_uid=os.getuid()) is None
    launcher.chmod(0o755)
    monkeypatch.setenv('AI_MANAGER_WORKER_ISOLATION', 'off')
    assert isolation.detect(config, root_uid=os.getuid()) is None


def test_closing_keeps_toolchain_directories_readable():
    recorder = Recorder(toolchain=('/srv/manager',))
    recorder.close('/srv/manager', '/srv/other')
    assert recorder.calls == [('setfacl', '-m', 'u:linko-worker:r-x', '/srv/manager'),
                              ('setfacl', '-m', 'u:linko-worker:---', '/srv/other')]


def test_blanket_nopasswd_detection():
    assert isolation.blanket_nopasswd('User mint1 may run:\n    (ALL : ALL) NOPASSWD: ALL\n')
    assert isolation.blanket_nopasswd('    (ALL) NOPASSWD: ALL')
    assert not isolation.blanket_nopasswd(
        '    (linko-worker) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/linko/worker-launcher.py run *\n'
        '    (root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/linko/repair-launcher.py --data /d tick\n'
        '    (ALL : ALL) ALL\n')


# ---- the launcher (runs as the worker user) -----------------------------------------------------------------------
def test_launcher_execs_only_configured_programs_with_a_clean_environment(tmp_path, monkeypatch):
    launcher = script('worker_launcher')
    config = tmp_path / 'worker.json'
    config.write_text(json.dumps({'programs': {'codex': '/opt/codex'}, 'path': '/opt/bin:/usr/bin'}))
    monkeypatch.setenv('XAI_API_KEY', 'never-passed')
    seen = []
    monkeypatch.setattr(os, 'execve', lambda binary, argv, env: seen.append((binary, argv, env)))
    monkeypatch.setattr(os, 'chdir', lambda path: seen.append(path))
    assert launcher.main(['run', '--tag', 'exec-6', '--cwd', '/work', 'codex', '--', 'app-server', '-c', 'a=1'],
                         config) == 0
    path, (binary, argv, env) = seen
    assert path == '/work' and binary == '/opt/codex' and argv == ['/opt/codex', 'app-server', '-c', 'a=1']
    assert env['LINKO_WORKER_TAG'] == 'exec-6' and env['PATH'] == '/opt/bin:/usr/bin' and 'XAI_API_KEY' not in env
    assert launcher.main(['run', '--tag', 'exec-6', 'bash', '--', '-c', 'id'], config) == 2  # not configured
    assert launcher.main(['run', '--tag', 'bad tag', 'codex', '--'], config) == 2
    assert launcher.main(['shell'], config) == 2
    monkeypatch.setattr(os, 'getuid', lambda: 0)
    assert launcher.main(['probe'], config) == 2  # never as root


def test_launcher_kill_finds_only_tagged_processes_of_its_user(tmp_path, monkeypatch):
    launcher = script('worker_launcher')
    proc = tmp_path / 'proc'
    for pid, environ in ((101, b'A=1\0LINKO_WORKER_TAG=exec-7\0'), (102, b'LINKO_WORKER_TAG=exec-70\0'),
                         (103, b'LINKO_WORKER_TAG=exec-7\0')):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / 'environ').write_bytes(environ)
    (proc / 'self').mkdir()
    assert sorted(launcher.tagged('exec-7', proc)) == [101, 103]
    assert list(launcher.tagged('exec-7', proc, uid=os.getuid() + 1)) == []


def test_launcher_probe_reports_reachable_paths(tmp_path, capsys):
    launcher = script('worker_launcher')
    secret = tmp_path / 'secret'
    secret.write_text('x')
    assert launcher.main(['probe', '--read', str(secret), '--read', str(tmp_path / 'missing'),
                          '--write', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['read'] == {str(secret): True, str(tmp_path / 'missing'): False}
    assert result['write'] == {str(tmp_path): True}


# ---- the installer ------------------------------------------------------------------------------------------------
@pytest.fixture
def host(tmp_path):
    installer = script('install_worker_user')
    home = tmp_path / 'home'
    data = home / '.local/share/linko-bot-manager'
    for path in (data / 'assignment-workspaces/task-a', data / 'repair', home / '.ssh', home / '.config',
                 home / 'projects/app', home / 'projects/other', home / 'tools/bin'):
        path.mkdir(parents=True)
    (data / 'manager.sqlite3').write_text('')
    codex = home / 'tools/bin/codex'
    codex.write_text('#!/bin/sh\n')
    layout = installer.Layout(lib=tmp_path / 'lib', etc=tmp_path / 'etc', sudoers_dir=tmp_path / 'sudoers.d')
    options = installer.Options(service=ME, home=home, data=data, worker='linko-test-no-such-user',
                                worker_home=tmp_path / 'worker-home', projects=[home / 'projects'],
                                programs={'codex': str(codex), 'claude': str(home / 'tools/bin/claude')},
                                toolchain=[home / 'tools'], git_name='Owner', git_email='owner@example.invalid')
    return SimpleNamespace(installer=installer, home=home, data=data, layout=layout, options=options)


def fake_run(seen):
    def run(argv, **kwargs):
        seen.append(argv)
        if 'visudo' in argv[0]:
            return subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    return run


@pytest.mark.smoke
def test_installer_plan_sudoers_launchers_and_closed_spool(host):
    installer, options, layout = host.installer, host.options, host.layout
    steps = installer.install_plan(options, layout)
    by_path = {str(s.detail.get('path')): s for s in steps if s.kind in ('write', 'sudoers')}
    repair = by_path[str(layout.repair_launcher)]
    assert repair.detail['owner'] == 'root:root' and repair.detail['mode'] == 0o755
    assert repair.detail['content'] == (ROOT / 'scripts/emergency_repair.py').read_text()
    worker = by_path[str(layout.worker_launcher)]
    assert worker.detail['owner'] == 'root:root' and worker.detail['mode'] == 0o755
    sudoers = by_path[str(layout.sudoers)]
    assert sudoers.detail['mode'] == 0o440 and sudoers.detail['owner'] == 'root:root'
    text = sudoers.detail['content']
    launcher = f'/usr/bin/python3 -I {layout.worker_launcher}'
    assert f'Cmnd_Alias LINKO_WORKER = {launcher} run *, {launcher} kill *, {launcher} probe *' in text
    assert (f'Cmnd_Alias LINKO_REPAIR = /usr/bin/python3 -I {layout.repair_launcher} --data {host.data} tick'
            in text)
    assert f'{ME} ALL=(linko-test-no-such-user) NOPASSWD: LINKO_WORKER' in text
    assert f'{ME} ALL=(root) NOPASSWD: LINKO_REPAIR' in text
    rules = [line for line in text.splitlines() if 'NOPASSWD' in line]
    assert len(rules) == 2 and not any(line.rstrip().endswith('NOPASSWD: ALL') for line in rules)
    useradd = next(s.detail['argv'] for s in steps if s.detail.get('argv', [''])[0] == 'useradd')
    assert '--shell' in useradd and '/usr/sbin/nologin' in useradd and '-G' not in useradd and '--groups' not in useradd
    acl = [s.detail['argv'] for s in steps if s.detail.get('argv', [''])[0] == 'setfacl']
    worker_user = 'u:linko-test-no-such-user:'
    # The spool, secrets, database and other projects are closed; the worker never gets write access in the data dir.
    for closed in (host.data / 'repair', host.home / '.ssh', host.home / '.config', host.data / 'manager.sqlite3',
                   host.home / 'projects/other', host.data / 'assignment-workspaces/task-a'):
        assert ['setfacl', '-P', '-m', worker_user + '---', str(closed)] in acl, closed
    assert not any(str(host.data) in a[-1] and ('w' in a[-2].split(':')[-1]) for a in acl)
    assert ['setfacl', '-P', '-m', worker_user + '--x', str(host.data)] in acl
    assert ['setfacl', '-P', '-R', '-m', f'{worker_user}rX,d:{worker_user}rX', str(host.home / 'tools')] in acl
    config = json.loads(by_path[str(layout.config)].detail['content'])
    assert config['worker_user'] == 'linko-test-no-such-user' and config['launcher'] == str(layout.worker_launcher)
    assert 'safe' in by_path[str(options.worker_home / '.gitconfig')].detail['content']


def test_installer_sudoers_passes_visudo_and_install_is_idempotent(host):
    installer, layout = host.installer, host.layout
    if not (shutil.which('visudo') or os.path.exists('/usr/sbin/visudo')):
        pytest.skip('visudo is not available')
    seen = []
    installer.apply(installer.install_plan(host.options, layout), fake_run(seen), output=lambda line: None)
    assert any('visudo' in argv[0] and argv[1] == '-cf' for argv in seen)
    files = {p: p.read_text() for p in (layout.sudoers, layout.config, layout.worker_launcher, layout.repair_launcher)}
    assert installer.check_sudoers(layout.sudoers)[0] is True
    assert oct(layout.sudoers.stat().st_mode & 0o777) == '0o440'
    again = installer.install_plan(host.options, layout)
    assert [s.describe() for s in again] == [s.describe() for s in installer.install_plan(host.options, layout)]
    installer.apply(again, fake_run([]), output=lambda line: None)
    assert {p: p.read_text() for p in files} == files
    assert not list(layout.sudoers_dir.glob('.linko-*'))  # no leftover candidates


def test_installer_refuses_a_sudoers_file_visudo_rejects(host, monkeypatch):
    installer, layout = host.installer, host.layout
    monkeypatch.setattr(installer, 'check_sudoers', lambda path, run=None: (False, 'syntax error'))
    with pytest.raises(RuntimeError, match='sudoers check failed'):
        installer.apply(installer.install_plan(host.options, layout), fake_run([]), output=lambda line: None)
    assert not layout.sudoers.exists()


def test_installer_dry_run_changes_nothing(host, monkeypatch, capsys):
    installer = host.installer
    monkeypatch.setattr(installer, 'defaults', lambda *a, **k: host.options)
    monkeypatch.setattr(installer, 'Layout', lambda: host.layout)
    monkeypatch.setattr(installer, 'apply', lambda *a, **k: pytest.fail('dry run applied steps'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('dry run ran a command'))
    install_plan = installer.install_plan
    monkeypatch.setattr(installer, 'install_plan', lambda options: install_plan(options, host.layout))
    assert installer.main(['install', '--dry-run']) == 0
    out = capsys.readouterr().out
    assert 'run: useradd' in out and 'sudoers: ' in out and 'run: setfacl' in out
    assert not host.layout.lib.exists() and not host.layout.sudoers_dir.exists()


def test_installer_rejects_privileged_worker_and_unsafe_paths(host, monkeypatch):
    installer, options = host.installer, host.options
    monkeypatch.setattr(installer, 'user', lambda name: SimpleNamespace(pw_dir=str(options.worker_home)))
    monkeypatch.setattr(installer, 'groups_of', lambda name: ['linko-worker', 'sudo'])
    with pytest.raises(ValueError, match='privileged groups'):
        installer.install_plan(options, host.layout)
    monkeypatch.setattr(installer, 'groups_of', lambda name: ['linko-worker'])
    options.data = Path('/tmp/has space')
    with pytest.raises(ValueError, match='plain absolute path'):
        installer.install_plan(options, host.layout)


def test_uninstall_restores_same_user_mode(host, monkeypatch):
    installer, layout = host.installer, host.layout
    monkeypatch.setattr(installer, 'user', lambda name: SimpleNamespace(pw_dir='/var/lib/x'))
    steps = installer.uninstall_plan(host.options, layout, remove_user=True)
    removed = [s.detail['path'] for s in steps if s.kind == 'remove']
    assert removed[0] == layout.sudoers and set(removed) == {layout.sudoers, layout.config, layout.worker_launcher,
                                                             layout.repair_launcher}
    argvs = [s.detail['argv'] for s in steps if s.kind == 'run']
    assert any(a[0] == 'find' and '-user' in a and f'{ME}:' in a for a in argvs)  # worker files back to the service user
    assert argvs[-1] == ['userdel', '--remove', 'linko-test-no-such-user']


def test_toolchain_root_never_opens_a_directory_containing_secrets_or_data(host):
    installer, options = host.installer, host.options
    uv = host.home / '.local/bin/uv'
    uv.parent.mkdir(parents=True)
    uv.write_text('')
    assert installer.toolchain_root(uv, options) == uv  # ~/.local would contain the data directory
    assert installer.toolchain_root(host.home / 'tools/bin/codex', options) == host.home / 'tools'
    assert installer.toolchain_root('/usr/bin/env', options) is None
    release = host.home / 'tools/releases/1.2.3/bin/codex'
    release.parent.mkdir(parents=True)
    release.write_text('')
    assert installer.toolchain_root(release, options) == host.home / 'tools/releases'  # survives self-updates


def test_repair_timer_uses_the_root_launcher_only_when_installed_for_this_data_dir(host, tmp_path):
    installer, layout = host.installer, host.layout
    assert installer.repair_command(host.data, layout) is None
    layout.etc.mkdir()
    layout.config.write_text(json.dumps({'data': str(host.data)}))
    layout.lib.mkdir()
    layout.repair_launcher.write_text('#')
    assert installer.repair_command(host.data, layout) is None  # not root-owned
    installer.root_owned = lambda path: True
    assert installer.repair_command(host.data, layout) == [
        '/usr/bin/sudo', '-n', '/usr/bin/python3', '-I', str(layout.repair_launcher), '--data', str(host.data), 'tick']
    assert installer.repair_command(tmp_path / 'other', layout) is None

    install_repair = script('install_repair')
    home = tmp_path / 'service-home'
    data = tmp_path / 'data'
    command = installer.repair_command(host.data, layout)
    install_repair.install(ROOT, data, home, 'origin', activate=False, privileged=command)
    unit = (home / '.config/systemd/user/linko-bot-manager-repair.service').read_text()
    assert f'ExecStart={" ".join(command)}\n' in unit  # exactly the argv the sudoers rule allows
    assert json.loads((data / 'repair/installed.json').read_text())['path']
    assert install_repair.status(data, home)['as_root'] is True
    install_repair.install(ROOT, data, home, 'origin', activate=False)
    assert 'sudo' not in (home / '.config/systemd/user/linko-bot-manager-repair.service').read_text()


# ---- the host agent: root only for its CLI turn --------------------------------------------------------------------
def test_root_repair_launcher_keeps_files_as_the_service_user_and_runs_the_agent_as_root(tmp_path, monkeypatch):
    repair_module = script('emergency_repair')
    monkeypatch.delenv('SUDO_UID', raising=False)
    assert repair_module.drop_privileges() is None  # not started through sudo: unchanged same-user behaviour
    repair = repair_module.Repair(tmp_path, {'path': '/opt/node/bin:/usr/bin'}, invoker=(1000, 1000))
    options = repair.agent()
    assert options['user'] == 0 and options['group'] == 0 and options['extra_groups'] == []
    assert options['env']['HOME'] == pwd.getpwuid(0).pw_dir and options['env']['PATH'] == '/opt/node/bin:/usr/bin'
    assert repair_module.Repair(tmp_path, {}).agent() == {}
    ids = []
    monkeypatch.setattr(os, 'seteuid', lambda uid: ids.append(('u', uid)))
    monkeypatch.setattr(os, 'setegid', lambda gid: ids.append(('g', gid)))
    with repair_module.elevated((1000, 1001)):
        assert ids == [('u', 0), ('g', 0)]
    assert ids[2:] == [('g', 1001), ('u', 1000)]


# ---- doctor -------------------------------------------------------------------------------------------------------
def test_doctor_reports_missing_isolation_and_blanket_nopasswd(tmp_path, monkeypatch):
    listing = '(ALL : ALL) NOPASSWD: ALL'
    monkeypatch.setattr(doctor, 'command', lambda argv, **kw: (0, listing) if argv[:2] == ['sudo', '-n'] else (1, ''))
    lines = []
    check = doctor.Doctor(home=tmp_path, repo=ROOT, output=lines.append, isolation_config=tmp_path / 'missing.json')
    check.worker_isolation(tmp_path)
    assert lines[0].startswith('WARN worker user installed') and "your user's rights" in lines[0]
    assert lines[1].startswith('WARN no blanket NOPASSWD')
    assert not check.failed


def test_doctor_isolation_working_when_the_probe_reaches_nothing(tmp_path, monkeypatch):
    found = Isolation('linko-worker', str(tmp_path / 'worker-launcher.py'), ME)
    shutil.copy(ROOT / 'scripts/worker_launcher.py', found.launcher)
    shutil.copy(ROOT / 'scripts/emergency_repair.py', tmp_path / 'repair-launcher.py')
    config = tmp_path / 'worker.json'
    config.write_text(json.dumps({'repair_launcher': str(tmp_path / 'repair-launcher.py')}))
    monkeypatch.setattr(doctor.isolation, 'detect', lambda path: found)
    unit = tmp_path / '.config/systemd/user/linko-bot-manager-repair.service'
    unit.parent.mkdir(parents=True)
    unit.write_text(f'ExecStart=/usr/bin/sudo -n /usr/bin/python3 -I {tmp_path / "repair-launcher.py"} --data d tick\n')
    reached = {'read': {'env': False, 'ssh': False, 'db': False}, 'write': {'spool': False}}

    def command(argv, **kw):
        if 'probe' in argv:
            assert argv[:len(found.prefix())] == found.prefix()
            return 0, json.dumps(reached)
        return 1, ''
    monkeypatch.setattr(doctor, 'command', command)
    lines = []
    check = doctor.Doctor(home=tmp_path, repo=ROOT, output=lines.append, isolation_config=config)
    check.worker_isolation(tmp_path)
    assert all(line.startswith('PASS') for line in lines), lines
    reached['write']['spool'] = True
    lines.clear()
    check.worker_isolation(tmp_path)
    assert any(line.startswith('FAIL worker isolation working') for line in lines) and check.failed


# ---- opt-in integration probe on a host where the installer ran ----------------------------------------------------
@pytest.mark.skipif(os.getenv('LINKO_ISOLATION_PROBE') != '1', reason='opt-in: set LINKO_ISOLATION_PROBE=1 on an '
                    'installed host')
def test_installed_worker_user_cannot_reach_secrets_database_or_spool():
    found = isolation.detect()
    assert found, 'worker isolation is not installed for this service user'
    data = Settings().data_dir
    home = Path.home()
    targets = {'--read': [home / '.config/linko-bot-manager.env', home / '.ssh', data / 'manager.sqlite3'],
               '--write': [data / 'repair']}
    argv = [*found.prefix(), 'probe', *[a for flag, paths in targets.items() for p in paths for a in (flag, str(p))]]
    result = json.loads(subprocess.run(argv, capture_output=True, text=True, timeout=60, check=True).stdout)
    assert result['user'] == found.user
    assert not any(result['read'].values()) and not any(result['write'].values()), result

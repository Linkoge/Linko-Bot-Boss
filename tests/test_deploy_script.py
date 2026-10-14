"""scripts/deploy.py against a private disposable runtime: a copy of this code, its own Git repo, database and port.

The real application is started and restarted by a tiny service-control script, so the idle wait, backup, restart,
health check and automatic code+database rollback paths run end to end. Nothing here touches the live service.
"""
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy_script', ROOT / 'scripts/deploy.py')
deploy_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_script)

CTL = '''import fcntl, os, signal, subprocess, sys, time
from pathlib import Path
action, state, repo = sys.argv[1:4]
os.makedirs(os.environ['AI_MANAGER_DATA'] + '/deploy', exist_ok=True)
lock = open(os.environ['AI_MANAGER_DATA'] + '/deploy/checkout.lock', 'a')
try:
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    seen = 'free'
except OSError:
    seen = 'fenced'
with open(Path(state) / 'probe', 'a') as probe:
    probe.write(action + ':' + seen + '\\n')
lock.close()
pid_file = Path(state) / 'pid'
if action == 'start':
    log = open(Path(state) / 'service.log', 'ab')
    process = subprocess.Popen([sys.executable, '-m', 'ai_manager.cli', 'serve'], cwd=repo, stdout=log, stderr=log,
                               start_new_session=True)
    pid_file.write_text(str(process.pid))
elif pid_file.exists():
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            os.kill(pid, 0)
            time.sleep(.2)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pid_file.unlink()
'''


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, message, **edits):
    for name, (old, new) in edits.items():
        path = repo / name
        text = path.read_text()
        assert old in text, (name, old)
        path.write_text(text.replace(old, new, 1))
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '--allow-empty', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


class Runtime:
    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path
        self.repo, self.data, self.state = tmp_path / 'live', tmp_path / 'data', tmp_path / 'service'
        self.repo.mkdir()
        self.state.mkdir()
        listed = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-co', '--exclude-standard'], capture_output=True,
                                text=True, check=True).stdout.splitlines()
        for name in listed:
            source = ROOT / name
            if source.is_file():
                (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
                (self.repo / name).write_bytes(source.read_bytes())
        git(self.repo, 'init', '-q', '-b', 'main')
        self.base = commit(self.repo, 'live code')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        (tmp_path / 'ctl.py').write_text(CTL)
        for key, value in {'AI_MANAGER_DATA': self.data, 'AI_MANAGER_PORT': self.port,
                           'AI_MANAGER_PROJECT_ROOTS': tmp_path / 'projects', 'AI_MANAGER_REPO': self.repo,
                           'AI_MANAGER_SECURE_COOKIE': '0'}.items():
            monkeypatch.setenv(key, str(value))
        (tmp_path / 'projects').mkdir()
        ctl = [sys.executable, str(tmp_path / 'ctl.py')]
        self.config = {'repo': str(self.repo), 'data': str(self.data), 
                       'url': f'http://127.0.0.1:{self.port}', 'test_cmd': None, 'health_seconds': 25,
                       'poll_seconds': 0.1, 'idle_seconds': 20,
                       'service_cmds': {'stop': [*ctl, 'stop', str(self.state), str(self.repo)],
                                        'start': [*ctl, 'start', str(self.state), str(self.repo)]}}
        self.deploy = deploy_script.Deploy(self.config)

    def start(self):
        subprocess.run(self.config['service_cmds']['start'], check=True)
        self.deploy.wait_health(self.base)

    def stop(self):
        subprocess.run(self.config['service_cmds']['stop'], check=False)

    def request(self, **extra):
        value = {'id': os.urandom(8).hex(), 'source': 'owner', 'requested_at': time.time(), **extra}
        deploy_script.atomic(self.data / 'deploy' / 'request.json', value)
        return value

    def run(self):
        return deploy_script.Deploy(self.config).main()

    def sql(self, query, args=()):
        with sqlite3.connect(self.data / 'manager.sqlite3', timeout=10) as conn:
            return conn.execute(query, args).fetchall()

    def running(self):
        return deploy_script.Deploy(self.config).running_commit()

    def result(self):
        return deploy_script.read(self.data / 'deploy' / 'result.json')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    value = Runtime(tmp_path, monkeypatch)
    yield value
    value.stop()


def test_success_backs_up_restarts_on_new_main_and_persists_result(runtime):
    runtime.start()
    runtime.sql("INSERT INTO settings VALUES('survivor','1')")
    new = commit(runtime.repo, 'docs change', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss (deployed)')})
    runtime.request()
    result = runtime.run()
    assert result['status'] == 'success' and result['commit'] == new and result['previous'] == runtime.base
    assert runtime.running() == new
    assert runtime.sql("SELECT value FROM settings WHERE key='survivor'") == [('1',)]
    assert sqlite3.connect(Path(result['backup']) / 'before.sqlite3').execute(
        "SELECT value FROM settings WHERE key='survivor'").fetchone()
    assert not (runtime.data / 'deploy/request.json').exists() and not (runtime.data / 'deploy/state.json').exists()
    assert deploy_script.read(runtime.data / 'deploy/results' / (result['id'] + '.json')) == result
    assert 'success: deployed' in (runtime.data / 'deploy/last-deploy.txt').read_text()
    assert runtime.sql("SELECT count(*) FROM sessions") == [(0,)]  # the health check's temporary session is gone
    # Re-requesting the live version is a no-op, not a restart.
    pid = (runtime.state / 'pid').read_text()
    runtime.request()
    assert 'already live' in runtime.run()['reason'] and (runtime.state / 'pid').read_text() == pid


def test_unhealthy_release_rolls_back_code_and_database(runtime):
    runtime.start()
    good = commit(runtime.repo, 'good', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss v2')})
    runtime.request()
    assert runtime.run()['status'] == 'success'
    runtime.sql("INSERT INTO settings VALUES('before-bad-deploy','kept')")
    bad = commit(runtime.repo, 'bad release', **{'ai_manager/web.py': (
        '"scheduler_running": bool(scheduler.job and not scheduler.job.done()),', '"scheduler_running": False,'),
        'ai_manager/pipeline.py': ("SETTING = 'auto_deploy:'", "SETTING = 'auto_deploy:'\nimport sqlite3 as _s;"
                                   "_c=_s.connect(os.environ['AI_MANAGER_DATA']+'/manager.sqlite3');"
                                   "_c.execute(\"INSERT OR REPLACE INTO settings VALUES('bad-marker','1')\");_c.commit();open(os.environ['AI_MANAGER_DATA']+'/bad-ran','w').close()")})
    runtime.config['health_seconds'] = 6
    runtime.request()
    result = runtime.run()
    assert result['status'] == 'rolled_back' and result['commit'] == bad and result['previous'] == good
    assert 'scheduler' in result['reason'] and 'restored' in result['reason']
    assert git(runtime.repo, 'rev-parse', 'HEAD') == good == git(runtime.repo, 'rev-parse', 'main')
    assert git(runtime.repo, 'rev-parse', 'linko-failed-' + result['id']) == bad
    assert runtime.running() == good
    assert (runtime.data / 'bad-ran').exists()  # the bad release really ran and wrote to the database
    assert runtime.sql("SELECT value FROM settings WHERE key='bad-marker'") == []
    assert runtime.sql("SELECT value FROM settings WHERE key='before-bad-deploy'") == [('kept',)]
    assert 'rolled back' in (runtime.data / 'deploy/last-deploy.txt').read_text()


def test_service_that_never_starts_is_rolled_back(runtime):
    runtime.start()
    with (runtime.repo / 'ai_manager/__init__.py').open('a') as stream:
        stream.write('raise SystemExit(3)\n')
    git(runtime.repo, 'commit', '-qam', 'does not start')
    runtime.config['health_seconds'] = 4
    runtime.request()
    result = runtime.run()
    assert result['status'] == 'rolled_back' and runtime.running() == runtime.base
    assert git(runtime.repo, 'rev-parse', 'main') == runtime.base


def test_idle_query_counts_every_kind_of_running_work(tmp_path):
    from ai_manager.db import Database
    db = Database(tmp_path / 'manager.sqlite3')
    deploy = deploy_script.Deploy({'repo': str(tmp_path), 'data': str(tmp_path)})
    assert deploy.busy() == {}
    with db.transaction() as conn:
        conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('p','p','/p','a',1,1)")
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at,delivery) VALUES('e1','manager','running',1,NULL)")
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at,finished_at,delivery) "
                     "VALUES('e2','manager','completed',1,2,'running')")
        conn.execute("INSERT INTO conversations(id,context,created_at) VALUES('c','c',1)")
        conn.execute("INSERT INTO messages VALUES('m','c','assistant','x','streaming',NULL,1)")
        conn.execute("INSERT INTO tasks(id,project_id,description,lease_owner,created_at,updated_at) "
                     "VALUES('t','p','x','e1',1,1)")
    assert set(deploy.busy()) == {'unfinished_executions', 'leases', 'streaming_messages', 'deliveries'}
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET finished_at=3,delivery='delivered'")
        conn.execute("UPDATE messages SET status='completed'")
        conn.execute("UPDATE tasks SET lease_owner=NULL")
    assert deploy.busy() == {}


def test_waits_for_idle_and_never_restarts_while_work_runs(runtime):
    runtime.start()
    new = commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss idle')})
    pid = (runtime.state / 'pid').read_text()
    busy = {'now': True}
    deploy = deploy_script.Deploy(runtime.config)
    deploy.busy = lambda: {'leases': 1} if busy['now'] else {}
    runtime.request()
    holder = {}
    thread = threading.Thread(target=lambda: holder.update(result=deploy.main()))
    thread.start()
    state_file = runtime.data / 'deploy/state.json'
    for _ in range(100):
        if (deploy_script.read(state_file) or {}).get('phase') == 'waiting_idle':
            break
        time.sleep(.1)
    time.sleep(1)
    assert deploy_script.read(state_file)['phase'] == 'waiting_idle'
    assert (runtime.state / 'pid').read_text() == pid and runtime.running() == runtime.base
    busy['now'] = False
    thread.join(60)
    assert holder['result']['status'] == 'success' and runtime.running() == new


def test_not_idle_within_the_limit_fails_without_restart(runtime):
    runtime.start()
    commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss never idle')})
    runtime.config['idle_seconds'] = 1
    deploy = deploy_script.Deploy(runtime.config)
    deploy.busy = lambda: {'unfinished_executions': 1}
    runtime.request()
    result = deploy.main()
    assert result['status'] == 'failed' and 'not idle' in result['reason'] and runtime.running() == runtime.base


def test_hold_and_withdrawal_stop_the_deploy_before_any_restart(runtime):
    runtime.start()
    commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss held')})
    project_id = 'p' * 32
    runtime.sql("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES(?,?,?,?,1,1)",
                (project_id, 'x', str(runtime.root / 'x'), 'project-x'))
    pid = (runtime.state / 'pid').read_text()
    runtime.request(source='auto', project_id=project_id)  # Auto-deploy setting absent == OFF
    result = runtime.run()
    assert result['status'] == 'cancelled' and 'off' in result['reason']
    runtime.sql("INSERT INTO settings VALUES(?,?)", ('auto_deploy:' + project_id, json.dumps({'enabled': True})))
    runtime.sql("UPDATE projects SET status='paused' WHERE id=?", (project_id,))
    runtime.request(source='auto', project_id=project_id)
    assert 'paused' in runtime.run()['reason']
    runtime.sql("UPDATE projects SET status='active' WHERE id=?", (project_id,))
    request = runtime.request(source='auto', project_id=project_id)
    deploy = deploy_script.Deploy(runtime.config)
    deploy.busy = lambda: {'leases': 1}
    holder = {}
    thread = threading.Thread(target=lambda: holder.update(result=deploy.main()))
    thread.start()
    time.sleep(1)
    (runtime.data / 'deploy/request.json').unlink()  # owner cancels while waiting for idle
    thread.join(30)
    assert holder['result']['status'] == 'cancelled' and holder['result']['id'] == request['id']
    assert (runtime.state / 'pid').read_text() == pid and runtime.running() == runtime.base


def test_failing_tests_dirty_checkout_and_missing_install_change_nothing(runtime, tmp_path):
    runtime.start()
    new = commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss tested')})
    pid = (runtime.state / 'pid').read_text()
    runtime.config['test_cmd'] = [sys.executable, '-c', 'print("1 failed"); raise SystemExit(1)']
    runtime.request()
    result = runtime.run()
    assert result['status'] == 'failed' and 'full backend suite failed' in result['reason'] and '1 failed' in result['reason']
    runtime.config['test_cmd'] = [sys.executable, '-c', 'raise SystemExit(0)']
    (runtime.repo / 'README.md').write_text('local edit\n')
    runtime.request()
    assert 'uncommitted' in runtime.run()['reason']
    git(runtime.repo, 'checkout', '--', 'README.md')
    assert (runtime.state / 'pid').read_text() == pid and runtime.running() == runtime.base
    runtime.request()
    assert runtime.run()['status'] == 'success' and runtime.running() == new  # passing tests run once, then deploy
    with pytest.raises(SystemExit, match='not installed'):
        deploy_script.load_config(None, tmp_path / 'nowhere')


def test_installer_writes_config_units_and_first_request(tmp_path, monkeypatch):
    installer = importlib.util.spec_from_file_location('install_deploy', ROOT / 'scripts/install_deploy.py')
    module = importlib.util.module_from_spec(installer)
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    installer.loader.exec_module(module)
    repo, data, home, bin_dir = tmp_path / 'repo', tmp_path / 'data', tmp_path / 'home', tmp_path / 'bin'
    (repo / '.venv/bin').mkdir(parents=True)
    (repo / '.venv/bin/python').symlink_to(sys.executable)
    (repo / 'deploy').mkdir()
    for name in module.UNITS:
        (repo / 'deploy' / (name + '.in')).write_text((ROOT / 'deploy' / (name + '.in')).read_text())
    bin_dir.mkdir()
    log = tmp_path / 'systemctl.log'
    (bin_dir / 'systemctl').write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (bin_dir / 'systemctl').chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    module.install(repo, data, home)
    config = json.loads((data / 'deploy/installed.json').read_text())
    assert config['repo'] == str(repo) and 'branch' not in config
    path_unit = (home / '.config/systemd/user/linko-bot-manager-deploy.path').read_text()
    assert f'PathExists={data}/deploy/request.json' in path_unit and '@' not in path_unit
    service = (home / '.config/systemd/user/linko-bot-manager-deploy.service').read_text()
    assert f'{repo}/scripts/deploy.py' in service and '@' not in service
    assert json.loads((data / 'deploy/request.json').read_text())['reason'] == 'first deploy after install'
    calls = log.read_text()
    assert 'daemon-reload' in calls and 'enable --now linko-bot-manager-deploy.path' in calls
    module.install(repo, data, home)  # Safe to re-run; the queued first request is kept.
    assert (data / 'deploy/request.json').exists()


def probes(runtime):
    return (runtime.state / 'probe').read_text().split()


def test_checkout_is_fenced_from_idle_check_through_stop_start_and_rollback(runtime):
    runtime.start()
    assert probes(runtime) == ['start:free']
    commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss fenced')})
    runtime.request()
    assert runtime.run()['status'] == 'success'
    assert probes(runtime) == ['start:free', 'stop:fenced', 'start:fenced']
    commit(runtime.repo, 'bad', **{'ai_manager/web.py': (
        '"scheduler_running": bool(scheduler.job and not scheduler.job.done()),', '"scheduler_running": False,')})
    runtime.config['health_seconds'] = 5
    runtime.request()
    assert runtime.run()['status'] == 'rolled_back'
    assert probes(runtime)[3:] == ['stop:fenced', 'start:fenced', 'stop:fenced', 'start:fenced']
    from ai_manager import fence
    with fence.hold(runtime.data, True):  # released again after the deploy
        pass


def test_work_that_slips_in_before_the_fence_is_not_interrupted(runtime):
    runtime.start()
    commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss slip')})
    deploy = deploy_script.Deploy(runtime.config)
    calls = {'n': 0}

    def busy():  # idle at the first look, work appears once the fence is held, then finishes
        calls['n'] += 1
        return {'leases': 1} if calls['n'] == 2 else {}
    deploy.busy = busy
    runtime.request()
    result = deploy.main()
    assert result['status'] == 'success' and calls['n'] >= 3
    assert probes(runtime)[1:] == ['stop:fenced', 'start:fenced']


def test_rollback_still_restarts_the_service_when_the_code_rollback_fails(runtime, monkeypatch):
    runtime.start()
    commit(runtime.repo, 'bad', **{'ai_manager/web.py': (
        '"scheduler_running": bool(scheduler.job and not scheduler.job.done()),', '"scheduler_running": False,')})
    runtime.config['health_seconds'] = 4
    deploy = deploy_script.Deploy(runtime.config)

    def broken(*args):
        raise RuntimeError('git exploded')
    monkeypatch.setattr(deploy, 'rollback', broken)
    runtime.request()
    result = deploy.main()
    assert result['status'] == 'rollback_failed' and 'code rollback failed: git exploded' in result['reason']
    assert probes(runtime).count('start:fenced') == 2  # deploy start plus the rollback attempt to start again
    assert (runtime.state / 'pid').exists()


def test_deploy_and_merge_agree_on_a_master_branch(runtime):
    from ai_manager import fence
    from ai_manager.pipeline import merge_into_main
    git(runtime.repo, 'branch', '-m', 'master')
    runtime.start()
    assert fence.main_branch(runtime.repo, runtime.config.get('branch')) == 'master'
    git(runtime.repo, 'checkout', '-q', '-b', 'work')
    work = commit(runtime.repo, 'work', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss master')})
    git(runtime.repo, 'checkout', '-q', 'master')
    head, changed = merge_into_main(runtime.repo, work)
    assert changed and head == work and git(runtime.repo, 'symbolic-ref', '--short', 'HEAD') == 'master'
    git(runtime.repo, 'checkout', '-q', '--detach')  # a deploy must return to master, not to a hardcoded main
    runtime.request()
    result = runtime.run()
    assert result['status'] == 'success' and git(runtime.repo, 'symbolic-ref', '--short', 'HEAD') == 'master'

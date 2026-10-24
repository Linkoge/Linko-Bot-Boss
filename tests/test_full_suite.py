"""The deploy's full backend suite runs in parallel from one definition: scripts/full_suite.py."""
import importlib.util
import json
import os
import sys
import time

from test_deploy_script import ROOT, commit, deploy_script
from test_deploy_script import runtime as runtime_fixture

runtime = runtime_fixture


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option(argv, flag):
    return [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == flag]


def test_the_suite_runs_in_parallel_then_runs_serial_tests_alone():
    suite = load('full_suite')
    assert 1 <= suite.WORKERS <= suite.MAX_WORKERS
    (first, parallel), (second, serial) = suite.phases(['-k', 'x'], xdist=True)
    assert (first, second) == ('parallel', 'serial')
    assert option(parallel, '-n') == [str(suite.WORKERS)]
    assert option(parallel, '--dist') == ['loadfile']  # a module's shared browser page stays on one worker
    assert option(parallel, '-m') == ['pytest', 'not serial'] and parallel[-2:] == ['-k', 'x']
    assert '-n' not in serial and 'no:xdist' in option(serial, '-p') and option(serial, '-m') == ['pytest', 'serial']
    # A dev dependency: an environment synced from the lock file has it (the deploy syncs after its first test run).
    assert '"pytest-xdist' in (ROOT / 'pyproject.toml').read_text()
    (_, fallback), _ = suite.phases([], xdist=False)  # a venv not yet synced with the new lock file
    assert '-n' not in fallback and option(fallback, '-m') == ['pytest', 'not serial']


def test_a_failing_phase_fails_the_suite_and_is_named_last(monkeypatch, capsys):
    suite = load('full_suite')
    fail = [sys.executable, '-c', 'print("1 failed, 2 passed"); raise SystemExit(1)']
    empty = [sys.executable, '-c', 'raise SystemExit(5)']  # nothing selected in that phase is not a failure
    monkeypatch.setattr(suite, 'phases', lambda args: [('parallel', fail), ('serial', empty)])
    assert suite.main([]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == 'Full suite failed: parallel phase: 1 failed, 2 passed'
    monkeypatch.setattr(suite, 'phases', lambda args: [('parallel', empty), ('serial', empty)])
    assert suite.main([]) == 0


def test_installer_and_older_installs_both_run_the_parallel_suite(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    installer = load('install_deploy')
    repo, data, home, bin_dir = tmp_path / 'repo', tmp_path / 'data', tmp_path / 'home', tmp_path / 'bin'
    (repo / '.venv/bin').mkdir(parents=True)
    (repo / '.venv/bin/python').symlink_to(sys.executable)
    (repo / 'deploy').mkdir()
    for name in installer.UNITS:
        (repo / 'deploy' / (name + '.in')).write_text((ROOT / 'deploy' / (name + '.in')).read_text())
    bin_dir.mkdir()
    (bin_dir / 'systemctl').write_text('#!/bin/sh\n')
    (bin_dir / 'systemctl').chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    installer.install(repo, data, home)
    saved = json.loads((data / 'deploy/installed.json').read_text())['test_cmd']
    assert saved == [str(repo / '.venv/bin/python'), deploy_script.SUITE] == deploy_script.suite_command(saved[0])
    assert (ROOT / deploy_script.SUITE).is_file()
    legacy = ['/venv/python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider']  # saved by installs before the runner
    assert deploy_script.test_command(legacy) == ['/venv/python', deploy_script.SUITE]
    custom = [sys.executable, '-c', 'raise SystemExit(0)']
    assert deploy_script.test_command(custom) == custom and deploy_script.test_command(None) is None


def test_the_deploy_test_timeout_still_bounds_the_suite(runtime):
    runtime.start()
    commit(runtime.repo, 'docs', **{'README.md': ('# Linko Bot Boss', '# Linko Bot Boss slow tests')})
    pid = (runtime.state / 'pid').read_text()
    runtime.config['test_cmd'] = [sys.executable, '-c', 'import time; time.sleep(60)']
    runtime.config['test_seconds'] = 1
    runtime.request()
    started = time.monotonic()
    result = runtime.run()
    assert time.monotonic() - started < 30
    assert result['status'] == 'failed' and (runtime.state / 'pid').read_text() == pid and runtime.running() == runtime.base

"""Smoke suite: python -m pytest -q -m smoke. Contract: README.md."""
import os
import time

import pytest

# Smoke takes ~23s idle (119 tests, temp dir on disk) and ~53s with all 4 CPUs busy elsewhere. The ~2x margin absorbs a
# host shared with other projects' parallel suites and gates a smoke that really slows down still fails. The deploy-flow test lifts it for its nested gate (LINKO_SMOKE_BUDGET_SECONDS=0).
SMOKE_BUDGET_SECONDS = 45
# Test databases are disposable: no fsync per commit (ai_manager/db.py synchronous()). With the temporary directory on a
# real disk instead of tmpfs, fsync alone took smoke from ~38s to ~80s. Set here so server subprocesses inherit it.
os.environ.setdefault('AI_MANAGER_SQLITE_SYNCHRONOUS', 'OFF')
# Test passwords protect nothing: minimal argon2 parameters (ai_manager/auth.py password_hasher()) instead of ~85 ms of
# CPU and 64 MiB per hash, the suite's largest fixed cost after fsync and the part that slows most on a loaded host.
os.environ.setdefault('AI_MANAGER_TEST_WEAK_PASSWORD_HASH', '1')
# The wall-clock budget guards the real smoke gate. A test that runs the gate as a nested step of something else (the
# deploy-flow test) sets this to 0: what it verifies is the pipeline, and the nested run's 180s timeout still applies.
BUDGET_ENV = 'LINKO_SMOKE_BUDGET_SECONDS'


def pytest_configure(config):
    # Registered here because dependency/config files need separate deployment review.
    config.addinivalue_line('markers', 'smoke: fast core-path suite run by every assignment')
    # scripts/full_suite.py runs these alone after the parallel phase. Mark a test serial only when sharing the host
    # changes its meaning; today: the full deploy flow, whose real gate runs a nested smoke suite against its budget.
    config.addinivalue_line('markers', 'serial: run alone after the parallel phase of scripts/full_suite.py')
    config.smoke_started = time.monotonic()


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if config.getoption('markexpr') != 'smoke' or exitstatus:
        return
    elapsed = time.monotonic() - config.smoke_started
    budget = float(os.environ.get(BUDGET_ENV, SMOKE_BUDGET_SECONDS))
    if budget and elapsed > budget:
        print(f'\nSmoke suite exceeded its {budget:g}s budget ({elapsed:.1f}s); keep it fast.')
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def isolated_claude_login(tmp_path_factory, monkeypatch):
    """Tests never find a real Claude login, so the host's usage request cannot reach the network."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path_factory.mktemp('claude-config')))
    monkeypatch.setenv('AI_MANAGER_TMPDIR', str(tmp_path_factory.mktemp('scratch')))  # never the real scratch space

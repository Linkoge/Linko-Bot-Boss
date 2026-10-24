"""Smoke suite: python -m pytest -q -m smoke. Contract: README.md."""
import time

import pytest

# Smoke takes ~21s idle (105 tests). The ~2x margin absorbs a host shared with other projects' parallel suites and gates
# (30s flaked the nested smoke of the serial deploy-flow test at load 10); a smoke that really slows down still fails.
SMOKE_BUDGET_SECONDS = 45


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
    if elapsed > SMOKE_BUDGET_SECONDS:
        print(f'\nSmoke suite exceeded its {SMOKE_BUDGET_SECONDS}s budget ({elapsed:.1f}s); keep it fast.')
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def isolated_claude_login(tmp_path_factory, monkeypatch):
    """Tests never find a real Claude login, so the host's usage request cannot reach the network."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path_factory.mktemp('claude-config')))

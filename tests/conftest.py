"""Smoke suite: python -m pytest -q -m smoke. Contract: docs/AUTO_DEPLOY.md."""
import time

SMOKE_BUDGET_SECONDS = 20  # The full backend suite takes about a minute; smoke must stay well below it.


def pytest_configure(config):
    # Registered here because dependency/config files need separate deployment review.
    config.addinivalue_line('markers', 'smoke: fast core-path suite run by every assignment')
    config.smoke_started = time.monotonic()


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if config.getoption('markexpr') != 'smoke' or exitstatus:
        return
    elapsed = time.monotonic() - config.smoke_started
    if elapsed > SMOKE_BUDGET_SECONDS:
        print(f'\nSmoke suite exceeded its {SMOKE_BUDGET_SECONDS}s budget ({elapsed:.1f}s); keep it fast.')
        session.exitstatus = 1

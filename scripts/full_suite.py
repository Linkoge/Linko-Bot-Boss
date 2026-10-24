#!/usr/bin/env python3
"""The full backend suite: the one definition the deploy runs (scripts/deploy.py). Contract: README.md "Run and develop".

    python scripts/full_suite.py [pytest selection args...]

Phase 1 runs every test not marked `serial` in parallel (pytest-xdist, WORKERS processes). Each test file stays on one
worker in file order (`--dist loadfile`), because browser modules share one page across their tests. Phase 2 then runs the
`serial` tests one at a time with the host to themselves (tests/conftest.py says why each is serial). Both phases always
run; the exit status is non-zero when either fails, and the last line names the failed phase for the deploy receipt.
Plain `python -m pytest -q` still runs the whole suite in one process, for debugging.
"""
import importlib.util
import os
import subprocess
import sys

# Tests wait on browsers, services and git about half the time, so 1.5 workers per core keeps the CPUs busy; the cap
# bounds memory (each worker may hold a Chromium and a uvicorn). Measured on the 4-core, 8 GB host: 4 -> 220 s, 6 -> 191 s.
MAX_WORKERS = 6
WORKERS = max(1, min((os.cpu_count() or 1) * 3 // 2, MAX_WORKERS))
PYTEST = ['-m', 'pytest', '-q', '-p', 'no:cacheprovider']
NO_TESTS = 5  # pytest's exit status when a selection collects nothing
# The deploy tests the new commit before it syncs dependencies (only once the service is stopped), so the first deploy
# that adds pytest-xdist runs with a venv without it: phase 1 then runs in one process instead of failing on `-n`.
XDIST = importlib.util.find_spec('xdist') is not None


def phases(args=(), workers=WORKERS, xdist=XDIST):
    spread = ['-n', str(workers), '--dist', 'loadfile'] if xdist else []
    return [('parallel', [sys.executable, *PYTEST, *spread, '-m', 'not serial', *args]),
            ('serial', [sys.executable, *PYTEST, '-p', 'no:xdist', '-m', 'serial', *args])]


def run(name, argv):
    """Stream the phase's output and return (exit status, its last non-empty line)."""
    last = ''
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            sys.stdout.write(line)
            last = line.strip() or last
    sys.stdout.flush()
    return (0 if process.returncode == NO_TESTS else process.returncode), last


def main(args):
    failed = []
    if not XDIST:
        print('pytest-xdist is not installed in this environment; the parallel phase runs in one process', flush=True)
    for name, argv in phases(args):
        code, last = run(name, argv)
        if code:
            failed.append(f'{name} phase: {last}')
    if failed:
        print('Full suite failed: ' + '; '.join(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

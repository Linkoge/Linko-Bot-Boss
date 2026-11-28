#!/usr/bin/env python3
"""The full backend suite: the one definition the deploy runs (scripts/deploy.py). Contract: README.md "Run and develop".

    python scripts/full_suite.py [pytest selection args...]

Phase 1 runs every test not marked `serial` in parallel (pytest-xdist, WORKERS processes). Each test file stays on one
worker in file order (`--dist loadfile`), because browser modules share one page across their tests. Phase 2 then runs the
`serial` tests one at a time with the host to themselves (tests/conftest.py says why each is serial). Both phases always
run; the exit status is non-zero when either fails, and the last line names the failed phase for the deploy receipt.
A failed parallel phase is followed by one serial rerun of only its failed tests: tests that pass then are flaky (recorded,
not failures); any that still fail fail the suite. The failed tests (node id and one-line error) are listed before the last
line. With AI_MANAGER_FLAKY_JOURNAL set (the deploy sets it) each failure is appended to that journal (ai_manager/flaky.py).
Plain `python -m pytest -q` still runs the whole suite in one process, for debugging.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_manager import flaky, scratch  # noqa: E402

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


JUNIT = ['-o', 'junit_family=xunit1']
FAILED, FLAKY = 'Failed tests:', 'Flaky tests (failed in the parallel phase, passed on the serial rerun; recorded):'


def junit(argv, xml):
    return [*argv, '--junitxml', str(xml), *JUNIT]


def read_failures(xml):
    try:
        return flaky.failures(Path(xml).read_text())
    except OSError:
        return []


def rerun(items, directory):
    """Run only these failed tests once more, serially. Returns the ones that still fail, or None if that run reported nothing."""
    xml = Path(directory) / 'rerun.xml'
    argv = [sys.executable, *PYTEST, '-p', 'no:xdist', *[node for node, _ in items]]
    print('Rerunning ' + str(len(items)) + ' failed test(s) serially, once', flush=True)
    code, _ = run('rerun', junit(argv, xml))
    if not code:
        return []
    return read_failures(xml) or None


def main(args):
    scratch.apply()  # pytest and its children keep temp files on the normal disk, not /tmp
    failed, real, flaked = [], [], []
    journal, commit = flaky.path_of(), os.environ.get(flaky.COMMIT_ENV, '')
    if not XDIST:
        print('pytest-xdist is not installed in this environment; the parallel phase runs in one process', flush=True)
    with tempfile.TemporaryDirectory(prefix='full-suite-') as directory:
        for name, argv in phases(args):
            xml = Path(directory) / (name + '.xml')
            code, last = run(name, junit(argv, xml))
            if not code:
                continue
            items = read_failures(xml)
            if name == 'parallel' and items:
                still = rerun(items, directory)
                maybe, hard = flaky.decide(items, still)
                flaked += maybe
                if not hard and still is not None:
                    continue  # every failure passed on the serial rerun: recorded as flaky, not a failure
                items = hard
                last += f' ({len(hard)} still failing on the serial rerun)' if still is not None else ' (the rerun reported nothing)'
            real += items
            failed.append(f'{name} phase: {last}')
        flaky.append(journal, [flaky.record(n, e, 'flaky', commit) for n, e in flaked]
                     + [flaky.record(n, e, 'real', commit) for n, e in real])
    if flaked:
        print('\n'.join([FLAKY, *flaky.failure_lines(flaked)]))
    if real:
        print('\n'.join([FAILED, *flaky.failure_lines(real)]))
    if failed:
        print('Full suite failed: ' + '; '.join(failed))
    elif flaked:
        print(f'Full suite passed with {len(flaked)} flaky test(s) recorded (they passed on the serial rerun)')
    if not failed and not args:
        flaky.mark_fixed(journal, commit)  # a complete green run: earlier real failures are fixed by this commit
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

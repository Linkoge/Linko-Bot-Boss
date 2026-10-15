"""Worker check plan and actual changed-file listing. Contract: docs/AUTO_DEPLOY.md.

One uniform policy for every task: smoke + targeted tests once, at most five repair rounds, then report. No risk
tiers, no AI review, no escalation. The full backend suite runs once, in the deploy script.
"""
import subprocess

from .assignment_workflow import REPAIR_LIMIT


def plan():
    return {'checks': ['Smoke suite once (python -m pytest -q -m smoke)',
                       'Targeted tests for the changed behavior', 'Relevant build/lint'],
            'skip': ['Full backend suite (the deploy script runs it once)', 'Independent review or escalation'],
            'repair_limit': REPAIR_LIMIT,
            'report': 'Final reply at most 12 short lines; preserve commands/outputs in the execution record. '
                      'Fix failures up to five rounds total, rerunning affected checks only, then stop and report '
                      'the concrete failure. End with RESULT: PASSED or RESULT: FAILED - <check>.'}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('git ' + args[0] + ' failed: ' + result.stderr.strip()[:300])
    return result.stdout


def changes(root, base):
    """Actual changed paths (tracked and untracked) relative to the assignment base."""
    paths = set(git(root, 'diff', '--name-only', base).splitlines())
    paths |= set(git(root, 'ls-files', '--others', '--exclude-standard').splitlines())
    return sorted(p for p in paths if p)

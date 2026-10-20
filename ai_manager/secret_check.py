"""Small, dependency-free checks of added lines in delivered Git commits. Never return source text."""
import os
import re
import subprocess
from pathlib import PurePosixPath

RULES = {
    'private-key': re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b'),
    'aws-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'openai-token': re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b'),
}


class ScanError(ValueError):
    pass


def git_bytes(root, *args, check=True, env=None):
    """Git failures must never disclose a remote URL, diff, hook output or credential."""
    try:
        result = subprocess.run(
            ['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null', *args],
            capture_output=True, timeout=60,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never', **(env or {})},
        )
    except (OSError, subprocess.SubprocessError):
        raise ScanError('git-operation-failed') from None
    if check and result.returncode:
        raise ScanError('git-operation-failed')
    return result.stdout if check else result


def sensitive(path):
    name = PurePosixPath(path).name.lower()
    return ((name == '.env' or name.startswith('.env.')) and name != '.env.example'
            or name.endswith(('.pem', '.key')) or name.startswith('id_rsa'))


def scan(root, base, head):
    """Inspect every commit in base..head (all history if base is None), including removed-later secrets.

    Combined merge diffs inspect resolutions, excluding lines simply inherited from either parent.
    """
    try:
        revisions = f'{base}..{head}' if base else head
        commits = git_bytes(root, 'rev-list', '--reverse', revisions).decode().splitlines()
        hits = set()
        for commit in commits:
            paths = git_bytes(root, 'diff-tree', '--root', '--cc', '-r', '--no-commit-id',
                              '--name-only', '-z', '--no-renames', commit).decode(errors='replace').split('\0')
            for path in filter(None, paths):
                # Deleted files have no delivered content; earlier commits are still inspected.
                if git_bytes(root, 'cat-file', '-e', f'{commit}:{path}', check=False).returncode:
                    continue
                if sensitive(path):
                    hits.add((path, 0, 'sensitive-filename'))
                patch = git_bytes(root, 'show', '--format=', '--root', '--cc', '--unified=0',
                                  '--no-ext-diff', '--no-textconv', '--no-renames', commit, '--', path)
                number, columns = 0, 1
                for line in patch.decode(errors='replace').splitlines():
                    if match := re.match(r'^(@@+) .* \+(\d+)(?:,\d+)? @@+', line):
                        number, columns = int(match[2]), len(match[1]) - 1
                    elif number and not line.startswith('\\'):
                        prefix, content = line[:columns], line[columns:]
                        if '-' in prefix:
                            continue
                        if '+' in prefix:
                            for rule, pattern in RULES.items():
                                if pattern.search(content):
                                    hits.add((path, number, rule))
                        number += 1
        return [{'file': p, 'line': n, 'rule': r} for p, n, r in sorted(hits)]
    except ScanError as error:
        return [{'file': '.', 'line': 0, 'rule': str(error)}]


def failures(hits):
    return [f"{h['file']}:{h['line']}: {h['rule']}" for h in hits[:30]]

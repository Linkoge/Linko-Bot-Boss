"""Bounded Git commands with sanitized errors for application-owned backups."""
import os
import subprocess

from .push_diagnosis import TAIL_BYTES, clean_output


class GitError(ValueError):
    def __init__(self, message='git-operation-failed', output=''):
        super().__init__(message)
        self.output = output  # redacted tail of Git's output, safe to parse and show


def git_bytes(root, *args, check=True, env=None):
    """Git failures must never disclose a remote URL, diff, hook output or credential."""
    try:
        result = subprocess.run(
            ['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null', *args],
            capture_output=True, timeout=60,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never', **(env or {})},
        )
    except subprocess.TimeoutExpired:
        raise GitError(output='git operation timed out') from None
    except (OSError, subprocess.SubprocessError):
        raise GitError() from None
    if check and result.returncode:
        raise GitError(output=clean_output(result.stderr[-TAIL_BYTES:] + b'\n' + result.stdout[-TAIL_BYTES:],
                                           [(env or {}).get('LINKO_GITHUB_CREDENTIAL')]))
    return result.stdout if check else result

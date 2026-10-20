"""Bounded Git commands with sanitized errors for application-owned backups."""
import os
import subprocess


class GitError(ValueError):
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
        raise GitError('git-operation-failed') from None
    if check and result.returncode:
        raise GitError('git-operation-failed')
    return result.stdout if check else result

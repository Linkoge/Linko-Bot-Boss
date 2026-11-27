"""Scratch space on the normal disk for every process the application spawns.

/tmp is a small RAM-backed tmpfs on the servers; pytest, Claude Code and build tools fill it. All spawned processes get
TMPDIR/TMP/TEMP/CLAUDE_CODE_TMPDIR pointing here (AI_MANAGER_TMPDIR, default ~/.cache/linko-tmp), one subfolder per run
where the run has an id. `cleanup` deletes old entries that do not belong to a running execution; it never leaves the folder.
"""
import os
import re
import shutil
import time
from pathlib import Path

VARIABLE = 'AI_MANAGER_TMPDIR'
DEFAULT = '~/.cache/linko-tmp'
KEYS = ('TMPDIR', 'TMP', 'TEMP', 'CLAUDE_CODE_TMPDIR')
MAX_AGE = 2 * 86400
INTERVAL = 3600
SAFE = re.compile(r'[^A-Za-z0-9_.-]')


def root(environ=None):
    return Path((os.environ if environ is None else environ).get(VARIABLE) or DEFAULT).expanduser()


def prepare(name=None, environ=None):
    """Create (0700) the scratch root, or its subfolder `name`, and return the path."""
    base = root(environ)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    if not name:
        return base
    folder = base / SAFE.sub('_', str(name))[:80]
    folder.mkdir(exist_ok=True, mode=0o700)
    return folder


def env(name=None, environ=None):
    """The variables that move a child's temp files here. Falls back to the plain root if the folder cannot be made."""
    try:
        folder = prepare(name, environ)
    except OSError:
        return {}
    return {key: str(folder) for key in KEYS}


def apply(name=None):
    """Point this process (and everything it starts) at the scratch space."""
    os.environ.update(env(name))


def cleanup(active=(), max_age=MAX_AGE, now=None, directory=None):
    """Delete direct entries of the scratch root older than `max_age` seconds whose name is not an active run.
    Symlinks are unlinked, never followed. Returns the removed names."""
    base, now, removed = Path(directory) if directory else root(), now or time.time(), []
    live = {SAFE.sub('_', str(item)) for item in active}
    try:
        entries = list(os.scandir(base))
    except OSError:
        return removed
    for entry in entries:
        try:
            if entry.name in live or now - entry.stat(follow_symlinks=False).st_mtime <= max_age:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                os.unlink(entry.path)
            removed.append(entry.name)
        except OSError:
            continue
    return removed

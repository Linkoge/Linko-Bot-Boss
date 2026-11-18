"""Remove leaked scratch folders of killed browser runs from the system temp directory."""
import os
import shutil
import tempfile
import time
from pathlib import Path

BROWSER_PREFIX = 'linko-browser-'
MAX_AGE = 3600


def sweep_stale(prefix=BROWSER_PREFIX, directory=None, max_age=MAX_AGE, now=None):
    """Delete `<prefix>*` real folders older than `max_age` seconds that this user owns. Returns the removed names."""
    base, now, removed = Path(directory or tempfile.gettempdir()), now or time.time(), []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return removed
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
            if (entry.name.startswith(prefix) and entry.is_dir(follow_symlinks=False)
                    and info.st_uid == os.getuid() and now - info.st_mtime > max_age):
                shutil.rmtree(entry.path, ignore_errors=True)
                removed.append(entry.name)
        except OSError:
            continue
    return removed

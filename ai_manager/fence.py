"""Standard-library checkout fence and branch resolution shared by the app and scripts/deploy.py.

One flock file (DATA/deploy/checkout.lock) serializes everything that touches the live checkout or starts work:
merges into main take it exclusively, task claims and chat submissions take it shared (briefly), and a deploy holds it
exclusively from its final idle check until the service is healthy again (or rolled back). While a deploy holds it,
no merge happens and no new work can start, so nothing is interrupted between the idle check and the restart.
"""
import fcntl
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    """The fence is held by a deploy or a merge."""


@contextmanager
def hold(data, exclusive=True, wait=0.0):
    path = Path(data) / 'deploy' / 'checkout.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = path.open('a')
    mode = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(stream, mode)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Busy('a deploy or merge holds the live checkout') from None
                time.sleep(0.1)
        yield
    finally:
        stream.close()  # closing releases the flock


def main_branch(repo, configured=None):
    """The one branch both the merge step and the deploy script use: configured, else main, else master."""
    for name in (configured, 'main', 'master'):
        if name and subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet', 'refs/heads/' + name],
                                   capture_output=True).returncode == 0:
            return name
    raise ValueError('No main branch (main or master) in the live checkout')

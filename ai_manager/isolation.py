"""Queue workers as a separate unprivileged Linux user. See README.md ("How work runs").

scripts/install_worker_user.py (run once with root) creates the worker user, writes CONFIG, a root-owned copy of
scripts/worker_launcher.py and a sudoers drop-in that lets the service user run only that launcher as the worker user.
The application then starts every worker provider process through it with a fixed argv, kills them through it (the
service user cannot signal another user's processes) and opens each task's workspace to the worker with ACLs on its own
files. Without the installation, workers keep the service user's rights and the application says so (WARNING).
"""
import json
import os
import pwd
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path('/etc/linko/worker.json')
SUDO, PYTHON = '/usr/bin/sudo', '/usr/bin/python3'
WARNING = ("Workers run with your user's rights: the separate worker user is not installed "
           "(run scripts/install_worker_user.py install as root; see README \"How work runs\").")
SIGNAL_SECONDS, ACL_SECONDS = 15, 600
# `sudo -l` lines granting every command without a password, e.g. "(ALL : ALL) NOPASSWD: ALL".
BLANKET = re.compile(r'^\s*\(\s*(ALL|root)\s*(:\s*\w+\s*)?\)\s*NOPASSWD:\s*ALL\s*$', re.M)


def blanket_nopasswd(listing):
    """True when `sudo -l` output grants the user every command as root without a password."""
    return bool(BLANKET.search(listing or ''))


@dataclass(frozen=True)
class Isolation:
    user: str
    launcher: str
    service_user: str
    python: str = PYTHON
    toolchain: tuple = ()  # directories the worker always reads (the installer opened them); never closed

    def prefix(self):
        """The only command the sudoers rule allows: the root-owned launcher, as the worker user, no shell."""
        return [SUDO, '-n', '-u', self.user, self.python, '-I', self.launcher]

    def wrap(self, program, args, tag, cwd=None):
        """argv that runs the configured `program` (codex or claude) as the worker user, tagged for kill."""
        return [*self.prefix(), 'run', '--tag', tag, *(['--cwd', str(cwd)] if cwd else []), program, '--', *args]

    def signal(self, tag, sig, wait=True):
        """Signal every worker-user process carrying this run tag. Never raises."""
        argv = [*self.prefix(), 'kill', '--tag', tag, '--signal', str(int(sig))]
        try:
            if not wait:
                subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            return subprocess.run(argv, capture_output=True, timeout=SIGNAL_SECONDS).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # -- workspace access: the service user changes ACLs on its own files; no root needed ---------------------------
    def setfacl(self, *args):
        subprocess.run(['setfacl', *args], check=True, capture_output=True, timeout=ACL_SECONDS)

    def granted(self, path, writable):
        """The recursive grant leaves a default entry for the worker on the directory itself."""
        try:
            listing = subprocess.run(['getfacl', '--omit-header', '--absolute-names', str(path)], capture_output=True,
                                     text=True, timeout=ACL_SECONDS).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        wanted = ['rwx'] if writable else ['r-x', 'rwx']
        return any(f'default:user:{self.user}:{perms}' in listing.split() for perms in wanted)

    def traverse(self, path):
        """Let the worker pass through our own ancestors of path (search only, no listing)."""
        uid = os.getuid()
        for parent in reversed(Path(path).parents):
            try:
                if parent.stat().st_uid == uid:
                    self.setfacl('-m', f'u:{self.user}:--x', str(parent))
            except OSError:
                continue

    def open(self, root, shared=None):
        """Give the worker its task workspace (read/write) and, for a Git worktree, the project's checkout (read) and
        Git directory (read/write). Files either user creates stay usable by both through default ACLs."""
        specs = {True: f'u:{self.user}:rwX,d:u:{self.user}:rwX,d:u:{self.service_user}:rwX',
                 False: f'u:{self.user}:rX,d:u:{self.user}:rX,d:u:{self.service_user}:rwX'}
        targets = [(Path(root), True)]
        if shared and Path(shared).resolve() != Path(root).resolve():
            targets = [(Path(shared), False), (Path(shared) / '.git', True), *targets]
        for path, writable in targets:
            if not self.granted(path, writable):
                self.setfacl('-R', '-m', specs[writable], str(path))
        for path, _ in targets:
            self.traverse(path)
        for path, writable in targets:  # after every traversal entry, which would narrow a gate it passes through
            self.setfacl('-m', f'u:{self.user}:' + ('rwx' if writable else 'r-x'), str(path))

    def close(self, *paths):
        """Shut the gate on a finished task's directories; contents keep their ACLs for the next open."""
        for path in paths:
            keep = any(Path(path).resolve() == Path(t).resolve() for t in self.toolchain)
            try:
                self.setfacl('-m', f'u:{self.user}:' + ('r-x' if keep else '---'), str(path))
            except (OSError, subprocess.SubprocessError):
                pass

    def share(self, files):
        """Let the worker read specific files outside its workspace (attached images)."""
        for path in files:
            self.traverse(path)
            self.setfacl('-m', f'u:{self.user}:r--', str(path))


def detect(path=CONFIG, root_uid=0):
    """The installed isolation, or None (same-user mode). AI_MANAGER_WORKER_ISOLATION=off forces same-user mode."""
    if os.getenv('AI_MANAGER_WORKER_ISOLATION', 'auto') == 'off':
        return None
    try:
        config = json.loads(Path(path).read_text())
        user, launcher = config['worker_user'], config['launcher']
        service = config['service_user']
        info = os.stat(launcher)
        pwd.getpwnam(user)
        me = pwd.getpwuid(os.getuid()).pw_name
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # A launcher the service or worker user could change is no boundary; another service user's install is not ours.
    if info.st_uid != root_uid or info.st_mode & 0o022 or service != me or user == me:
        return None
    return Isolation(user, launcher, service, config.get('python', PYTHON), tuple(config.get('toolchain', ())))


def status(path=CONFIG):
    found = detect(path)
    return {'active': bool(found), 'user': found.user if found else None, 'warning': '' if found else WARNING}

#!/usr/bin/python3
# Root-owned host adapter for one instance that runs as another Linux user (README "Multiple instances").
# Written by `scripts/instance_adapter.py apply|add NAME` to /usr/local/libexec/linko-NAME-instance; do not edit the
# installed copy, re-run the script. The unit runs it as `ExecStartPre=+... pre` and `ExecStartPost=+... post`.
#
# Layout "whole-checkout": the WHOLE checkout is bind-mounted read-only at CODE (one mount, so a new top-level path
# needs no change here). Secrets and the real .git are masked: .git by a per-instance fake git dir that only holds
# HEAD (release commit), every other MASK entry by an empty read-only tmpfs (directory) or /dev/null (file). Read
# access comes from the shared group GROUP (default ACL g:GROUP:rX on the checkout, set once by the script), so no
# per-file ACL is written here. The instance writes only in its own DATA directory.
#
# Self-contained on purpose: it runs as root and must never import code from the (non-root writable) checkout.
import os
import pwd
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- settings (written by scripts/instance_adapter.py) ---
LAYOUT = 'whole-checkout'
NAME = 'example'
USER = 'example'
OWNER = 'owner'
GROUP = 'linko-code'
CHECKOUT = '/home/owner/projects/Linko-Bot-Manager'
CODE = '/opt/linko-example/code'
STATE = '/var/lib/linko-example'
DATA = '/home/example/.local/share/linko-bot-manager-example'
PORT = 8788
MASK = ['.git', '.env', '.data', 'secrets', '.linko-runtime']
mounts = [('/home/owner/projects/Linko-Bot-Manager', '/opt/linko-example/code')]
# --- end settings ---


def run(*argv):
    subprocess.run([str(a) for a in argv], check=True)


def mounted(path):
    return subprocess.run(['mountpoint', '-q', str(path)]).returncode == 0


def bind_ro(source, target):
    run('mount', '--bind', source, target)
    run('mount', '-o', 'remount,bind,ro,nosuid,nodev', target)


def masked(names):
    """Top-level checkout entries never shown to the instance: MASK plus any .env-style file."""
    return sorted(n for n in names if n in MASK or n.startswith('.env') or n.endswith('.env'))


def fake_git():
    """The instance reads its release commit with `git rev-parse HEAD`; it gets HEAD only, never the real .git."""
    git = Path(STATE) / '.git'
    for folder in ('objects', 'refs'):
        (git / folder).mkdir(parents=True, exist_ok=True)
    if not (git / 'config').exists():
        (git / 'config').write_text('[core]\n\trepositoryformatversion = 0\n\tbare = false\n')
    commit = subprocess.check_output(['runuser', '-u', OWNER, '--', 'git', '-C', CHECKOUT, 'rev-parse', 'HEAD'],
                                     text=True).strip()
    (git / 'HEAD').write_text(commit + '\n')
    run('chown', '-R', f'{USER}:{USER}', git)
    return git


def pre():
    if mounted(CODE):
        run('umount', '-R', CODE)  # the previous layout, whatever it was (also the old per-path bind list)
    os.makedirs(CODE, mode=0o755, exist_ok=True)
    os.makedirs(STATE, mode=0o755, exist_ok=True)
    bind_ro(CHECKOUT, CODE)
    for name in masked(os.listdir(CHECKOUT)):
        source = os.path.join(CHECKOUT, name)
        target = os.path.join(CODE, name)
        if os.path.islink(source):
            continue  # never mount over a symlink target; it resolves outside the instance's view anyway
        if name == '.git' and os.path.isdir(source):
            bind_ro(fake_git(), target)
        elif os.path.isdir(source):
            run('mount', '-t', 'tmpfs', '-o', 'ro,nosuid,nodev,noexec,size=4k,mode=0555', 'tmpfs', target)
        else:
            bind_ro('/dev/null', target)
    for source, target in mounts[1:]:  # runtime outside the code view: interpreter, venv packages, provider CLI
        if not os.path.exists(source):
            raise RuntimeError(f'runtime mount source missing: {source}')
        if mounted(target):
            run('umount', target)
        if os.path.isdir(source):
            os.makedirs(target, mode=0o755, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            Path(target).touch(exist_ok=True)
        run('setfacl', '-P', '-R', '-m', f'g:{GROUP}:rX', source)  # outside the checkout: no inherited default ACL
        bind_ro(source, target)


def post():
    for _ in range(60):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/auth-status', timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f'{NAME} did not become healthy')


def data_access():
    """Open every user-controlled path without following symlinks; fix ownership after an owner-run deploy restore
    and give the owner (deploy) narrow ACLs on the database, its WAL files and the checkout lock."""
    who = pwd.getpwnam(USER)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    for component in Path(DATA).parts[1:]:
        child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = child
    if os.fstat(fd).st_uid != who.pw_uid:
        raise RuntimeError('Unexpected instance data owner')

    def acl(handle, value):
        subprocess.run(['setfacl', '-m', value, '/proc/self/fd/' + str(handle)], pass_fds=(handle,), check=True)

    acl(fd, f'u:{OWNER}:rwx,d:u:{OWNER}:rwx,d:u:{USER}:rwx')
    try:
        os.mkdir('deploy', mode=0o700, dir_fd=fd)
    except FileExistsError:
        pass
    deployfd = os.open('deploy', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    if os.fstat(deployfd).st_uid != who.pw_uid:
        raise RuntimeError('Unexpected deploy directory owner')
    acl(deployfd, f'u:{OWNER}:rwx,d:u:{OWNER}:rwx,d:u:{USER}:rwx')
    database = {'manager.sqlite3', 'manager.sqlite3-wal', 'manager.sqlite3-shm'}
    for directory, names in [(fd, [n for n in os.listdir(fd) if n in database]), (deployfd, ['checkout.lock'])]:
        for name in names:
            try:
                handle = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                                 | (os.O_CREAT if name == 'checkout.lock' else 0), mode=0o600, dir_fd=directory)
            except FileNotFoundError:
                continue
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError('Unsafe instance database or lock file')
            os.fchown(handle, who.pw_uid, who.pw_gid)
            acl(handle, f'u:{OWNER}:rw,u:{USER}:rw')
            os.close(handle)
    os.close(deployfd)
    os.close(fd)


if __name__ == '__main__':
    os.chdir('/')
    step = sys.argv[1] if len(sys.argv) > 1 else 'pre'
    if step == 'pre':
        pre()
    elif step == 'post':
        post()
    data_access()

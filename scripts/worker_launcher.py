#!/usr/bin/env python3
"""Fixed worker launcher, run as the unprivileged worker user through sudo. Standard library only.

scripts/install_worker_user.py installs a root-owned copy at /usr/local/lib/linko/worker-launcher.py; the sudoers
drop-in lets the service user run only that copy as the worker user. See README.md ("How work runs").

  run --tag T [--cwd DIR] PROGRAM -- ARGS   exec the configured codex or claude binary with a clean environment
  kill --tag T --signal N                   signal every worker-user process started with that run tag
  probe [--read PATH]... [--write DIR]...   report which paths this user can read or write (isolation check)
"""
import json
import os
import pwd
import re
import sys
import tempfile
from pathlib import Path

CONFIG = Path('/etc/linko/worker.json')
TAG = re.compile(r'[A-Za-z0-9_.-]{1,80}')
VARIABLE = 'LINKO_WORKER_TAG'
KEEP = ('LANG', 'LC_ALL', 'LC_CTYPE', 'TERM', 'TZ')
DEFAULT_PATH = '/usr/local/bin:/usr/bin:/bin'


def fail(message):
    print('worker-launcher: ' + message, file=sys.stderr)
    return 2


def options(argv, *flags):
    """Parse `--flag value` pairs; repeated flags collect lists. Anything else is an error."""
    found, items = {}, list(argv)
    while items:
        flag = items.pop(0)
        if flag not in flags or not items:
            raise ValueError('unexpected argument ' + flag)
        found.setdefault(flag, []).append(items.pop(0))
    return found


def environment(config, tag, user=None):
    """No inherited secrets: only locale, the worker's own home and the installed PATH, plus the run tag."""
    user = user or pwd.getpwuid(os.getuid())
    env = {key: os.environ[key] for key in KEEP if key in os.environ}
    env.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name, PATH=config.get('path') or DEFAULT_PATH)
    env[VARIABLE] = tag
    return env


def run(config, argv):
    if '--' not in argv:
        raise ValueError('run needs PROGRAM -- ARGS')
    split = argv.index('--')
    head, args = argv[:split], argv[split + 1:]
    if not head:
        raise ValueError('run needs PROGRAM')
    program = head.pop()
    found = options(head, '--tag', '--cwd')
    tag = found.get('--tag', [''])[-1]
    if not TAG.fullmatch(tag):
        raise ValueError('invalid tag')
    binary = (config.get('programs') or {}).get(program)
    if not binary:
        raise ValueError('program is not configured: ' + program)
    user = pwd.getpwuid(os.getuid())
    os.chdir(found.get('--cwd', [user.pw_dir])[-1])
    os.execve(binary, [binary, *args], environment(config, tag, user))


def tagged(tag, proc=Path('/proc'), uid=None):
    """PIDs of this user's processes whose environment carries the run tag (descendants inherit it)."""
    uid = os.getuid() if uid is None else uid
    marker = f'{VARIABLE}={tag}'.encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != uid or marker not in (entry / 'environ').read_bytes().split(b'\0'):
                continue
        except OSError:
            continue
        yield int(entry.name)


def kill(argv):
    found = options(argv, '--tag', '--signal')
    tag, number = found.get('--tag', [''])[-1], found.get('--signal', ['15'])[-1]
    if not TAG.fullmatch(tag) or not number.isdigit():
        raise ValueError('kill needs --tag and a numeric --signal')
    count = 0
    for pid in tagged(tag):
        try:
            os.kill(pid, int(number))
            count += 1
        except OSError:
            pass
    print(json.dumps({'signalled': count}))


def readable(path):
    path = Path(path)
    try:
        if path.is_dir():
            next(iter(os.scandir(path)), None)
        else:
            with path.open('rb') as stream:
                stream.read(1)
        return True
    except OSError:
        return False


def writable(path):
    try:
        fd, name = tempfile.mkstemp(dir=path, prefix='.linko-probe-')
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def probe(argv):
    found = options(argv, '--read', '--write')
    print(json.dumps({'user': pwd.getpwuid(os.getuid()).pw_name,
                      'read': {p: readable(p) for p in found.get('--read', [])},
                      'write': {p: writable(p) for p in found.get('--write', [])}}))


def main(argv=None, config_path=CONFIG):
    argv = sys.argv[1:] if argv is None else argv
    if os.getuid() == 0:
        return fail('refusing to run as root')
    if not argv or argv[0] not in ('run', 'kill', 'probe'):
        return fail('usage: run|kill|probe ...')
    try:
        if argv[0] == 'run':
            run(json.loads(Path(config_path).read_text()), argv[1:])
        elif argv[0] == 'kill':
            kill(argv[1:])
        else:
            probe(argv[1:])
    except (OSError, ValueError) as error:
        return fail(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main())

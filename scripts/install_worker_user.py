#!/usr/bin/env python3
"""Run once as root on each machine: queue workers become a separate unprivileged Linux user; only the host repair
agent can reach root. Standard library only. See README.md ("How work runs", "Emergency host repair").

  install    create the worker user (no sudo, no privileged groups), root-owned copies of scripts/worker_launcher.py
             and scripts/emergency_repair.py, /etc/linko/worker.json, the sudoers drop-in (checked with visudo -cf)
             and the directory ACLs. Idempotent; re-run after updating either script. --dry-run prints the plan only.
  status     read-only JSON report, including a probe of what the worker user can read and write.
  uninstall  remove the sudoers drop-in, config, launcher copies and the worker's ACL entries, and give files the worker
             created back to the service user: the application returns to same-user mode. --remove-user also deletes
             the account and its provider logins.

After install, re-run `python3 scripts/install_repair.py install` as the service user so the repair timer starts the
root-owned launcher through sudo.
"""
import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUDO, PYTHON = '/usr/bin/sudo', '/usr/bin/python3'
NAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')
SAFE_PATH = re.compile(r'/[A-Za-z0-9/._+-]*')  # nothing sudoers or systemd would interpret
PRIVILEGED_GROUPS = ('root', 'sudo', 'admin', 'wheel', 'adm', 'disk', 'libvirt', 'kvm', 'docker', 'lxd', 'incus')
# Never opened to the worker, even when a toolchain directory would contain them.
SECRET_NAMES = ('.ssh', '.config', '.codex', '.claude', '.claude.json', '.gnupg', '.netrc', '.git-credentials',
                '.aws', '.docker', '.kube')


@dataclass(frozen=True)
class Layout:
    """Where the root-owned pieces live; tests use a temporary prefix."""
    lib: Path = Path('/usr/local/lib/linko')
    etc: Path = Path('/etc/linko')
    sudoers_dir: Path = Path('/etc/sudoers.d')

    @property
    def worker_launcher(self):
        return self.lib / 'worker-launcher.py'

    @property
    def repair_launcher(self):
        return self.lib / 'repair-launcher.py'

    @property
    def config(self):
        return self.etc / 'worker.json'

    @property
    def sudoers(self):
        return self.sudoers_dir / 'linko-worker'


@dataclass
class Options:
    service: str
    home: Path
    data: Path
    worker: str = 'linko-worker'
    worker_home: Path = Path('/var/lib/linko-worker')
    projects: list = field(default_factory=list)
    programs: dict = field(default_factory=dict)  # codex/claude executable for the worker
    path: str = '/usr/local/bin:/usr/bin:/bin'
    toolchain: list = field(default_factory=list)  # directories the worker may read and execute
    git_name: str = ''
    git_email: str = ''
    repo: Path = REPO


@dataclass
class Step:
    kind: str  # run | write | sudoers | remove
    detail: dict

    def describe(self):
        d = self.detail
        if self.kind == 'run':
            return 'run: ' + ' '.join(d['argv'])
        if self.kind == 'remove':
            return 'remove: ' + str(d['path'])
        return f"{self.kind}: {d['path']} ({d['owner']}, {d['mode']:04o})"


# ---- inspecting the host (read-only) ------------------------------------------------------------------------------
def env_file(path):
    """KEY=VALUE assignments of a systemd environment file; never executed."""
    values = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.strip().partition('=')
        if sep and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) and not key.startswith('#'):
            values[key] = value.strip().strip('"\'')
    return values


def user(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def groups_of(name):
    entry = user(name)
    if not entry:
        return []
    return sorted({g.gr_name for g in grp.getgrall() if name in g.gr_mem} | {grp.getgrgid(entry.pw_gid).gr_name})


def git_identity(home):
    values = []
    for key in ('user.name', 'user.email'):
        try:
            result = subprocess.run(['git', 'config', '--file', str(home / '.gitconfig'), key], capture_output=True,
                                    text=True, timeout=10)
            values.append(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            values.append('')
    return values


def within(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def toolchain_root(binary, options):
    """The directory to open for one executable: its install prefix when that is safe, else just the file."""
    real = Path(os.path.realpath(binary))
    if not within(real, options.home):
        return None  # system locations are already readable
    root = real.parent.parent if real.parent.name == 'bin' else real.parent
    if root.parent.name in ('releases', 'versions'):
        root = root.parent  # self-updating CLIs add new versions beside it; default ACLs keep them readable
    protected = [options.home, options.data, *options.projects, *(options.home / n for n in SECRET_NAMES)]
    if any(within(p, root) for p in protected):
        return real  # e.g. ~/.local/bin/uv: opening ~/.local would expose the data directory
    return root


def defaults(service, data=None, worker='linko-worker', projects=None, codex=None, claude=None, toolchain=()):
    entry = user(service)
    if not entry:
        raise ValueError(f'service user {service} does not exist')
    home = Path(entry.pw_dir)
    env = env_file(home / '.config/linko-bot-manager.env')
    service_path = env.get('PATH') or f'{home}/.local/bin:/usr/local/bin:/usr/bin:/bin'
    data = Path(data or env.get('AI_MANAGER_DATA') or home / '.local/share/linko-bot-manager').expanduser()
    projects = [Path(p).expanduser() for p in (projects or env.get('AI_MANAGER_PROJECT_ROOTS', str(home / 'projects'))
                                               .split(os.pathsep))]
    found = {'codex': codex or env.get('AI_MANAGER_CODEX') or shutil.which('codex', path=service_path) or 'codex',
             'claude': claude or env.get('AI_MANAGER_CLAUDE') or shutil.which('claude', path=service_path)
             or str(home / '.local/bin/claude')}
    found = {k: v if os.path.isabs(v) else (shutil.which(v, path=service_path) or v) for k, v in found.items()}
    options = Options(service=service, home=home, data=data, worker=worker, projects=projects, programs=found)
    helpers = [shutil.which(name, path=service_path) for name in ('node', 'uv', 'git')]
    executables = [p for p in [*found.values(), *helpers] if p and os.path.isabs(p)]
    bins = [str(Path(p).parent) for p in executables if within(Path(p).parent, home)]
    options.path = ':'.join(dict.fromkeys([str(options.worker_home / '.local/bin'), *bins,
                                           '/usr/local/bin', '/usr/bin', '/bin']))
    roots = [toolchain_root(p, options) for p in executables]
    roots += [home / '.local/share/uv/python', options.repo, *map(Path, toolchain)]
    options.toolchain = list(dict.fromkeys(p for p in roots if p and Path(p).exists()))
    options.git_name, options.git_email = git_identity(home)
    return options


def validate(options):
    for name in (options.service, options.worker):
        if not NAME.fullmatch(name):
            raise ValueError(f'invalid user name: {name}')
    if options.service == options.worker:
        raise ValueError('the worker user must differ from the service user')
    for path in (options.data, options.home, options.worker_home, *options.programs.values()):
        if not SAFE_PATH.fullmatch(str(path)):
            raise ValueError(f'use a plain absolute path without spaces or special characters: {path}')
    if user(options.worker) and (bad := set(groups_of(options.worker)) & set(PRIVILEGED_GROUPS)):
        raise ValueError(f'{options.worker} is in privileged groups {sorted(bad)}; remove it from them first')
    if user(options.worker) and Path(user(options.worker).pw_dir) != options.worker_home:
        options.worker_home = Path(user(options.worker).pw_dir)


# ---- the plan -----------------------------------------------------------------------------------------------------
def sudoers_text(options, layout):
    worker = f'{PYTHON} -I {layout.worker_launcher}'
    return f"""# Managed by scripts/install_worker_user.py (Linko Bot Manager). Re-run it instead of editing this file.
# {options.service} may run only the fixed worker launcher as {options.worker} (never a shell), and only the
# root-owned repair launcher as root. Neither user needs any other sudo right.
Cmnd_Alias LINKO_WORKER = {worker} run *, {worker} kill *, {worker} probe *
Cmnd_Alias LINKO_REPAIR = {PYTHON} -I {layout.repair_launcher} --data {options.data} tick
Defaults!LINKO_WORKER env_reset
Defaults!LINKO_REPAIR env_reset
{options.service} ALL=({options.worker}) NOPASSWD: LINKO_WORKER
{options.service} ALL=(root) NOPASSWD: LINKO_REPAIR
"""


def config_text(options, layout):
    return json.dumps({'service_user': options.service, 'worker_user': options.worker,
                       'launcher': str(layout.worker_launcher), 'repair_launcher': str(layout.repair_launcher),
                       'data': str(options.data), 'python': PYTHON, 'programs': options.programs,
                       'path': options.path, 'toolchain': [str(p) for p in options.toolchain]}, indent=2) + '\n'


def gitconfig_text(options):
    # Workspaces belong to the service user; Git would otherwise refuse them as "dubious ownership".
    text = '# Managed by scripts/install_worker_user.py\n[safe]\n\tdirectory = *\n'
    if options.git_name and options.git_email:
        text += f'[user]\n\tname = {options.git_name}\n\temail = {options.git_email}\n'
    return text


def write(path, content, mode, owner='root:root'):
    return Step('write', {'path': Path(path), 'content': content, 'mode': mode, 'owner': owner})


def setfacl(*args):
    return Step('run', {'argv': ['setfacl', '-P', *map(str, args)]})


def traversal(options):
    """Directories the worker passes through (search only) and the paths below them it may reach."""
    workspaces = options.data / 'assignment-workspaces'
    targets = [workspaces, *options.projects, *options.toolchain]
    chain = {}
    for target in targets:
        if not within(target, options.home):
            continue
        for parent in [*reversed(Path(target).parents), Path(target)]:
            if within(parent, options.home) and (parent != Path(target) or target in (workspaces, *options.projects)):
                chain.setdefault(parent, None)
    return list(chain), targets


def acl_steps(options):
    """Search access on the way to workspaces, project roots and toolchain; an explicit no-access entry for every
    other child of those directories (it overrides 'other' permissions); read access to the toolchain. Workspaces and
    project checkouts start closed: the application opens each one only while its task runs."""
    worker = options.worker
    chain, targets = traversal(options)
    allowed = [*chain, *targets]
    steps = [setfacl('-m', f'u:{worker}:--x', d) for d in chain]
    for directory in chain:
        try:
            children = sorted(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink() or any(within(a, child) for a in allowed):
                continue
            steps.append(setfacl('-m', f'u:{worker}:---', child))
    for root in options.toolchain:
        if Path(root).is_dir():
            steps.append(setfacl('-R', '-m', f'u:{worker}:rX,d:u:{worker}:rX', root))
        else:
            steps.append(setfacl('-m', f'u:{worker}:r-x', root))
    # The repair spool and everything else in the data directory stay closed, whatever was listed above.
    steps.append(setfacl('-m', f'u:{worker}:---', options.data / 'repair'))
    return steps


def install_plan(options, layout=Layout()):
    validate(options)
    steps = []
    if not user(options.worker):
        steps.append(Step('run', {'argv': ['useradd', '--system', '--user-group', '--create-home', '--home-dir',
                                           str(options.worker_home), '--shell', '/usr/sbin/nologin',
                                           '--comment', 'Linko queue worker', options.worker]}))
    steps.append(Step('run', {'argv': ['install', '-d', '-o', 'root', '-g', 'root', '-m', '0755',
                                       str(layout.lib), str(layout.etc)]}))
    steps.append(write(layout.worker_launcher, (options.repo / 'scripts/worker_launcher.py').read_text(), 0o755))
    steps.append(write(layout.repair_launcher, (options.repo / 'scripts/emergency_repair.py').read_text(), 0o755))
    steps.append(write(layout.config, config_text(options, layout), 0o644))
    steps.append(write(options.worker_home / '.gitconfig', gitconfig_text(options), 0o644,
                       f'{options.worker}:{options.worker}'))
    steps.append(Step('sudoers', {'path': layout.sudoers, 'content': sudoers_text(options, layout), 'mode': 0o440,
                                  'owner': 'root:root'}))
    steps += acl_steps(options)
    return steps


def uninstall_plan(options, layout=Layout(), remove_user=False):
    worker = options.worker
    steps = [Step('remove', {'path': p}) for p in (layout.sudoers, layout.config, layout.worker_launcher,
                                                   layout.repair_launcher)]
    chain, _ = traversal(options)
    for directory in chain:
        steps.append(Step('run', {'argv': ['setfacl', '-P', '-x', f'u:{worker}', str(directory)], 'check': False}))
        try:
            children = sorted(directory.iterdir())
        except OSError:
            children = []
        steps += [Step('run', {'argv': ['setfacl', '-P', '-x', f'u:{worker}', str(c)], 'check': False})
                  for c in children if not c.is_symlink()]
    for root in [*options.toolchain, options.data / 'assignment-workspaces', options.data / 'chat-workspace',
                 *options.projects]:
        steps.append(Step('run', {'argv': ['setfacl', '-P', '-R', '-x', f'u:{worker}', '-x', f'd:u:{worker}',
                                           str(root)], 'check': False}))
    if user(worker):
        steps.append(Step('run', {'argv': ['find', str(options.data), *map(str, options.projects), '-xdev', '-user',
                                           worker, '-exec', 'chown', '-h', f'{options.service}:', '{}', '+'],
                                  'check': False}))
        if remove_user:
            steps.append(Step('run', {'argv': ['userdel', '--remove', worker], 'check': False}))
    return steps


def repair_command(data, layout=Layout()):
    """The repair timer's ExecStart when the root-owned launcher is installed for this data directory, else None.
    It must match the sudoers rule exactly."""
    try:
        config = json.loads(layout.config.read_text())
    except (OSError, ValueError):
        return None
    if config.get('data') != str(data) or not root_owned(layout.repair_launcher):
        return None
    return [SUDO, '-n', PYTHON, '-I', str(layout.repair_launcher), '--data', str(data), 'tick']


# ---- applying -----------------------------------------------------------------------------------------------------
def chown(path, owner):
    if os.geteuid() != 0:
        return  # dry runs and tests: the plan records the owner
    name, _, group = owner.partition(':')
    shutil.chown(path, name, group or None)


def atomic_write(path, content, mode, owner):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.linko-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.chmod(name, mode)
        chown(name, owner)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def check_sudoers(path, run=subprocess.run):
    """visudo -cf when available; returns (ok, message). Missing visudo is reported, not treated as valid."""
    visudo = shutil.which('visudo') or next((p for p in ('/usr/sbin/visudo', '/sbin/visudo') if os.path.exists(p)), None)
    if not visudo:
        return None, 'visudo not found'
    result = run([visudo, '-cf', str(path)], capture_output=True, text=True, timeout=30)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def apply(steps, run=subprocess.run, output=print):
    for step in steps:
        d = step.detail
        if step.kind == 'run':
            result = run(d['argv'], capture_output=True, text=True, timeout=600)
            if result.returncode and d.get('check', True):
                raise RuntimeError(f"{' '.join(d['argv'])} failed: {(result.stderr or '').strip()[:400]}")
        elif step.kind == 'remove':
            Path(d['path']).unlink(missing_ok=True)
        elif step.kind == 'write':
            path = Path(d['path'])
            if path.is_file() and path.read_text() == d['content'] and (path.stat().st_mode & 0o7777) == d['mode']:
                chown(path, d['owner'])
                continue
            atomic_write(path, d['content'], d['mode'], d['owner'])
        elif step.kind == 'sudoers':
            path = Path(d['path'])
            # Validate a candidate first; a broken drop-in would lock sudo for everyone.
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, candidate = tempfile.mkstemp(dir=path.parent, prefix='.linko-')
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(d['content'])
                os.chmod(candidate, d['mode'])
                chown(candidate, d['owner'])
                ok, message = check_sudoers(candidate, run)
                if ok is False:
                    raise RuntimeError('sudoers check failed: ' + message)
                if ok is None and os.geteuid() == 0:
                    raise RuntimeError('visudo is required to install the sudoers drop-in')
                os.replace(candidate, path)
            finally:
                if os.path.exists(candidate):
                    os.unlink(candidate)
        output('done ' + step.describe())


# ---- status -------------------------------------------------------------------------------------------------------
def digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def root_owned(path):
    try:
        info = os.stat(path)
    except OSError:
        return False
    return info.st_uid == 0 and not info.st_mode & 0o022


def status(options, layout=Layout(), run=subprocess.run):
    worker = user(options.worker)
    report = {
        'worker_user': options.worker, 'worker_exists': bool(worker),
        'worker_privileged_groups': sorted(set(groups_of(options.worker)) & set(PRIVILEGED_GROUPS)),
        'config': layout.config.is_file(), 'sudoers': layout.sudoers.is_file(),
        'worker_launcher_root_owned': root_owned(layout.worker_launcher),
        'repair_launcher_root_owned': root_owned(layout.repair_launcher),
        'worker_launcher_current': digest(layout.worker_launcher) == digest(options.repo / 'scripts/worker_launcher.py'),
        'repair_launcher_current': digest(layout.repair_launcher) == digest(options.repo / 'scripts/emergency_repair.py'),
    }
    if os.geteuid() == 0:
        listing = run(['sudo', '-l', '-U', options.service], capture_output=True, text=True, timeout=30).stdout
        report['service_blanket_nopasswd'] = bool(re.search(
            r'^\s*\(\s*(ALL|root)\s*(:\s*\w+\s*)?\)\s*NOPASSWD:\s*ALL\s*$', listing, re.M))
        if report['sudoers']:
            report['sudoers_valid'] = check_sudoers(layout.sudoers, run)[0]
        if worker and report['worker_launcher_root_owned']:
            report['probe'] = probe(options, layout, run)
    return report


def probe(options, layout=Layout(), run=subprocess.run):
    """What the worker user can actually reach; every value should be False."""
    argv = ['sudo', '-n', '-u', options.worker, PYTHON, '-I', str(layout.worker_launcher), 'probe',
            '--read', str(options.home / '.config/linko-bot-manager.env'), '--read', str(options.home / '.ssh'),
            '--read', str(options.data / 'manager.sqlite3'), '--write', str(options.data / 'repair')]
    try:
        result = run(argv, capture_output=True, text=True, timeout=60)
        found = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'error': 'probe failed'}
    return {**found['read'], **{'write ' + k: v for k, v in found['write'].items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['install', 'status', 'uninstall'])
    parser.add_argument('--service-user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name,
                        help='the user the application runs as (default: the user who ran sudo)')
    parser.add_argument('--worker-user', default='linko-worker')
    parser.add_argument('--data', help='application data directory (default: from the service env file)')
    parser.add_argument('--projects', help='project roots separated by ":" (default: from the service env file)')
    parser.add_argument('--codex', help='Codex executable the worker runs (default: the service user\'s)')
    parser.add_argument('--claude', help='Claude Code executable the worker runs (default: the service user\'s)')
    parser.add_argument('--toolchain', action='append', default=[], help='extra directory the worker may read')
    parser.add_argument('--dry-run', action='store_true', help='print the plan; change nothing')
    parser.add_argument('--remove-user', action='store_true', help='uninstall: also delete the worker account')
    args = parser.parse_args(argv)
    options = defaults(args.service_user, args.data, args.worker_user, args.projects and args.projects.split(':'),
                       args.codex, args.claude, args.toolchain)
    if args.command == 'status':
        print(json.dumps(status(options), indent=2))
        return 0
    if os.geteuid() != 0 and not args.dry_run:
        parser.error(f'{args.command} needs root (sudo); use --dry-run to see the plan')
    steps = install_plan(options) if args.command == 'install' else uninstall_plan(options, remove_user=args.remove_user)
    if args.dry_run:
        for step in steps:
            print(step.describe())
        return 0
    apply(steps)
    if args.command == 'install':
        print(f'Workers now run as {options.worker}. Next, as {options.service}: python3 scripts/install_repair.py '
              f'install; log in the worker providers (README "How work runs"); then remove any blanket NOPASSWD rule.')
    else:
        print(f'Workers run as {options.service} again (same-user mode). As {options.service}, re-run '
              'python3 scripts/install_repair.py install so the repair timer no longer uses sudo.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

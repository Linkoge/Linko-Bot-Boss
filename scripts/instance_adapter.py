#!/usr/bin/env python3
"""Instances as other Linux users: one whole-checkout code view per instance. Standard library only.

An instance that runs as another user (linko-bot-manager@NAME.service, e.g. kaxa) sees this checkout through its
root-owned host adapter /usr/local/libexec/linko-NAME-instance. The old adapters bind a per-path list, so every new
top-level runtime path broke them. The layout written here binds the WHOLE checkout read-only (secrets and the real
.git masked, template deploy/instance-adapter.py) and gives read access through the shared group linko-code:
setgid directories with the default ACL g:linko-code:rX, so files git or a deploy rewrites inherit it.

  plan NAME     print what apply would change for instance NAME; writes nothing, needs no root
  apply NAME    (root) migrate or re-apply instance NAME: group, membership, checkout ACLs, adapter (the old one is
                kept as linko-NAME-instance.legacy), unit drop-in, daemon-reload; restarts a running instance and
                restores the old adapter when it does not come back healthy
  add NAME      (root) the same layout for a new profile (existing Linux user; --port; writes the unit if missing)
  check         (no root) verify every registered instance's code view, as the deploy does before a restart;
                --repair re-grants g:linko-code:rX on tracked files this user owns that lost it

Idempotent: a second apply changes nothing and restarts nothing.
"""
import argparse
import grp
import json
import os
import pwd
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / 'deploy' / 'instance-adapter.py'
sys.path.insert(0, str(REPO))
from ai_manager import code_view  # noqa: E402

NAME = re.compile(r'[a-z][a-z0-9-]{0,30}')
SAFE_PATH = re.compile(r'/[A-Za-z0-9/._+-]*')  # nothing systemd or a shell would interpret
BEGIN, END = '# --- settings (written by scripts/instance_adapter.py) ---\n', '# --- end settings ---\n'
PYTHON_TARGET, VENV_TARGET, CODEX_TARGET = '/opt/linko-runtime/python', '/opt/linko-runtime/venv', '/opt/linko-codex/bin'
BATCH = 400  # paths per chgrp/chmod command
SYSTEM = Path('/')  # the real prefix: apply/add there need root


@dataclass(frozen=True)
class Layout:
    """Where the root-owned pieces live; tests use a temporary prefix."""
    root: Path = Path('/')

    def adapter(self, name):
        return self.root / 'usr/local/libexec' / f'linko-{name}-instance'

    def unit(self, name):
        return self.root / 'etc/systemd/system' / f'linko-bot-manager@{name}.service'

    def dropin(self, name):
        return self.root / 'etc/systemd/system' / f'linko-bot-manager@{name}.service.d' / '50-linko-code.conf'


@dataclass
class Config:
    name: str
    user: str
    owner: str
    checkout: str
    code: str
    state: str
    data: str
    port: int
    runtime: list = field(default_factory=list)  # [(source, target)] outside the code view
    group: str = code_view.GROUP

    def settings(self):
        return {'LAYOUT': code_view.WHOLE, 'NAME': self.name, 'USER': self.user, 'OWNER': self.owner,
                'GROUP': self.group, 'CHECKOUT': self.checkout, 'CODE': self.code, 'STATE': self.state,
                'DATA': self.data, 'PORT': self.port, 'MASK': list(code_view.MASKED),
                'mounts': [(self.checkout, self.code)] + [tuple(m) for m in self.runtime]}


@dataclass
class Step:
    text: str
    argv: list = None  # a command
    path: Path = None  # or a file to write
    content: str = None
    mode: int = 0o644
    changes: bool = True  # False: idempotent upkeep (ACLs); does not by itself need a restart


class Failed(SystemExit):
    pass


def within(path, parent):
    path, parent = Path(path), Path(parent)
    return path == parent or parent in path.parents


def owner_of(path):
    return pwd.getpwuid(os.stat(path).st_uid).pw_name


def registry(data):
    try:
        return {i.get('name'): i for i in json.loads((Path(data) / 'deploy' / 'installed.json').read_text())
                .get('instances', []) if isinstance(i, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def main_data(checkout, data=None):
    """The main instance's data directory: --data, else the checkout owner's default (sudo keeps root's HOME)."""
    if data:
        return Path(data)
    return Path(pwd.getpwnam(owner_of(checkout)).pw_dir) / '.local/share/linko-bot-manager'


def legacy_config(path, name, entry, checkout):
    """The settings of an old per-path adapter (its mounts list and the literals in its code)."""
    text = Path(path).read_text()
    mounts = code_view.adapter_mounts(path) or []
    code_mounts = [(s, t) for s, t in mounts if Path(s).parent == Path(checkout) and Path(t).parent != Path('/')]
    code = str(Path(code_mounts[0][1]).parent) if code_mounts else f'/opt/linko-{name}/code'
    runtime = [(s, t) for s, t in mounts if (s, t) not in code_mounts]
    user = re.search(r"getpwnam\(['\"]([^'\"]+)", text)
    owner = re.search(r"runuser['\"],\s*['\"]-u['\"],\s*['\"]([^'\"]+)", text)
    state = re.search(r"['\"]--bind['\"],\s*['\"]([^'\"]+)['\"],\s*['\"]" + re.escape(code) + "['\"]", text)
    port = re.search(r'127\.0\.0\.1:(\d+)', text) or re.search(r':(\d+)', entry.get('url', ''))
    components = re.search(r'for component in (\[[^\]]*\])', text)
    data = '/' + '/'.join(json.loads(components.group(1).replace("'", '"'))) if components else entry.get('data')
    return Config(name=name, user=user.group(1) if user else name, owner=owner.group(1) if owner else owner_of(checkout),
                  checkout=str(checkout), code=code, state=state.group(1) if state else f'/var/lib/linko-{name}',
                  data=data or '', port=int(port.group(1)) if port else 0, runtime=runtime)


def whole_config(settings):
    return Config(name=settings['NAME'], user=settings['USER'], owner=settings['OWNER'], checkout=settings['CHECKOUT'],
                  code=settings['CODE'], state=settings['STATE'], data=settings['DATA'], port=int(settings['PORT']),
                  runtime=[tuple(m) for m in settings['mounts'][1:]], group=settings.get('GROUP', code_view.GROUP))


def venv_runtime(checkout, codex=None):
    """Interpreter, venv packages and provider CLI of this checkout, for a new profile without a template."""
    runtime = []
    try:
        cfg = {key.strip(): value for key, value in (line.split('=', 1) for line in
               (Path(checkout) / '.venv/pyvenv.cfg').read_text().splitlines() if '=' in line)}
        runtime.append((str(Path(cfg['home'].strip()).parent), PYTHON_TARGET))
    except (OSError, KeyError, ValueError):
        pass
    for site in sorted((Path(checkout) / '.venv/lib').glob('python3*/site-packages')):
        runtime.append((str(site), VENV_TARGET + str(site).split('/.venv', 1)[1]))
        break
    if codex:
        runtime.append((str(Path(codex)), CODEX_TARGET))
    return runtime


def resolve(command, name, layout, args):
    """(config, kind of the current adapter: 'whole', 'legacy' or None, notes)."""
    if not NAME.fullmatch(name):
        raise Failed(f'instance name {name!r}: lowercase letters, digits and -')
    checkout = Path(args.checkout or REPO).resolve()
    entry = registry(main_data(checkout, args.data)).get(name, {})
    adapter = layout.adapter(name)
    notes = []
    if command == 'add':
        if adapter.exists():
            raise Failed(f'{adapter} exists: use apply {name}')
        user = args.user or name
        try:
            home = pwd.getpwnam(user).pw_dir
        except KeyError:
            raise Failed(f'Linux user {user} does not exist; create it first (docs/ONBOARDING.md)') from None
        port = args.port or int((re.search(r':(\d+)', entry.get('url', '')) or [0, 0])[1])
        if not port:
            raise Failed(f'no port for {name}: pass --port (the AI_MANAGER_PORT of its env file)')
        if args.like:
            like = code_view.adapter_settings(layout.adapter(args.like)) or {}
            other = (whole_config(like) if like.get('LAYOUT') == code_view.WHOLE else
                     legacy_config(layout.adapter(args.like), args.like, {}, checkout))
            runtime = other.runtime
        else:
            runtime = venv_runtime(checkout, args.codex)
        config = Config(name=name, user=user, owner=owner_of(checkout), checkout=str(checkout),
                        code=f'/opt/linko-{name}/code', state=f'/var/lib/linko-{name}',
                        data=entry.get('data') or f'{home}/.local/share/linko-bot-manager-{name}', port=port,
                        runtime=runtime)
        return config, None, notes
    if not adapter.exists():
        raise Failed(f'{adapter} does not exist: for a new profile use add {name}')
    settings = code_view.adapter_settings(adapter)
    if settings is None:
        raise Failed(f'cannot read {adapter} (run plan/apply as root)')
    if settings.get('LAYOUT') == code_view.WHOLE:
        config, kind = whole_config(settings), 'whole'
    else:
        config, kind = legacy_config(adapter, name, entry, checkout), 'legacy'
    if Path(config.checkout).resolve() != checkout:
        notes.append(f'the adapter mounted {config.checkout}; it will mount {checkout}')
        config.checkout = str(checkout)
    if args.user:
        config.user = args.user
    if args.port:
        config.port = args.port
    if not config.port or not config.data:
        raise Failed(f'cannot tell the port/data directory of {name}: pass --port (and register it in the deploy)')
    return config, kind, notes


def validate(config):
    for value in [config.checkout, config.code, config.state, config.data] + [p for m in config.runtime for p in m]:
        if not SAFE_PATH.fullmatch(str(value)):
            raise Failed(f'unsupported path {value!r}')
    for value in (config.user, config.owner, config.group):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', value):
            raise Failed(f'unsupported user or group name {value!r}')
    if within(config.code, config.checkout) or within(config.checkout, config.code):
        raise Failed(f'code view {config.code} must lie outside the checkout {config.checkout}')


def render(config):
    """The adapter: deploy/instance-adapter.py with this instance's settings."""
    template = TEMPLATE.read_text()
    head, rest = template.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    lines = ''.join(f'{key} = {value!r}\n' for key, value in config.settings().items())
    return head + BEGIN + lines + END + tail


def dropin_text(config):
    return ('# Written by scripts/instance_adapter.py: read the shared checkout through the code group.\n'
            f'[Service]\nSupplementaryGroups={config.group}\n')


def unit_text(config, layout):
    """A system unit for a new profile (add): the instance runs as its own user from the code view."""
    adapter = '/' + str(layout.adapter(config.name).relative_to(layout.root))
    python = next((t for s, t in config.runtime if t == PYTHON_TARGET or Path(s, 'bin/python3').exists()), None)
    site = next((t for _, t in config.runtime if t.endswith('site-packages')), None)
    codex = next((t for _, t in config.runtime if 'codex' in t), None)
    if not python:
        raise Failed('no Python runtime to mount: create the checkout venv (uv sync) or pass --like OTHER')
    try:
        home = pwd.getpwnam(config.user).pw_dir
    except KeyError:
        home = f'/home/{config.user}'
    lines = ['[Unit]', f'Description=Linko Bot Boss instance {config.name} (user {config.user}, whole-checkout code '
             'view; written by scripts/instance_adapter.py add)', 'After=network-online.target',
             'Wants=network-online.target', '', '[Service]', 'Type=simple', f'User={config.user}',
             f'Group={config.user}', f'SupplementaryGroups={config.group}', f'WorkingDirectory={config.code}',
             f'ExecStartPre=+{adapter} pre', f'ExecStart={python}/bin/python3 -m ai_manager.cli serve',
             f'ExecStartPost=+{adapter} post', 'Environment=PYTHONUNBUFFERED=1']
    if site:
        lines.append(f'Environment=PYTHONPATH={site}')
    if codex:
        lines.append(f'Environment=AI_MANAGER_CODEX={codex}/codex')
    lines += ['Environment=AI_MANAGER_SECURE_COOKIE=1', f'EnvironmentFile={home}/.config/linko-bot-manager-{config.name}.env',
              'UMask=0077', 'NoNewPrivileges=yes', 'Restart=on-failure', 'RestartSec=5', 'TimeoutStopSec=30',
              'KillMode=control-group', '', '[Install]', 'WantedBy=multi-user.target', '']
    return '\n'.join(lines)


def shared_dirs(checkout):
    """The checkout root and every directory below its non-masked entries (not following symlinks)."""
    dirs = [checkout]
    for name in sorted(os.listdir(checkout)):
        top = os.path.join(checkout, name)
        if code_view.masked([name]) or os.path.islink(top) or not os.path.isdir(top):
            continue
        for current, children, _ in os.walk(top):
            dirs.append(current)
            children[:] = [c for c in children if not os.path.islink(os.path.join(current, c))]
    return dirs


def current(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


def steps(config, layout, kind):
    """Every change, in order. ACL steps always run (idempotent upkeep); the rest only when something differs."""
    plan = []
    try:
        group = grp.getgrnam(config.group)
    except KeyError:
        group = None
        plan.append(Step(f'create group {config.group}', ['groupadd', '--system', config.group]))
    if not group or not code_view.member(config.user, group):
        plan.append(Step(f'add user {config.user} to {config.group}', ['usermod', '-aG', config.group, config.user]))
    checkout = config.checkout
    entries = [os.path.join(checkout, n) for n in sorted(os.listdir(checkout))
               if not code_view.masked([n]) and not os.path.islink(os.path.join(checkout, n))]
    hidden = code_view.masked(os.listdir(checkout))
    dirs = shared_dirs(checkout)
    for i in range(0, len(dirs), BATCH):
        part = dirs[i:i + BATCH]
        plan.append(Step(f'checkout directories {i + 1}-{i + len(part)} of {len(dirs)}: group {config.group}',
                         ['chgrp', config.group, *part], changes=False))
        plan.append(Step(f'checkout directories {i + 1}-{i + len(part)} of {len(dirs)}: setgid (new files get group '
                         f'{config.group})', ['chmod', 'g+s', *part], changes=False))
    acl = f'g:{config.group}:rX,d:g:{config.group}:rX'
    plan.append(Step(f'checkout root: access + default ACL {acl}', ['setfacl', '-P', '-m', acl, checkout], changes=False))
    plan.append(Step(f'checkout entries ({len(entries)}, recursive; never {", ".join(hidden) or "-"}): ACL {acl}',
                     ['setfacl', '-P', '-R', '-m', acl, *entries], changes=False))
    for source, _ in config.runtime:
        if not within(source, checkout):
            plan.append(Step(f'runtime {source}: ACL {acl}', ['setfacl', '-P', '-R', '-m', acl, source], changes=False))
    adapter, text = layout.adapter(config.name), render(config)
    if kind == 'legacy' and not Path(str(adapter) + '.legacy').exists():
        plan.append(Step(f'keep the old adapter as {adapter}.legacy', ['cp', '-p', str(adapter), str(adapter) + '.legacy']))
    if current(adapter) != text:
        plan.append(Step(f'write {adapter} (whole checkout {checkout} -> {config.code} read-only; masks '
                         f'{", ".join(code_view.MASKED)} and .env files; runtime mounts {len(config.runtime)})',
                         path=adapter, content=text, mode=0o755))
    unit = layout.unit(config.name)
    if kind is None and not unit.exists():  # add only; an existing instance keeps its own unit (plus the drop-in)
        plan.append(Step(f'write {unit}', path=unit, content=unit_text(config, layout), mode=0o644))
    if current(layout.dropin(config.name)) != dropin_text(config):
        plan.append(Step(f'write {layout.dropin(config.name)} (SupplementaryGroups={config.group})',
                         path=layout.dropin(config.name), content=dropin_text(config), mode=0o644))
    if any(s.changes for s in plan):
        plan.append(Step('systemctl daemon-reload', ['systemctl', 'daemon-reload']))
    return plan


def write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def execute(plan, output):
    for step in plan:
        output(f'- {step.text}')
        if step.path is not None:
            write(step.path, step.content, step.mode)
        else:
            subprocess.run(step.argv, check=True)


def service(name):
    return f'linko-bot-manager@{name}.service'


def journal(unit, output, lines=30):
    """The unit's last journal lines, so a failed restart shows its cause without another command."""
    command = ['journalctl', '-u', unit, '-n', str(lines), '--no-pager', '-o', 'short-iso']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        text = (result.stdout or result.stderr).rstrip()
    except (OSError, subprocess.TimeoutExpired) as error:
        text = f'({" ".join(command)} failed: {error})'
    output(f'--- last {lines} journal lines of {unit} ---')
    for line in text.splitlines()[-lines:] or ['(empty)']:
        output(line)
    output('--- end of journal ---')


def restart(config, layout, kind, output, dropin_before=None):
    """Restart a running instance on the new adapter; when it fails, show the journal, put the old adapter (and the
    unit drop-in as it was before this apply) back and restart again."""
    unit = service(config.name)
    if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode:
        output(f'{unit} is not running: the new layout applies at its next start')
        return 0
    output(f'- systemctl restart {unit}')
    if not subprocess.run(['systemctl', 'restart', unit]).returncode:
        output(f'{unit} restarted healthy on the whole-checkout code view')
        return 0
    journal(unit, output)
    legacy = Path(str(layout.adapter(config.name)) + '.legacy')
    if kind != 'legacy' or not legacy.exists():
        output(f'{unit} failed to restart; see journalctl -u {unit}')
        return 1
    output(f'{unit} failed to restart: restoring {legacy}')
    subprocess.run(['umount', '-R', config.code])
    write(layout.adapter(config.name), legacy.read_text(), 0o755)
    dropin = layout.dropin(config.name)
    if current(dropin) != dropin_before:
        if dropin_before is None:
            output(f'- remove {dropin} (written by this apply; the old adapter does not need it)')
            dropin.unlink(missing_ok=True)
        else:
            output(f'- restore {dropin}')
            write(dropin, dropin_before, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if subprocess.run(['systemctl', 'restart', unit]).returncode:
        output(f'{unit} also failed on the restored adapter')
        journal(unit, output)
        return 1
    output(f'restored the old adapter ({unit} running); see journalctl -u {unit}')
    return 1


def describe(config, kind, layout, notes):
    old = {'legacy': 'per-path bind list (legacy)', 'whole': 'whole checkout (re-apply)', None: 'none (new profile)'}
    lines = [f'instance {config.name}: user {config.user}, owner {config.owner}, port {config.port}',
             f'  adapter {layout.adapter(config.name)}: {old[kind]}',
             f'  code view {config.code} = {config.checkout} read-only; instance data {config.data} (its only '
             'writable place)', f'  release HEAD from {config.state}/.git (masks the real .git)']
    lines += [f'  runtime {source} -> {target}' for source, target in config.runtime]
    lines += [f'  note: {note}' for note in notes]
    return lines


def check(args, output):
    """Non-root: what the deploy checks before restarting, for every registered instance."""
    checkout = Path(args.checkout or REPO).resolve()
    instances = registry(main_data(checkout, args.data))
    layout = Layout(Path(args.root))
    failed = False
    for name, entry in sorted(instances.items()):
        report = code_view.inspect(checkout, name, entry.get('adapter') or layout.adapter(name), repair=args.repair)
        if report.problems:
            failed = True
            output(f'FAIL instance {name}: ' + '; '.join(report.problems))
        elif report.layout is None:
            output(f'OK   instance {name}: no host adapter (same user)')
        else:
            output(f'OK   instance {name}: {report.layout}')
        for note in report.notes:
            output(f'     note: {note}')
    if not instances:
        output('no registered instances')
    return 1 if failed else 0


def main(argv=None, output=print):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'apply', 'add', 'check'])
    parser.add_argument('name', nargs='?')
    parser.add_argument('--root', default='/', help='prefix of /usr/local/libexec and /etc/systemd (tests)')
    parser.add_argument('--checkout', help=f'the shared checkout (default {REPO})')
    parser.add_argument('--data', help="the main instance's data directory (default: the checkout owner's)")
    parser.add_argument('--group', help=f'shared code group (default {code_view.GROUP})')
    parser.add_argument('--user', help='Linux user of the instance (default: NAME, or what the old adapter says)')
    parser.add_argument('--port', type=int, help='instance port (add; default from the deploy registry)')
    parser.add_argument('--like', help='add: copy the runtime mounts of this existing instance')
    parser.add_argument('--codex', help='add without --like: directory holding the codex binary to mount')
    parser.add_argument('--for', dest='target', choices=['apply', 'add'], help='plan: plan an add instead of apply')
    parser.add_argument('--repair', action='store_true', help='check: re-grant group read on owned tracked files')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            return check(args, output)
        if not args.name:
            parser.error(f'{args.command} needs NAME')
        layout = Layout(Path(args.root))
        command = args.target or ('add' if args.command == 'add' else 'apply')
        config, kind, notes = resolve(command, args.name, layout, args)
        config.group = args.group or config.group
        validate(config)
        plan = steps(config, layout, kind)
        for line in describe(config, kind, layout, notes):
            output(line)
        if args.command == 'plan':
            output('plan (nothing written):')
            output('\n'.join(f'- {s.text}' + (f'\n    {" ".join(s.argv[:6])}{" ..." if len(s.argv) > 6 else ""}'
                                              if s.argv else '') for s in plan))
            if any(s.changes for s in plan) and command == 'apply':
                output(f'- restart {service(config.name)} if it is running (old adapter restored if it fails)')
            output(f'apply with: sudo python3 {REPO}/scripts/instance_adapter.py {command} {args.name}')
            return 0
        if layout.root == SYSTEM and os.geteuid() != 0:
            raise Failed(f'{args.command} needs root: sudo python3 {REPO}/scripts/instance_adapter.py '
                         f'{args.command} {args.name}')
        dropin_before = current(layout.dropin(config.name))
        execute(plan, output)
        if args.command == 'add':
            output(f'next: env file, setup-owner, then systemctl enable --now {service(config.name)} and '
                   f'python3 scripts/install_deploy.py --instance {config.name} (docs/INSTALL.md section 6)')
            return 0
        if not any(s.changes for s in plan):
            output('nothing changed: no restart')
            return 0
        return restart(config, layout, kind, output, dropin_before)
    except subprocess.CalledProcessError as error:
        output(f'failed: {" ".join(map(str, error.cmd[:6]))} exited {error.returncode}')
        return 1
    except Failed as error:
        output(str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

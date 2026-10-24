#!/usr/bin/env python3
"""One-time install of the host helper behind the owner UI's "Add User" screen (docs/ONBOARDING.md). Standard library only.

  sudo python3 scripts/install_onboarding.py install    as root: root-owned launcher copy, /etc/linko/onboarding.json and a
                                                        sudoers drop-in allowing the service user exactly three commands
  python3 scripts/install_onboarding.py activate        as the manager's user: spool directories and the user path unit
                                                        that starts the launcher (the web service itself keeps
                                                        NoNewPrivileges and never runs sudo)
  python3 scripts/install_onboarding.py status | uninstall [--dry-run for install/uninstall]

Idempotent; re-run `install` after updating ai_manager/provision.py, because the root-owned launcher is a copy of it.
"""
import argparse
import json
import os
import pwd
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_worker_user import NAME, PYTHON, SAFE_PATH, SUDO, Layout, Step, apply, check_sudoers, digest, env_file, root_owned, write  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
UNITS = ('linko-bot-manager-onboarding.service', 'linko-bot-manager-onboarding.path')


def launcher_path(layout):
    return layout.lib / 'onboarding-launcher.py'


def config_path(layout):
    return layout.etc / 'onboarding.json'


def sudoers_path(layout):
    return layout.sudoers_dir / 'linko-onboarding'


def tick_command(data, layout=Layout()):
    return [SUDO, '-n', PYTHON, '-I', str(launcher_path(layout)), '--data', str(data), 'tick']


def sudoers_text(service, data, layout):
    base = f'{PYTHON} -I {launcher_path(layout)} --data {data}'
    return f"""# Managed by scripts/install_onboarding.py (Linko Bot Manager). Re-run it instead of editing this file.
# {service} may run only the root-owned onboarding launcher: handle the spooled Add User requests, and stop/start one
# onboarded instance (the launcher checks the name against units it wrote). No shell, no other command.
Cmnd_Alias LINKO_ONBOARD = {base} tick, {base} service stop *, {base} service start *
Defaults!LINKO_ONBOARD env_reset
{service} ALL=(root) NOPASSWD: LINKO_ONBOARD
"""


def config_text(service, data, repo, config_file, layout):
    return json.dumps({'service_user': service, 'data': str(data), 'repo': str(repo), 'app_python': str(repo / '.venv/bin/python'),
                       'launcher': str(launcher_path(layout)), 'config_file': str(config_file)}, indent=2) + '\n'


def defaults(service):
    try:
        entry = pwd.getpwnam(service)
    except KeyError:
        raise ValueError(f'service user {service} does not exist') from None
    home = Path(entry.pw_dir)
    data = Path(env_file(home / '.config/linko-bot-manager.env').get('AI_MANAGER_DATA') or home / '.local/share/linko-bot-manager')
    return home, data.expanduser()


def validate(service, data, repo):
    if not NAME.fullmatch(service) or service == 'root':
        raise ValueError(f'invalid service user: {service}')
    for path in (data, repo):
        if not SAFE_PATH.fullmatch(str(path)):
            raise ValueError(f'use a plain absolute path without spaces or special characters: {path}')


def install_plan(service, data, repo=REPO, config_file=None, layout=Layout()):
    validate(service, data, repo)
    config_file = config_file or defaults(service)[0] / '.config/linko-onboarding.env'
    return [Step('run', {'argv': ['install', '-d', '-o', 'root', '-g', 'root', '-m', '0755', str(layout.lib), str(layout.etc)]}),
            write(launcher_path(layout), (repo / 'ai_manager/provision.py').read_text(), 0o755),
            write(config_path(layout), config_text(service, data, repo, config_file, layout), 0o644),
            Step('sudoers', {'path': sudoers_path(layout), 'content': sudoers_text(service, data, layout), 'mode': 0o440,
                             'owner': 'root:root'})]


def uninstall_plan(layout=Layout()):
    return [Step('remove', {'path': p}) for p in (sudoers_path(layout), config_path(layout), launcher_path(layout))]


def installed(data, layout=Layout()):
    """True when the root part is installed for this data directory."""
    try:
        config = json.loads(config_path(layout).read_text())
    except (OSError, ValueError):
        return False
    return config.get('data') == str(data) and root_owned(launcher_path(layout)) and sudoers_path(layout).is_file()


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, timeout=30, capture_output=True, text=True)


def activate(repo, data, home, layout=Layout(), enable=True):
    if not installed(data, layout):
        raise SystemExit('Run `sudo python3 scripts/install_onboarding.py install` first')
    if any(ch in str(repo) + str(data) + str(home) for ch in ' \n\r"%'):
        raise SystemExit('Use paths without spaces or systemd escape characters')
    for name in ('requests', 'results'):
        (data / 'onboarding' / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    units = home / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    for name in UNITS:
        text = ((repo / 'deploy' / (name + '.in')).read_text().replace('@DATA@', str(data))
                .replace('@START@', ' '.join(tick_command(data, layout))))
        (units / name).write_text(text)
    (data / 'onboarding' / 'installed.json').write_text(json.dumps({'launcher': str(launcher_path(layout)), 'installed_at': time.time()}))
    if enable:
        systemctl('daemon-reload')
        systemctl('enable', '--now', UNITS[1])


def status(data, home, repo=REPO, layout=Layout(), run=subprocess.run):
    units = home / '.config/systemd/user'
    report = {'root_part': installed(data, layout), 'launcher_current': digest(launcher_path(layout)) == digest(repo / 'ai_manager/provision.py'),
              'sudoers_valid': check_sudoers(sudoers_path(layout), run)[0] if os.geteuid() == 0 and sudoers_path(layout).is_file() else None,
              'units': all((units / n).is_file() for n in UNITS), 'activated': (data / 'onboarding/installed.json').is_file(),
              'queued_requests': len(list((data / 'onboarding/requests').glob('*.json')))}
    if report['units']:
        report['path_unit'] = systemctl('is-active', UNITS[1], check=False).stdout.strip()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['install', 'activate', 'status', 'uninstall'])
    parser.add_argument('--service-user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name,
                        help='the user the manager runs as (default: the user who ran sudo)')
    parser.add_argument('--data', help='manager data directory (default: from the service env file)')
    parser.add_argument('--dry-run', action='store_true', help='install/uninstall: print the plan; change nothing')
    args = parser.parse_args(argv)
    home, data = defaults(args.service_user)
    data = Path(args.data).expanduser() if args.data else data
    if args.command == 'activate':
        activate(REPO, data, Path.home())
        print('Add User host helper active. The Add User screen now plans and provisions through it.')
    elif args.command == 'status':
        print(json.dumps(status(data, Path.home()), indent=2))
    else:
        if os.geteuid() != 0 and not args.dry_run:
            parser.error(f'{args.command} needs root (sudo); use --dry-run to see the plan')
        steps = install_plan(args.service_user, data) if args.command == 'install' else uninstall_plan()
        if args.dry_run:
            for step in steps:
                print(step.describe())
            return 0
        apply(steps)
        print(f'Done. Now, as {args.service_user}: python3 scripts/install_onboarding.py activate' if args.command == 'install'
              else 'Removed. As the manager\'s user, also run: systemctl --user disable --now ' + UNITS[1])
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Owner-run install/status/uninstall of the independent, snapshotted repair timer. Never deploys or restarts the app."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from emergency_repair import atomic, read

UNITS = ('linko-bot-manager-repair.service', 'linko-bot-manager-repair.timer')
TIMER = UNITS[1]


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, timeout=30, capture_output=True, text=True)


def install(repo, data, home, conversation, provider='codex', model=None, auto_detect=False, activate=True):
    if any(c in str(repo) + str(data) + str(home) + sys.executable for c in ' \n\r"%'):
        raise ValueError('Use paths without spaces or systemd escape characters')
    root = data / 'repair'
    # Copy the standalone launcher outside the checkout and venv, so a broken app import/deploy cannot disable it.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / 'launcher.py'
    temporary = root / 'launcher.new'
    shutil.copyfile(repo / 'scripts/emergency_repair.py', temporary)
    os.chmod(temporary, 0o700)
    os.replace(temporary, target)
    atomic(root / 'installed.json', {'repo': str(repo), 'conversation_id': conversation, 'provider': provider,
           'model': model, 'auto_detect': auto_detect, 'codex_bin': os.getenv('AI_MANAGER_CODEX', 'codex'),
           'claude_bin': os.getenv('AI_MANAGER_CLAUDE', str(home / '.local/bin/claude')),
           'url': 'http://127.0.0.1:' + os.getenv('AI_MANAGER_PORT', '8787')})
    units = home / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    # Use host Python, never the application's .venv.
    python = next((str(path) for path in (Path('/usr/bin/python3'), Path('/usr/local/bin/python3'))
                   if path.is_file()), None)
    if not python:
        raise ValueError('A host python3 is required')
    (units / 'linko-bot-manager-repair.service').write_text(f'''[Unit]
Description=Independent Linko emergency repair and result delivery
[Service]
Type=oneshot
ExecStart={python} {target} --data {data} tick
Environment=PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-{home}/.config/linko-bot-manager.env
UMask=0077
TimeoutStartSec=35min
KillMode=control-group
''')
    (units / 'linko-bot-manager-repair.timer').write_text('''[Unit]
Description=Wake independent Linko emergency repair
[Timer]
OnBootSec=60
OnUnitInactiveSec=60
Persistent=true
[Install]
WantedBy=timers.target
''')
    if activate:
        systemctl('daemon-reload')
        systemctl('enable', '--now', TIMER)
    return target


def status(data, home):
    root, units = data / 'repair', home / '.config/systemd/user'
    config = read(root / 'installed.json', {})

    def unit(*args):
        return systemctl(*args, TIMER, check=False).stdout.strip()

    return {'installed': bool(config) and (root / 'launcher.py').is_file() and all((units / u).is_file() for u in UNITS),
            'enabled': unit('is-enabled'), 'active': unit('is-active'), 'conversation': config.get('conversation_id'),
            'auto_detect': config.get('auto_detect'), 'launcher': str(root / 'launcher.py'),
            'requests': len(list((root / 'requests').glob('*.json')))}


def uninstall(data, home, activate=True):
    """Remove timer, units and launcher snapshot. Request receipts and run logs are kept."""
    if activate:
        systemctl('disable', '--now', TIMER, check=False)
    for path in [home / '.config/systemd/user' / u for u in UNITS] + [data / 'repair' / n for n in
                                                                       ('launcher.py', 'installed.json')]:
        path.unlink(missing_ok=True)
    if activate:
        systemctl('daemon-reload', check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('install', help='Idempotent; re-run to refresh the launcher snapshot')
    add.add_argument('--conversation', help='Exact existing chat ID for results (required on first install)')
    add.add_argument('--provider', choices=['codex', 'claude'], default='codex')
    add.add_argument('--model')
    add.add_argument('--auto-detect', action='store_true', help='Also request repairs after 30 minutes of outage')
    commands.add_parser('status')
    commands.add_parser('uninstall')
    args = parser.parse_args(argv)
    os.umask(0o077)
    data = Path(os.getenv('AI_MANAGER_DATA', '~/.local/share/linko-bot-manager')).expanduser()
    home = Path.home()
    if args.command == 'status':
        print(json.dumps(status(data, home), indent=2))
    elif args.command == 'uninstall':
        uninstall(data, home)
        print('Repair timer removed. Receipts kept under ' + str(data / 'repair'))
    else:
        conversation = args.conversation or read(data / 'repair/installed.json', {}).get('conversation_id')
        if not conversation:
            parser.error('install requires --conversation on first run')
        target = install(Path(__file__).resolve().parents[1], data, home, conversation,
                         args.provider, args.model, args.auto_detect)
        try:
            lingering = subprocess.run(['loginctl', 'enable-linger'], timeout=30, capture_output=True).returncode == 0
        except OSError:
            lingering = False
        print('Repair timer enabled. Host launcher: ' + str(target))
        if not lingering:
            print('Warning: could not enable lingering; run `loginctl enable-linger $USER` for unattended '
                  'operation after logout/reboot.')


if __name__ == '__main__':
    main()

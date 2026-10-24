#!/usr/bin/env python3
"""Owner-run install/status/uninstall of the independent, snapshotted repair timer. Never deploys or restarts the app."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from emergency_repair import DEFAULT_TURN_SECONDS, atomic, read
from install_worker_user import repair_command

UNITS = ('linko-bot-manager-repair.service', 'linko-bot-manager-repair.timer')
TIMER = UNITS[1]


def systemctl(*args, check=True):
    return subprocess.run(['systemctl', '--user', *args], check=check, timeout=30, capture_output=True, text=True)


def turn_seconds_arg(value):
    seconds = int(value)
    if not 300 <= seconds <= 86400:
        raise argparse.ArgumentTypeError('must be between 300 and 86400 seconds')
    return seconds


def detect_provider(home, env=os.environ):
    """The fallback provider: the one whose CLI is installed and logged in, claude preferred if both are. Failing that,
    an installed CLI (claude first), else claude."""
    claude = env.get('AI_MANAGER_CLAUDE', str(home / '.local/bin/claude'))
    codex = env.get('AI_MANAGER_CODEX', 'codex')
    present = {'claude': shutil.which(claude) is not None, 'codex': shutil.which(codex) is not None}
    login = {'claude': (home / '.claude/.credentials.json').is_file(), 'codex': (home / '.codex/auth.json').is_file()}
    for names in ([n for n in present if present[n] and login[n]], [n for n in present if present[n]]):
        if names:
            return names[0]
    return 'claude'


def install(repo, data, home, conversation, provider=None, model=None, auto_detect=False,
            turn_seconds=None, activate=True, privileged=None):
    """privileged: ExecStart argv for the root-owned launcher (install_worker_user.repair_command); None keeps the
    service user's own snapshot."""
    if any(c in str(repo) + str(data) + str(home) + sys.executable for c in ' \n\r"%'):
        raise ValueError('Use paths without spaces or systemd escape characters')
    root = data / 'repair'
    previous = read(root / 'installed.json', {})
    if turn_seconds is None:
        turn_seconds = previous.get('turn_seconds', DEFAULT_TURN_SECONDS)
    if not isinstance(turn_seconds, int) or not 300 <= turn_seconds <= 86400:
        raise ValueError('turn_seconds must be between 300 and 86400')
    # Copy the standalone launcher outside the checkout and venv, so a broken app import/deploy cannot disable it.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / 'launcher.py'
    temporary = root / 'launcher.new'
    shutil.copyfile(repo / 'scripts/emergency_repair.py', temporary)
    os.chmod(temporary, 0o700)
    os.replace(temporary, target)
    provider = provider or detect_provider(home)
    atomic(root / 'installed.json', {'repo': str(repo), 'conversation_id': conversation, 'provider': provider,
           'model': model, 'auto_detect': auto_detect, 'codex_bin': os.getenv('AI_MANAGER_CODEX', 'codex'),
           'claude_bin': os.getenv('AI_MANAGER_CLAUDE', str(home / '.local/bin/claude')),
           'url': 'http://127.0.0.1:' + os.getenv('AI_MANAGER_PORT', '8787'), 'turn_seconds': turn_seconds,
           # sudo resets PATH; the root host agent needs this one to find node-based provider CLIs.
           'path': os.getenv('PATH', '/usr/local/bin:/usr/bin:/bin')})
    units = home / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    # Use host Python, never the application's .venv.
    python = next((str(path) for path in (Path('/usr/bin/python3'), Path('/usr/local/bin/python3'))
                   if path.is_file()), None)
    if not python:
        raise ValueError('A host python3 is required')
    start = ' '.join(privileged) if privileged else f'{python} {target} --data {data} tick'
    (units / 'linko-bot-manager-repair.service').write_text(f'''[Unit]
Description=Independent Linko emergency repair and result delivery
[Service]
Type=oneshot
ExecStart={start}
Environment=PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-{home}/.config/linko-bot-manager.env
UMask=0077
TimeoutStartSec={turn_seconds + 300}s
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


def read_text(path):
    try:
        return path.read_text()
    except OSError:
        return ''


def status(data, home):
    root, units = data / 'repair', home / '.config/systemd/user'
    config = read(root / 'installed.json', {})

    def unit(*args):
        return systemctl(*args, TIMER, check=False).stdout.strip()

    return {'installed': bool(config) and (root / 'launcher.py').is_file() and all((units / u).is_file() for u in UNITS),
            'fallback_provider': config.get('provider'), 'enabled': unit('is-enabled'), 'active': unit('is-active'), 'conversation': config.get('conversation_id'),
            'auto_detect': config.get('auto_detect'), 'turn_seconds': config.get('turn_seconds', DEFAULT_TURN_SECONDS),
            'launcher': str(root / 'launcher.py'),
            'as_root': 'ExecStart=/usr/bin/sudo ' in read_text(units / UNITS[0]),
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
    add.add_argument('--provider', choices=['codex', 'claude'],
                     help='Fallback only, for requests with no provider from the app (default: the installed, '
                          'logged-in CLI; claude if both)')
    add.add_argument('--model')
    add.add_argument('--turn-seconds', type=turn_seconds_arg,
                     help='Provider CLI turn timeout (300..86400 seconds)')
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
        privileged = repair_command(data)
        target = install(Path(__file__).resolve().parents[1], data, home, conversation,
                         args.provider, args.model, args.auto_detect, args.turn_seconds, privileged=privileged)
        try:
            lingering = subprocess.run(['loginctl', 'enable-linger'], timeout=30, capture_output=True).returncode == 0
        except OSError:
            lingering = False
        print('Repair timer enabled. Host launcher: ' + (privileged[4] + ' (as root through sudo)' if privileged
                                                         else str(target)))
        if not lingering:
            print('Warning: could not enable lingering; run `loginctl enable-linger $USER` for unattended '
                  'operation after logout/reboot.')


if __name__ == '__main__':
    main()

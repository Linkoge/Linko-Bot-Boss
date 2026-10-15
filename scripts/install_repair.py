#!/usr/bin/env python3
"""Explicit operator bootstrap of an independent, snapshotted repair timer. Does not deploy the app."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from emergency_repair import atomic


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
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=30)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'linko-bot-manager-repair.timer'],
                       check=True, timeout=30)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--conversation', required=True, help='Exact existing originating chat ID for watchdog results')
    parser.add_argument('--provider', choices=['codex', 'claude'], default='codex')
    parser.add_argument('--model')
    parser.add_argument('--auto-detect', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    target = install(Path(__file__).resolve().parents[1], Path(os.getenv(
        'AI_MANAGER_DATA', '~/.local/share/linko-bot-manager')).expanduser(), Path.home(), args.conversation,
        args.provider, args.model, args.auto_detect)
    print('Repair timer enabled. Host launcher: ' + str(target))


if __name__ == '__main__':
    main()

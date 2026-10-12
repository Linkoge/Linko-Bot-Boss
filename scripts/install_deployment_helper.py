"""Install the fixed helper independently; never stop/restart the manager."""
import argparse
import os
from pathlib import Path

from ai_manager.deployment import atomic, read, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', required=True, type=Path)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--https', required=True)
    args = parser.parse_args()
    if not args.https.startswith('https://') or any(c in args.https for c in '\n\r"% '):
        raise ValueError('Use existing private HTTPS origin')
    os.umask(0o077)
    root = args.data.resolve() / 'deployment-helper'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {'repo': str(args.live.resolve()), 'data': str(args.data.resolve()),
              'native': str(Path.home() / '.codex'), 'loopback': 'http://127.0.0.1:8787', 'https': args.https,
              'preserve_files': [str(Path.home() / '.config/systemd/user/linko-bot-manager.service'),
                                 str(Path.home() / '.config/linko-bot-manager.env')]}
    if (root / 'config.json').exists() and read(root / 'config.json') != config:
        raise ValueError('Existing helper configuration differs; review separately')
    atomic(root / 'config.json', config)
    source = Path(__file__).resolve().parents[1] / 'ai_manager/deployment.py'
    installed = root / 'helper-v1.py'
    if installed.exists() and installed.read_bytes() != source.read_bytes():
        raise ValueError('Installed helper differs; version/review helper separately')
    installed.write_bytes(source.read_bytes())
    python = args.live.resolve() / '.venv/bin/python'
    for path in (root, python):
        if any(c in str(path) for c in '\n\r"% '):
            raise ValueError('Unsupported systemd path')
    unit = f'''[Unit]
Description=Linko controlled independent deployment helper
After=default.target
[Service]
Type=simple
ExecStart={python} {installed} {root / 'config.json'}
UMask=0077
NoNewPrivileges=yes
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
'''
    destination = Path.home() / '.config/systemd/user/linko-deployment-helper.service'
    if destination.exists() and destination.read_text() != unit:
        raise ValueError('Existing helper unit differs')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(unit)
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', '--now', 'linko-deployment-helper.service'])
    print('Independent helper installed; manager was not restarted')


if __name__ == '__main__':
    main()

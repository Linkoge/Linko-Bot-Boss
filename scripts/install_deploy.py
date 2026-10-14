#!/usr/bin/env python3
"""One-time operator command that installs and enables the deploy mechanism (user systemd units, no sudo).

    python3 scripts/install_deploy.py

It writes DATA/deploy/installed.json, installs a path unit that runs scripts/deploy.py whenever the manager writes a
deploy request, retires the old deployment helper service if it is idle, and queues the first deploy so the running
service picks up the code now in main. Safe to re-run.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import atomic, read  # noqa: E402

UNITS = ('linko-bot-manager-deploy.service', 'linko-bot-manager-deploy.path')
IDLE_PHASES = {None, 'idle', 'succeeded', 'failed', 'rolled_back', 'cancelled'}


def install(repo, data, home):
    venv_python = repo / '.venv/bin/python'
    python3 = shutil.which('python3')
    if not python3 or not venv_python.exists():
        raise SystemExit('Run from the live checkout with its .venv (uv sync --locked) and python3 installed')
    if any(ch in str(repo) + str(data) for ch in ' \n\r"%'):
        raise SystemExit('Use paths without spaces or systemd escape characters')
    uv = shutil.which('uv') or str(home / '.local/bin/uv')
    config = {'repo': str(repo), 'data': str(data), 'service': 'linko-bot-manager.service',
              'url': 'http://127.0.0.1:' + os.getenv('AI_MANAGER_PORT', '8787'), 'uv': uv if Path(uv).exists() else None,
              'test_cmd': [str(venv_python), '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
              'installed_at': time.time()}
    atomic(data / 'deploy' / 'installed.json', config)
    directory = home / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    for name in UNITS:
        text = ((repo / 'deploy' / (name + '.in')).read_text().replace('@REPO@', str(repo))
                .replace('@DATA@', str(data)).replace('@PYTHON@', python3))
        (directory / name).write_text(text)
    systemctl = ['systemctl', '--user']
    subprocess.run([*systemctl, 'daemon-reload'], check=True)
    old = read(data / 'deployment-helper' / 'active.json') or {}
    if old.get('phase') in IDLE_PHASES:
        subprocess.run([*systemctl, 'disable', '--now', 'linko-deployment-helper.service'], capture_output=True)
    else:
        print('Old deployment helper has an unfinished request; it was left running.')
    request = data / 'deploy' / 'request.json'
    if not request.exists():
        atomic(request, {'id': os.urandom(8).hex(), 'source': 'owner', 'reason': 'first deploy after install',
                         'requested_at': time.time()})
    subprocess.run([*systemctl, 'enable', '--now', 'linko-bot-manager-deploy.path'], check=True)
    print(json.dumps({'installed': str(data / 'deploy/installed.json'), 'first_deploy': 'queued'}, indent=2))
    print('Deploy mechanism installed. Progress: ' + str(data / 'deploy/last-deploy.txt'))


def main():
    install(REPO, Path(os.getenv('AI_MANAGER_DATA', '~/.local/share/linko-bot-manager')).expanduser(), Path.home())


if __name__ == '__main__':
    main()

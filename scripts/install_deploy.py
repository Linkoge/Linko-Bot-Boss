#!/usr/bin/env python3
"""One-time operator command that installs and enables the deploy mechanism (user systemd units, no sudo).

    python3 scripts/install_deploy.py [--instance NAME ...]

It writes DATA/deploy/installed.json, installs a path unit that runs scripts/deploy.py whenever the manager writes a
deploy request, and queues the first deploy so the running service picks up the code now in main. Safe to re-run.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import atomic  # noqa: E402
from deploy import read as deploy_read  # noqa: E402

UNITS = ('linko-bot-manager-deploy.service', 'linko-bot-manager-deploy.path')


def instance_entry(home, name):
    """One more instance of this code copy, described by its own env file (the single source of its data dir and port)."""
    path = home / f'.config/linko-bot-manager-{name}.env'
    if not path.is_file():
        raise SystemExit(f'Create {path} first (copy deploy/instance.env.example)')
    env = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
    data, port = env.get('AI_MANAGER_DATA', '').strip().strip('"\''), env.get('AI_MANAGER_PORT', '').strip()
    if not data or not port.isdigit():
        raise SystemExit(f'{path} must set AI_MANAGER_DATA and AI_MANAGER_PORT')
    return {'name': name, 'service': f'linko-bot-manager@{name}.service', 'data': data,
            'url': 'http://127.0.0.1:' + port}


def install(repo, data, home, instances=()):
    venv_python = repo / '.venv/bin/python'
    python3 = shutil.which('python3')
    if not python3 or not venv_python.exists():
        raise SystemExit('Run from the live checkout with its .venv (uv sync --locked) and python3 installed')
    if any(ch in str(repo) + str(data) for ch in ' \n\r"%'):
        raise SystemExit('Use paths without spaces or systemd escape characters')
    uv = shutil.which('uv') or str(home / '.local/bin/uv')
    # Instances already listed (including hand-edited service_cmds for another Linux user) are kept; named ones are upserted.
    kept = {i['name']: i for i in (deploy_read(data / 'deploy' / 'installed.json') or {}).get('instances', [])}
    kept.update({name: instance_entry(home, name) for name in instances})
    config = {'repo': str(repo), 'data': str(data), 'service': 'linko-bot-manager.service', 'instances': list(kept.values()),
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
    request = data / 'deploy' / 'request.json'
    if not request.exists():
        atomic(request, {'id': os.urandom(8).hex(), 'source': 'owner', 'reason': 'first deploy after install',
                         'requested_at': time.time()})
    subprocess.run([*systemctl, 'enable', '--now', 'linko-bot-manager-deploy.path'], check=True)
    print(json.dumps({'installed': str(data / 'deploy/installed.json'), 'first_deploy': 'queued'}, indent=2))
    print('Deploy mechanism installed. Progress: ' + str(data / 'deploy/last-deploy.txt'))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--instance', action='append', default=[], metavar='NAME',
                        help='also restart and health-check linko-bot-manager@NAME on every deploy (repeatable)')
    args = parser.parse_args()
    install(REPO, Path(os.getenv('AI_MANAGER_DATA', '~/.local/share/linko-bot-manager')).expanduser(), Path.home(),
            args.instance)


if __name__ == '__main__':
    main()

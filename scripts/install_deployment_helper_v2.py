"""Explicit future helper upgrade from an exact prepared artifact; no manager restart.

The same guarded procedure installs v3 (scripts/install_deployment_helper_v3.py) over v2.
"""
import argparse
import os
from pathlib import Path

from ai_manager.deployment import ID, Handoff, atomic, lock, read, run, sha


def check_external():
    cgroup = Path('/proc/self/cgroup').read_text()
    if 'linko-bot-manager.service' in cgroup or 'linko-deployment-helper.service' in cgroup:
        raise ValueError('Helper installation requires an authorized context outside manager/helper cgroups')


def install(data, release_id, version='v2'):
    check_external()
    previous = 'v' + str(int(version[1:]) - 1)
    handoff = Handoff(data)
    if not ID.fullmatch(release_id):
        raise ValueError('Use an exact prepared release ID')
    manifest = read(handoff.root / 'releases' / release_id / 'manifest.json')
    if not manifest or manifest.get('id') != release_id:
        raise ValueError('Release is not prepared')
    source = handoff.root / 'releases' / release_id / ('helper-' + version + '.py')
    if manifest.get('required_helper_version') != version or not source.is_file() or sha(source) != manifest.get('required_helper_sha256'):
        raise ValueError('Prepared artifact does not pin this helper ' + version)
    if handoff.gated():
        raise ValueError('Resolve the existing pending handoff before helper upgrade')
    config = read(handoff.root / 'config.json')
    if not config or config['data'] != str(data.resolve()):
        raise ValueError('Existing helper configuration is missing or differs')
    destination = handoff.root / ('helper-' + version + '.py')
    unit = Path.home() / '.config/systemd/user/linko-deployment-helper.service'
    current = unit.read_text()
    old_path = str(handoff.root / ('helper-' + previous + '.py'))
    new_path = str(destination)
    if current.count(old_path) != 1 or new_path in current:
        raise ValueError('Existing helper unit differs; inspect separately')
    with lock(handoff.root / 'helper.lock'), lock(handoff.root / 'protocol.lock'):
        if handoff.gated():
            raise ValueError('A deployment handoff arrived before helper upgrade')
        if destination.exists() and sha(destination) != sha(source):
            raise ValueError('Existing ' + version + ' helper differs')
        receipt = handoff.root / ('helper-' + version + '-upgrade.json')
        if receipt.exists():
            raise ValueError('A prior helper upgrade receipt exists; inspect it')
        atomic(receipt, {'phase':'prepared','release_id':release_id,'helper_sha256':sha(source),
                         'old_helper_sha256':sha(Path(old_path)),'old_unit':current})
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o600)
        unit.write_text(current.replace(old_path,new_path))
        run(['systemctl','--user','daemon-reload'])
        run(['systemctl','--user','restart','linko-deployment-helper.service'])
        run(['systemctl','--user','is-active','linko-deployment-helper.service'])
        atomic(receipt, {**read(receipt),'phase':'succeeded','unit_sha256':sha(unit)})
    print('Helper ' + version + ' upgraded; manager has not been restarted or deployed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('release_id')
    parser.add_argument('--data',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    install(args.data.resolve(),args.release_id)

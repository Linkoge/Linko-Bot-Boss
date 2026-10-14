"""Explicit future helper upgrade from an exact prepared artifact; no manager restart."""
import argparse
import os
from pathlib import Path

from ai_manager.deployment import ID, Handoff, atomic, lock, read, run, sha


def install(data, release_id):
    handoff = Handoff(data)
    if not ID.fullmatch(release_id):
        raise ValueError('Use an exact prepared release ID')
    manifest = read(handoff.root / 'releases' / release_id / 'manifest.json')
    if not manifest or manifest.get('id') != release_id:
        raise ValueError('Release is not prepared')
    source = handoff.root / 'releases' / release_id / 'helper-v2.py'
    if manifest.get('required_helper_version') != 'v2' or not source.is_file() or sha(source) != manifest.get('required_helper_sha256'):
        raise ValueError('Prepared artifact does not pin this helper v2')
    if handoff.gated():
        raise ValueError('Resolve the existing pending handoff before helper upgrade')
    config = read(handoff.root / 'config.json')
    if not config or config['data'] != str(data.resolve()):
        raise ValueError('Existing helper configuration is missing or differs')
    destination = handoff.root / 'helper-v2.py'
    unit = Path.home() / '.config/systemd/user/linko-deployment-helper.service'
    current = unit.read_text()
    old_path = str(handoff.root / 'helper-v1.py')
    new_path = str(destination)
    if current.count(old_path) != 1 or new_path in current:
        raise ValueError('Existing helper unit differs; inspect separately')
    with lock(handoff.root / 'helper.lock'), lock(handoff.root / 'protocol.lock'):
        if handoff.gated():
            raise ValueError('A deployment handoff arrived before helper upgrade')
        if destination.exists() and sha(destination) != sha(source):
            raise ValueError('Existing v2 helper differs')
        receipt = handoff.root / 'helper-v2-upgrade.json'
        if receipt.exists():
            raise ValueError('A prior helper upgrade receipt exists; inspect it')
        atomic(receipt, {'phase':'prepared','release_id':release_id,'helper_sha256':sha(source),
                         'old_helper_sha256':sha(handoff.root/'helper-v1.py'),'old_unit':current})
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o600)
        unit.write_text(current.replace(old_path,new_path))
        run(['systemctl','--user','daemon-reload'])
        run(['systemctl','--user','restart','linko-deployment-helper.service'])
        run(['systemctl','--user','is-active','linko-deployment-helper.service'])
        atomic(receipt, {**read(receipt),'phase':'succeeded','unit_sha256':sha(unit)})
    print('Helper v2 upgraded; manager has not been restarted or deployed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('release_id')
    parser.add_argument('--data',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    install(args.data.resolve(),args.release_id)

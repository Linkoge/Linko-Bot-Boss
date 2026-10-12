"""One-time external bootstrap, after an operator stops the already idle old manager.

This command never stops a manager without a cooperative gate. It fails closed if
run from a releasing worker or if the old service is still active.
"""
import argparse
import sqlite3
from types import SimpleNamespace

from ai_manager.deployment import ID, Helper, idle, lock, read, run


def bootstrap(config, release_id):
    if not ID.fullmatch(release_id):
        raise ValueError('Use a prepared release ID')
    helper = Helper(read(config))
    helper.check_external()
    with lock(helper.root / 'helper.lock'):
        state = run(['systemctl', '--user', 'show', 'linko-bot-manager.service', '--property=ActiveState', '--value'])
        if state != 'inactive':
            raise ValueError('External operator must first stop the idle manager in a quiet submission window')
        with sqlite3.connect(helper.data / 'manager.sqlite3') as conn:
            if any(idle(conn).values()):
                raise ValueError('Stopped database is not idle; start original manager and defer bootstrap')
        manifest = read(helper.root / 'releases' / release_id / 'manifest.json')
        helper.validate(manifest)
        helper.handoff.request(release_id)
        helper.handoff.acknowledge(SimpleNamespace(connect=lambda: sqlite3.connect(helper.data / 'manager.sqlite3')))
    return helper.handoff.status()  # Dedicated helper consumes the acknowledgement.


if __name__ == '__main__':
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('release_id')
    args = parser.parse_args()
    print(bootstrap(args.config, args.release_id)['phase'])

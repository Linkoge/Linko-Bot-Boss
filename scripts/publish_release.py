"""Register exact validated artifacts after worker cleanup; never deploy or retest."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from ai_manager.db import Database
from ai_manager.release_publication import publish


class ExistingDatabase(Database):
    def __init__(self, path):
        # Publication must not initialize/migrate the live database.
        self.path = path
        with sqlite3.connect(f'file:{path}?mode=rw', uri=True):
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--index-sha256', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(publish(ExistingDatabase(args.data.resolve() / 'manager.sqlite3'), args.source,
                             args.manifest_sha256, args.index_sha256, args.task_id)))

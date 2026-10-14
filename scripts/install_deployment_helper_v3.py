"""Explicit future helper v3 upgrade over installed v2: rehearsed schema/dependency releases with database
(and environment) restore on rollback. Same guarded procedure as v2; no manager restart or deployment."""
import argparse
import os
from pathlib import Path

from install_deployment_helper_v2 import install

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('release_id')
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    install(args.data.resolve(), args.release_id, 'v3')

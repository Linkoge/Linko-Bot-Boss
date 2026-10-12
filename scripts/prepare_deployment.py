"""Trusted local preparation only. Conversation receives an ID, never a path/argv."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from ai_manager.deployment import ID, Handoff, atomic, run, sha


def prepare(repo, live, data, release_id, python):
    if not ID.fullmatch(release_id):
        raise ValueError('Invalid release ID')
    if run(['git', 'status', '--porcelain'], repo):
        raise ValueError('Commit reviewed candidate before preparing it')
    base = run(['git', 'rev-parse', 'HEAD'], live)
    commit = run(['git', 'rev-parse', 'HEAD'], repo)
    run(['git', 'merge-base', '--is-ancestor', base, commit], repo)
    for name in ('pyproject.toml', 'uv.lock', 'ai_manager/db.py'):
        if run(['git', 'diff', base, commit, '--', name], repo):
            raise ValueError('Schema/dependency changes are outside this helper')
    target = Handoff(data).root / 'releases' / release_id
    target.mkdir(parents=True, mode=0o700)  # Never overwrite a prepared receipt.
    checks = [[str(python), '-m', 'pytest', '-q'], [str(python), '-m', 'ruff', 'check', '.'],
              ['node', '--check', 'ai_manager/static/app.js'],
              ['node', '--check', 'ai_manager/static/usage.js'],
              [str(python), 'scripts/browser_smoke.py'], [str(python), 'scripts/browser_deployment.py'],
              [str(python), 'scripts/browser_account_usage.py']]
    with (target / 'validation.log').open('w') as log:
        for argv in checks:
            result = subprocess.run(argv, cwd=repo, stdout=log, stderr=log, timeout=180)
            if result.returncode:
                raise ValueError('Release validation failed; see private validation.log')
    run(['git', 'bundle', 'create', str(target / 'release.bundle'), 'HEAD'], repo)
    def assets(ref):
        names = run(['git', 'ls-tree', '-r', '--name-only', ref, '--', 'ai_manager/static'], repo).splitlines()
        import hashlib
        return {name.removeprefix('ai_manager/static/'): hashlib.sha256(subprocess.run(
            ['git', 'show', ref+':'+name], cwd=repo, capture_output=True, check=True).stdout).hexdigest()
                for name in names}
    manifest = {'id': release_id, 'base': base, 'commit': commit,
                'assets': assets(commit), 'base_assets': assets(base),
                'bundle_sha256': sha(target / 'release.bundle'), 'validation_sha256': sha(target / 'validation.log')}
    atomic(target / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('release_id')
    parser.add_argument('--live', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    prepare(Path(__file__).resolve().parents[1], args.live.resolve(), args.data.resolve(), args.release_id,
            Path(sys.executable))
    print('Prepared release ' + args.release_id)

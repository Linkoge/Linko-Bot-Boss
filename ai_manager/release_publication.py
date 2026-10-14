"""Trusted, serialized import of validated immutable releases. Never activates code."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .deployment import ID, Handoff, lock, read, run, schema_change, sha
from .deployment_intent import assignment_evidence


def inventory(root, manifest_digest, index_digest):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Release must be a real directory')
    files = {}
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Release symlinks/special files are forbidden')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha(path)
    if files.get('manifest.json') != manifest_digest or files.get('evidence-index.json') != index_digest:
        raise ValueError('Pinned manifest/index checksum mismatch')
    manifest, index = read(root / 'manifest.json'), read(root / 'evidence-index.json')
    if not ID.fullmatch(manifest.get('id', '')) or any(
            not re.fullmatch('[0-9a-f]{40}', manifest.get(k, '')) for k in ('base', 'commit')):
        raise ValueError('Invalid release identity')
    if (index.get('base') != manifest['base'] or index.get('candidate', index.get('commit')) != manifest['commit']
            or index.get('release_id') != manifest['id']):
        raise ValueError('Evidence index identity mismatch')
    listed = index.get('files', {})
    if any(PurePosixPath(p).is_absolute() or '..' in PurePosixPath(p).parts for p in listed):
        raise ValueError('Invalid evidence path')
    if listed != {p: digest for p, digest in files.items() if p != 'evidence-index.json'}:
        raise ValueError('Evidence index inventory/checksum mismatch')
    for name, field in (('release.bundle', 'bundle_sha256'), ('validation.log', 'validation_sha256')):
        if files.get(name) != manifest.get(field) or not files.get(name):
            raise ValueError('Bundle/validation checksum mismatch')
    return manifest, files


def publish(db, source, manifest_digest, index_digest, task_id):
    """Run after cleanup, under preparation lock + SQLite/protocol serialization.

    A failed attempt never replaces a registered ID; retries compare every byte.
    No intent is created/revised and no helper request is made.
    """
    source = Path(source).absolute()
    handoff = Handoff(db.path.parent)
    with lock(handoff.root / 'preparation.lock'), db.transaction() as conn, lock(handoff.root / 'protocol.lock'):
        if conn.execute("SELECT 1 FROM settings WHERE key IN ('assignment_integration','release_preparation')").fetchone():
            raise ValueError('Publication waits for integration/preparation cleanup')
        if handoff.gated():
            raise ValueError('Deployment is already pending')
        manifest, files = inventory(source, manifest_digest, index_digest)
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or not task['workspace']:
            raise ValueError('Owned assignment workspace required')
        workspace = Path(task['workspace']).resolve()
        if not source.resolve().is_relative_to(workspace):
            raise ValueError('Release source must belong to the assignment workspace')
        project = conn.execute('SELECT * FROM projects WHERE id=?', (task['project_id'],)).fetchone()
        config = read(handoff.root / 'config.json') or {}
        if not project or project['status'] != 'active' or config.get('repo') != project['path']:
            raise ValueError('Registered deployment project mismatch or inactive project')
        if conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND lease_owner IS NOT NULL', (task['project_id'],)).fetchone():
            raise ValueError('Publication waits for confirmed worker cleanup')
        if run(['git', 'status', '--porcelain'], project['path']) or run(['git', 'rev-parse', 'HEAD'], project['path']) != manifest['base']:
            raise ValueError('Current live base changed or dirty')
        if run(['git', 'status', '--porcelain'], workspace) or run(['git', 'rev-parse', 'HEAD'], workspace) != manifest['commit']:
            raise ValueError('Candidate changed or dirty')
        if run(['git', 'branch', '--show-current'], workspace) != task['workspace_branch']:
            raise ValueError('Assignment branch ownership mismatch')
        run(['git', 'merge-base', '--is-ancestor', manifest['base'], manifest['commit']], workspace)
        run(['git', 'bundle', 'verify', str(source / 'release.bundle')], workspace)
        heads = run(['git', 'bundle', 'list-heads', str(source / 'release.bundle')], workspace)
        if manifest['commit'] + ' HEAD' not in heads.splitlines():
            raise ValueError('Bundle does not contain the exact candidate')
        if schema_change(workspace, manifest['base'], manifest['commit']) != manifest.get('schema'):
            raise ValueError('Schema contract mismatch')
        for ref, field in ((manifest['base'], 'base_assets'), (manifest['commit'], 'assets')):
            names = run(['git', 'ls-tree', '-r', '--name-only', ref, '--', 'ai_manager/static'], workspace).splitlines()
            assets = {}
            for name in names:
                content = subprocess.run(['git', 'show', ref + ':' + name], cwd=workspace,
                                         check=True, capture_output=True, timeout=60).stdout
                assets[name.removeprefix('ai_manager/static/')] = hashlib.sha256(content).hexdigest()
            if assets != manifest.get(field):
                raise ValueError('Pinned assets do not match Git source')
        bindings = manifest.get('assignments', {})
        if task_id not in bindings:
            raise ValueError('Publication requires the owning task binding')
        for identifier, digest in bindings.items():
            bound = conn.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
            if (not bound or bound['project_id'] != task['project_id'] or bound['lifecycle'] != 'completed'
                    or bound['lease_owner']):
                raise ValueError('Bound assignment incomplete or held')
            workflow = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_workflow:' + identifier,)).fetchone()
            if workflow and json.loads(workflow[0]).get('state') in {'owner_wait', 'deferred'}:
                raise ValueError('Bound assignment is held by owner')
            if not conn.execute("SELECT 1 FROM executions WHERE task_id=? AND status='completed' AND finished_at IS NOT NULL", (identifier,)).fetchone():
                raise ValueError('Completed worker evidence required')
            if assignment_evidence(conn, bound, manifest.get('assignment_evidence_version', 1)) != digest:
                raise ValueError('Assignment scope/evidence changed')
        target = handoff.root / 'releases' / manifest['id']
        if target.exists():
            _, existing = inventory(target, manifest_digest, index_digest)
            if existing != files:
                raise ValueError('Registered release differs; never overwrite')
            return {'release_id': manifest['id'], 'phase': 'registered', 'path': str(target), 'reused': True}
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix='.publication-', dir=target.parent))
        try:
            for name in files:
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with (source / name).open('rb') as src, destination.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                destination.chmod(0o600)
            inventory(staging, manifest_digest, index_digest)
            for directory in [p for p in staging.rglob('*') if p.is_dir()] + [staging]:
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            staging.rename(target)
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        receipt = {'release_id': manifest['id'], 'phase': 'registered', 'path': str(target),
                   'manifest_sha256': manifest_digest, 'index_sha256': index_digest, 'commit': manifest['commit']}
        db.event(conn, 'release.published', receipt, task['project_id'], task_id)
        return receipt

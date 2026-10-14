"""Private, fixed-operation deployment protocol. Also installed outside the manager cgroup.

No application imports: the helper must survive replacement of application code.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

TERMINAL = {'succeeded', 'failed', 'rolled_back', 'cancelled'}
ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
CRITICAL = ('owner', 'sessions', 'chat_requests', 'todos', 'conversations', 'messages', 'executions', 'tasks')

# Helper v2 permits only this reviewed additive workspace/index migration. The
# installed v1 remains unchanged and rejects it until separately upgraded.
PARALLEL_DB_BEFORE = '6bd2534dac72ba60934f0531750eeed250fb6492abdf80af39f4d9d8decd434e'
PARALLEL_DB_AFTER = 'ba5444f9986e9f7028e77eeed9fb1add0c3a4815c98142c013705f3b18d17223'
PARALLEL_SCHEMA = {'migration': 'parallel-workspaces-v12', 'from': 11, 'to': 12,
                   'db_sha256': PARALLEL_DB_AFTER, 'helper': 'v2'}


# Helper v3: any other schema/dependency release is allowed only with a passing migration rehearsal on a
# private copy of the live database, a stopped pre-activation backup, and data (plus venv) restore on rollback.
REHEARSED = 'rehearsed-v3'
DEPENDENCIES = ('pyproject.toml', 'uv.lock')
MIGRATE = 'import sys; sys.path.insert(0, sys.argv[1]); from ai_manager.db import Database; Database(sys.argv[2])'


def blob(repo, ref, name):
    result = subprocess.run(['git', 'show', ref + ':' + name], cwd=repo, capture_output=True)
    return hashlib.sha256(result.stdout).hexdigest() if result.returncode == 0 else None


def schema_change(repo, base, commit):
    """None for code-only releases; all newly prepared schema/dependency changes require rehearsal."""
    dependencies = {name: blob(repo, commit, name) for name in DEPENDENCIES
                    if run(['git', 'diff', base, commit, '--', name], repo)}
    if not dependencies and not run(['git', 'diff', base, commit, '--', 'ai_manager/db.py'], repo):
        return None
    after = blob(repo, commit, 'ai_manager/db.py')
    return {'migration': REHEARSED, 'db_sha256': after, 'dependencies': dependencies, 'helper': 'v3',
            'restore_database': True}


def rehearsed(manifest):
    return (manifest.get('schema') or {}).get('migration') == REHEARSED


def schema_digest(conn, schema='main'):
    rows = conn.execute(f"SELECT type,name,tbl_name,COALESCE(sql,'') FROM {schema}.sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows]).encode()).hexdigest()


def table_columns(conn, schema='main'):
    names = [r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
                                        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: [r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{name}")')] for name in names}


def rehearse(database, repo, python, directory, base, commit):
    """Run the candidate's own migration on a private online copy of the live database.

    Passes only for additive migrations: every original table, column and row survives, schema versions only
    grow, and integrity/foreign keys hold. The copies are removed afterwards; the live database is only read.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    original, copy = directory / 'before.sqlite3', directory / 'rehearsal.sqlite3'
    try:
        backup(database, original)
        shutil.copyfile(original, copy)
        result = subprocess.run([str(python), '-I', '-c', MIGRATE, str(repo), str(copy)], cwd=repo,
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError('Candidate migration failed on the database copy: ' + result.stderr.strip()[-500:])
        with closing(sqlite3.connect(copy)) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Migrated copy failed integrity/foreign-key validation')
            conn.execute('ATTACH DATABASE ? AS old', (str(original),))
            old, new = table_columns(conn, 'old'), table_columns(conn)
            tables = {}
            for name, columns in old.items():
                if name not in new or not set(columns) <= set(new[name]):
                    raise ValueError('Migration removes or renames existing data: ' + name)
                projection = ','.join('"' + c + '"' for c in columns)
                before = conn.execute(f'SELECT count(*) FROM old."{name}"').fetchone()[0]
                after = conn.execute(f'SELECT count(*) FROM main."{name}"').fetchone()[0]
                if after < before or conn.execute(f'SELECT count(*) FROM (SELECT {projection} FROM old."{name}" '
                                                  f'EXCEPT SELECT {projection} FROM main."{name}")').fetchone()[0]:
                    raise ValueError('Migration changes existing rows: ' + name)
                tables[name] = {'rows': before, 'added_columns': [c for c in new[name] if c not in columns]}
            versions = {key: sorted(r[0] for r in conn.execute(f'SELECT version FROM {key}.schema_version'))
                        for key in ('old', 'main')}
            if not set(versions['old']) <= set(versions['main']):
                raise ValueError('Migration removes schema versions')
            return {'passed': True, 'base': base, 'commit': commit, 'schema_before': schema_digest(conn, 'old'),
                    'schema_after': schema_digest(conn), 'versions_before': versions['old'],
                    'versions_after': versions['main'], 'tables': tables,
                    'new_tables': sorted(set(new) - set(old)), 'at': time.time()}
    finally:
        for path in (original, copy):
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(path) + suffix).unlink(missing_ok=True)


def restore(snapshot, target):
    """Replace the STOPPED live database with the verified pre-activation snapshot (rehearsed releases only)."""
    expected = sha(snapshot)
    temporary = target.with_name(target.name + '.restore')
    shutil.copyfile(snapshot, temporary)
    os.chmod(temporary, 0o600)
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    for suffix in ('-wal', '-shm'):  # Uncheckpointed pages belong to the failed candidate.
        Path(str(target) + suffix).unlink(missing_ok=True)
    os.replace(temporary, target)
    fd = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if sha(target) != expected:
        raise ValueError('Restored database differs from the pre-activation backup')
    with closing(sqlite3.connect(target)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Restored database failed integrity validation')
    return expected


def auto_guard(conn, origin, manifest, release):
    """Independent activation guard: standing policy, explicit holds and the pinned verified candidate.

    Kept in the standalone helper so a mutable running app cannot swap the approved release at cutover.
    """
    if not origin or not origin.get('auto'):
        return
    candidate = origin.get('candidate') or {}
    if (sha(release / 'manifest.json') != candidate.get('manifest_sha256')
            or manifest['commit'] != candidate.get('commit') or manifest['base'] != candidate.get('base')):
        raise ValueError('Auto-deploy exact candidate changed')
    row = conn.execute('SELECT value FROM settings WHERE key=?', ('auto_deploy:' + origin['project_id'],)).fetchone()
    project = conn.execute('SELECT status FROM projects WHERE id=?', (origin['project_id'],)).fetchone()
    if not row or not json.loads(row[0]).get('enabled') or not project or project[0] != 'active':
        raise ValueError('Auto-deploy disabled or project held')
    conn.row_factory = sqlite3.Row
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (origin['task_id'],)).fetchone()
    workflow = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_workflow:' + origin['task_id'],)).fetchone()
    if (not task or task['lease_owner'] or task['lifecycle'] != 'completed'
            or (workflow and json.loads(workflow[0]).get('state') != 'complete')):
        raise ValueError('Auto-deploy assignment is incomplete or explicitly held')
    execution = conn.execute('SELECT * FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1', (task['id'],)).fetchone()
    if (not execution or execution['id'] != origin['execution_id'] or execution['status'] != 'completed'
            or execution['stop_requested'] or execution['delivery'] != 'delivered'):
        raise ValueError('Auto-deploy verified execution changed')
    fields = {k: task[k] for k in ('id', 'project_id', 'description', 'acceptance', 'request_key')}
    context = conn.execute("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task['id'],)).fetchone()
    fields['context'] = json.loads(context[0]) if context else {}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    if (manifest.get('assignment_evidence_version') != 2 or manifest.get('assignments') != {task['id']: digest}
            or not release_attested(manifest, execution['id']) or not manifest.get('restore_database')):
        raise ValueError('Auto-deploy scope or release-check evidence changed')
    intent = conn.execute('SELECT value FROM settings WHERE key=?', ('deployment_intent:' + origin['intent_id'],)).fetchone()
    if not intent or json.loads(intent[0]).get('status') != 'dispatched':
        raise ValueError('Auto-deploy intent cancelled or not consumed')


def release_attested(manifest, execution_id):
    """Exact new scoped-gate attestation, with immutable historical full-gate compatibility."""
    common = {'execution_id': execution_id, 'commit': manifest['commit'], 'tier_checks': 'passed'}
    attestation = manifest.get('auto_deploy')
    return ((attestation == {**common, 'full_suite': 'passed'}
             and (manifest.get('verification') or {}).get('version') in (None, 1, 2)) or
            (attestation == {**common, 'release_checks': 'passed'}
             and ((manifest.get('verification') or {}).get('version') == 2
                  or ((manifest.get('verification') or {}).get('version') == 3
                      and manifest['verification'].get('tier') == 'deploy'
                      and manifest['verification'].get('full_backend') is True))))


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def run(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, timeout=180).stdout.strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def idle(conn):
    checks = {
        'unfinished_executions': 'SELECT count(*) FROM executions WHERE finished_at IS NULL',
        'leases': 'SELECT count(*) FROM tasks WHERE lease_owner IS NOT NULL',
        'queued_tasks': "SELECT count(*) FROM tasks WHERE lifecycle='queued'",
        'questions': "SELECT count(*) FROM questions WHERE status='pending'",
        'deliveries': "SELECT count(*) FROM executions WHERE delivery IN ('pending','running','waiting')",
        'streaming_messages': "SELECT count(*) FROM messages WHERE status IN ('queued','streaming')",
    }
    return {key: conn.execute(sql).fetchone()[0] for key, sql in checks.items()}


def backup(source, destination):
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or dst.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Backup failed integrity/foreign-key validation')
    os.chmod(destination, 0o600)
    with destination.open('rb') as stream:
        os.fsync(stream.fileno())
    return sha(destination)


class Handoff:
    def __init__(self, data):
        self.root = Path(data) / 'deployment-helper'

    def status(self):
        return read(self.root / 'active.json') or {'phase': 'idle'}

    def gated(self):
        return self.status()['phase'] not in TERMINAL | {'idle'}

    def request(self, release_id, origin=None):
        if not isinstance(release_id, str) or not ID.fullmatch(release_id):
            raise ValueError('Use a prepared release ID')
        manifest = read(self.root / 'releases' / release_id / 'manifest.json')
        if not manifest or manifest['id'] != release_id:
            raise ValueError('Release is not prepared')
        with lock(self.root / 'protocol.lock'):
            previous = self.status()
            if previous.get('release_id') == release_id:
                return previous  # Retry never deploys the same receipt twice.
            if self.gated():
                raise ValueError('Another deployment is pending')
            value = {'release_id': release_id, 'nonce': secrets.token_hex(16), 'phase': 'waiting_idle',
                     'requested_at': time.time(), 'origin': origin}
            atomic(self.root / 'active.json', value)
            atomic(self.root / 'results' / (value['nonce'] + '.json'), value)
            return value

    def cancel(self, nonce):
        with lock(self.root / 'protocol.lock'):
            value = self.status()
            if value.get('nonce') != nonce or value['phase'] != 'waiting_idle':
                raise ValueError('Only the exact unacknowledged request can be cancelled')
            value.update(phase='cancelled', updated_at=time.time())
            atomic(self.root / 'results' / (value['nonce']+'.json'), value)
            atomic(self.root / 'active.json', value)
            return value

    def acknowledge(self, db, busy=False):
        with lock(self.root / 'protocol.lock'):
            value = self.status()
            if value['phase'] != 'waiting_idle' or busy:
                return
            with db.connect() as conn:
                counts = idle(conn)
                if (value.get('origin') or {}).get('auto'):
                    release = self.root / 'releases' / value['release_id']
                    try:
                        auto_guard(conn, value['origin'], read(release / 'manifest.json'), release)
                    except ValueError as error:
                        value.update(phase='cancelled', error=str(error), updated_at=time.time())
                        atomic(self.root / 'results' / (value['nonce'] + '.json'), value)
                        atomic(self.root / 'active.json', value)
                        return
            if any(counts.values()):
                return
            value.update(phase='acknowledged', acknowledged_at=time.time(), idle=counts)
            atomic(self.root / 'active.json', value)

    def report(self, db):
        value = self.status()
        if value['phase'] not in TERMINAL | {'rollback_failed'} or not value.get('origin'):
            return
        origin = value['origin']
        with db.transaction() as conn:
            key = 'deployment_report:' + value['nonce']
            if conn.execute('SELECT 1 FROM settings WHERE key=?', (key,)).fetchone():
                return
            payload = {'status': value['phase'], 'receipt': str(self.root / 'results' / (value['nonce']+'.json'))}
            db.event(conn, 'deployment.result', payload, origin.get('project_id'),
                     origin.get('task_id'), origin.get('execution_id'))
            if origin.get('task_id'):
                db.event(conn, 'task.evidence', payload, origin.get('project_id'), origin['task_id'],
                         origin.get('execution_id'))
            if conn.execute('SELECT 1 FROM conversations WHERE id=?', (origin.get('conversation_id'),)).fetchone():
                text = 'Deployment complete.' if value['phase'] == 'succeeded' else 'Deployment '+value['phase']+'. Activation needs attention.'
                if origin.get('auto'):
                    text = auto_report(value)
                conn.execute("INSERT INTO messages(id,conversation_id,role,content,status,created_at) VALUES(?,?,'assistant',?,'completed',?)",
                             ('deploy-'+value['nonce'], origin['conversation_id'], text, time.time()))
            conn.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(payload)))

    def ready_to_stop(self):
        return self.status()['phase'] in {'acknowledged', 'backing_up', 'activating', 'verifying', 'rolling_back', 'rollback_failed'}


def auto_report(value):
    release, error = value['release_id'], (value.get('error') or '')[:300]
    restored = ' Code' + (', database' if (value.get('restored') or {}).get('database') else '') + (
        ' and dependencies' if (value.get('restored') or {}).get('venv') else '') + ' were restored.'
    return {
        'succeeded': f'Auto-deploy complete: {release} is live and passed post-deploy health checks.',
        'rolled_back': f'Auto-deploy of {release} failed the post-deploy health check ({error}) and was rolled back '
                       f'automatically to the previous release.{restored} It will not be retried automatically.',
        'failed': f'Auto-deploy of {release} stopped before activation ({error}). Nothing changed; it will not be '
                  'retried automatically.',
        'cancelled': f'Auto-deploy of {release} was cancelled before activation.',
        'rollback_failed': f'Auto-deploy of {release} could not roll back automatically ({error}). Admission stays '
                           'fenced; external inspection is required.',
    }[value['phase']]


def backup_workspaces(data, database, directory):
    """Quiet retained workspace bytes and all source refs; schema11 is compatible."""
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if 'workspace' not in {row[1] for row in conn.execute('PRAGMA table_info(tasks)')}:
            return {}
        tasks = conn.execute('SELECT t.*,p.path AS project_path FROM tasks t JOIN projects p ON p.id=t.project_id WHERE workspace IS NOT NULL').fetchall()
    result = {}
    repositories = {}
    for task in tasks:
        if task['lease_owner']:
            raise ValueError('Retained workspace backup needs confirmed writer cleanup')
        if not re.fullmatch('[0-9a-f]{32}', task['id']):
            raise ValueError('Workspace backup identity differs')
        path = Path(task['workspace'])
        if path != data / 'assignment-workspaces' / task['id'] or path.is_symlink() or not path.is_dir():
            raise ValueError('Retained workspace missing/changed during backup')
        archive = directory / ('workspace-' + task['id'] + '.tar')
        with tarfile.open(archive, 'w', dereference=False) as target:
            target.add(path, arcname=task['id'])
        receipt_path = path.with_suffix('.json')
        if not receipt_path.is_file():
            raise ValueError('Retained workspace receipt missing')
        saved_receipt = directory / ('workspace-' + task['id'] + '.json')
        shutil.copyfile(receipt_path, saved_receipt)
        result[task['id']] = {'workspace': str(path), 'branch': task['workspace_branch'],
                             'archive_sha256': sha(archive), 'receipt_sha256': sha(saved_receipt)}
        repositories[task['project_id']] = task['project_path']
    for project_id, path in repositories.items():
        if not re.fullmatch('[0-9a-f]{32}', project_id):
            raise ValueError('Project backup identity differs')
        bundle = directory / ('workspace-source-' + project_id + '.bundle')
        run(['git', 'bundle', 'create', str(bundle), '--all'], path)
        result['repository:' + project_id] = {'path': path, 'bundle_sha256': sha(bundle)}
    return result


class Helper:
    def __init__(self, config):
        self.config = config
        self.repo = Path(config['repo'])
        self.data = Path(config['data'])
        self.handoff = Handoff(self.data)
        self.root = self.handoff.root

    def state(self, value, phase, **extra):
        with lock(self.root / 'protocol.lock'):
            value.update(phase=phase, updated_at=time.time(), **extra)
            atomic(self.root / 'results' / (value['nonce'] + '.json'), value)
            atomic(self.root / 'active.json', value)
        return value

    def service(self, operation):
        run(['systemctl', '--user', operation, 'linko-bot-manager.service'])

    def check_external(self):
        if 'linko-bot-manager.service' in Path('/proc/self/cgroup').read_text():
            raise ValueError('Helper must run outside manager service cgroup')

    def validate(self, manifest):
        if not ID.fullmatch(manifest['id']) or not all(re.fullmatch(r'[0-9a-f]{40}', manifest[key]) for key in ('base', 'commit')):
            raise ValueError('Invalid pinned release manifest')
        release = self.root / 'releases' / manifest['id']
        if sha(release / 'validation.log') != manifest['validation_sha256']:
            raise ValueError('Validation receipt changed')
        if sha(release / 'release.bundle') != manifest['bundle_sha256']:
            raise ValueError('Prepared bundle changed')
        if run(['git', 'status', '--porcelain'], self.repo):
            raise ValueError('Live checkout has changes; refusing to overwrite')
        if run(['git', 'rev-parse', 'HEAD'], self.repo) != manifest['base']:
            raise ValueError('Live release changed; reprepare')
        run(['git', 'fetch', str(release / 'release.bundle'), manifest['commit']], self.repo)
        run(['git', 'merge-base', '--is-ancestor', manifest['base'], manifest['commit']], self.repo)
        expected_schema = schema_change(self.repo, manifest['base'], manifest['commit'])
        if manifest.get('schema') != expected_schema:
            raise ValueError('Pinned reviewed schema contract mismatch')
        if rehearsed(manifest):
            rehearsal = manifest.get('rehearsal') or {}
            if (not rehearsal.get('passed') or rehearsal.get('commit') != manifest['commit']
                    or rehearsal.get('base') != manifest['base']):
                raise ValueError('Schema/dependency release needs a passing migration rehearsal on a database copy')
            with closing(sqlite3.connect(f'file:{self.data / "manager.sqlite3"}?mode=ro', uri=True)) as conn:
                if schema_digest(conn) != rehearsal['schema_before']:
                    raise ValueError('Live database schema differs from the rehearsed copy; reprepare')
            if expected_schema['dependencies'] and not Path(self.uv()).is_file():
                raise ValueError('Dependency release needs uv for the reviewed dependency sync')

    def uv(self):
        return self.config.get('uv') or shutil.which('uv') or str(Path.home() / '.local/bin/uv')

    def venv(self):
        return Path(self.config.get('venv') or self.repo / '.venv')

    def sync_dependencies(self):
        """Install the activated commit's exact locked dependencies into the service environment."""
        subprocess.run([self.uv(), 'sync', '--frozen'], cwd=self.repo, check=True, capture_output=True, text=True,
                       timeout=600, env={**os.environ, 'UV_PROJECT_ENVIRONMENT': str(self.venv())})

    def restore_venv(self, directory):
        shutil.rmtree(self.venv(), ignore_errors=True)
        shutil.copytree(directory / 'venv', self.venv(), symlinks=True)

    def snapshot(self, directory, manifest=None):
        directory.mkdir(parents=True, mode=0o700)
        receipt = {'manager': backup(self.data / 'manager.sqlite3', directory / 'manager.sqlite3')}
        if manifest and rehearsed(manifest) and manifest['schema']['dependencies']:
            shutil.copytree(self.venv(), directory / 'venv', symlinks=True)  # Exact rollback environment.
            receipt['venv'] = str(self.venv())
        receipt['workspaces'] = backup_workspaces(self.data, directory / 'manager.sqlite3', directory)
        run(['git', 'bundle', 'create', str(directory / 'source.bundle'), 'HEAD'], self.repo)
        receipt['source'] = sha(directory / 'source.bundle')
        native = Path(self.config['native'])
        for name in ('state_5.sqlite', 'thread_history_1.sqlite'):
            if (native / name).exists():
                receipt[name] = backup(native / name, directory / name)
        with sqlite3.connect(directory / 'manager.sqlite3') as conn:
            threads = {r[0] for r in conn.execute('SELECT thread_id FROM executions UNION SELECT thread_id FROM conversations UNION SELECT native_thread FROM tasks') if r[0]}
        # Claude provider sessions have application history plus CLI resume JSONL;
        # do not misclassify their UUIDs as missing Codex rollout references.
        claude_root = self.data / 'claude-threads'
        claude_native = Path(self.config.get('claude_native', Path.home() / '.claude'))
        codex_threads = set(threads)
        receipt['claude_threads'], receipt['claude_sessions'] = {}, {}
        for thread in sorted(threads):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', thread):
                raise ValueError('Native thread identity is not a safe backup filename')
            path = claude_root / (thread + '.json')
            if not path.exists():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(claude_root.resolve()):
                raise ValueError('Claude history path changed')
            saved = read(path)
            if saved.get('id') != thread:
                raise ValueError('Claude history identity changed')
            codex_threads.remove(thread)
            target = directory / 'claude-threads' / path.name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.copyfile(path, target)
            if target.read_bytes() != path.read_bytes():
                raise ValueError('Claude history changed during backup')
            receipt['claude_threads'][str(path)] = sha(target)
            if saved.get('session'):
                matches = list((claude_native / 'projects').rglob(thread + '.jsonl'))
                if len(matches) != 1 or matches[0].is_symlink() or not matches[0].resolve().is_relative_to(claude_native.resolve()):
                    raise ValueError('Claude resume session missing/ambiguous; review configured claude_native root')
                source = matches[0]
                target = directory / 'claude-sessions' / source.name
                target.parent.mkdir(mode=0o700, exist_ok=True)
                shutil.copyfile(source, target)
                with source.open('rb') as original:
                    if original.read(target.stat().st_size) != target.read_bytes():
                        raise ValueError('Claude resume session changed during backup')
                receipt['claude_sessions'][str(source)] = sha(target)
        files = []
        if codex_threads:
            with sqlite3.connect(f'file:{native / "state_5.sqlite"}?mode=ro', uri=True) as conn:
                files = [r[0] for thread in codex_threads for r in conn.execute('SELECT rollout_path FROM threads WHERE id=?', (thread,))]
        if len(files) != len(codex_threads):
            raise ValueError('Native thread backup has missing references')
        receipt['rollouts'] = {}
        for index, filename in enumerate(files):
            path = Path(filename).resolve()
            if not path.is_relative_to(native.resolve()) or not path.is_file():
                raise ValueError('Native rollout unavailable/outside configured root')
            target = directory / f'rollout-{index}.jsonl'
            shutil.copyfile(path, target)
            with path.open('rb') as original:
                if original.read(target.stat().st_size) != target.read_bytes():
                    raise ValueError('Native rollout changed during backup')
            with target.open('rb') as stream:
                os.fsync(stream.fileno())
            receipt['rollouts'][str(path)] = sha(target)
        # Unit/environment backups stay private; never print their contents.
        for index, filename in enumerate(self.config['preserve_files']):
            path = Path(filename)
            if path.exists():
                shutil.copyfile(path, directory / f'config-{index}')
                receipt[str(path)] = sha(directory / f'config-{index}')
        for path in directory.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
        atomic(directory / 'receipt.json', receipt)
        return receipt

    def http(self, path, cookie=None, base=None):
        request = urllib.request.Request((base or self.config['loopback']) + path)
        if cookie:
            request.add_header('Cookie', 'ai_manager_session=' + cookie)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def verify(self, manifest, snapshot):
        if run(['systemctl', '--user', 'is-active', 'linko-bot-manager.service']) != 'active':
            raise ValueError('Manager is not active')
        for base in (self.config['loopback'], self.config['https']):
            status, body = self.http('/api/auth-status', base=base)
            info = json.loads(body)
            if status != 200 or info['release_commit'] != manifest['commit'] or not info['scheduler_running']:
                raise ValueError('Release/scheduler health mismatch')
            if self.http('/api/portfolio', base=base)[0] != 401:
                raise ValueError('Authentication boundary failed')
            for name, digest in manifest['assets'].items():
                status, body = self.http('/static/' + name, base=base)
                if status != 200 or hashlib.sha256(body).hexdigest() != digest:
                    raise ValueError('Served asset mismatch: ' + name)
        with sqlite3.connect(self.data / 'manager.sqlite3') as conn, sqlite3.connect(snapshot) as old:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Production integrity failed')
            for table in CRITICAL:
                columns = [row[1] for row in old.execute('PRAGMA table_info(' + table + ')')]
                current_columns = [row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')]
                if current_columns != columns and not (rehearsed(manifest) and set(columns) <= set(current_columns)):
                    if table != 'tasks' or manifest.get('schema') != PARALLEL_SCHEMA or current_columns != columns + ['workspace', 'workspace_branch', 'workspace_base']:
                        raise ValueError('Unexpected preserved table schema: ' + table)
                    if conn.execute('SELECT 1 FROM tasks WHERE workspace IS NOT NULL OR workspace_branch IS NOT NULL OR workspace_base IS NOT NULL').fetchone():
                        raise ValueError('Existing workspace identity changed during migration')
                projection = ','.join('"' + column + '"' for column in columns)
                before = set(old.execute('SELECT ' + projection + ' FROM ' + table).fetchall())
                after = set(conn.execute('SELECT ' + projection + ' FROM ' + table).fetchall())
                if not before <= after:
                    raise ValueError('Preserved data changed: ' + table)
            for key in ('global_instructions', 'model_routes', 'model_settings', 'model_settings:claude', 'model_provider',
                        'max_workers', 'archived_tasks', 'global_last_used_at'):
                query = 'SELECT value FROM settings WHERE key=?'
                if old.execute(query, (key,)).fetchone() != conn.execute(query, (key,)).fetchone():
                    raise ValueError('Protected setting changed: ' + key)
            query = 'SELECT id,name,path,repository,agent_id,display_name,last_used_at FROM projects'
            if not set(old.execute(query).fetchall()) <= set(conn.execute(query).fetchall()):
                raise ValueError('Project identity/recency changed')
            old_versions = set(old.execute('SELECT * FROM schema_version').fetchall())
            versions = set(conn.execute('SELECT * FROM schema_version').fetchall())
            if rehearsed(manifest):
                # Activation must reach exactly the rehearsed schema; a restoring rollback the original one.
                stage = 'before' if manifest.get('rollback') else 'after'
                if (schema_digest(conn) != manifest['rehearsal']['schema_' + stage]
                        or {v[0] for v in versions} != set(manifest['rehearsal']['versions_' + stage])):
                    raise ValueError('Live schema differs from the rehearsed migration')
            elif versions != old_versions and not (manifest.get('schema') == PARALLEL_SCHEMA and versions == old_versions | {(12,)}):
                raise ValueError('Unexpected schema migration')
            if manifest.get('schema') == PARALLEL_SCHEMA:
                indices = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND name IN ('project_worker','workspace_worker')"))
                if indices.get('project_worker') != "CREATE UNIQUE INDEX project_worker ON tasks(project_id) WHERE lifecycle='running' AND workspace IS NULL" or indices.get('workspace_worker') != 'CREATE UNIQUE INDEX workspace_worker ON tasks(workspace) WHERE lease_owner IS NOT NULL AND workspace IS NOT NULL':
                    raise ValueError('Workspace claim index contract mismatch')
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute('INSERT INTO sessions VALUES(?,?,?)', (digest, secrets.token_hex(16), time.time()+60))
            conn.commit()
            try:
                if self.http('/api/portfolio', token)[0] != 200:
                    raise ValueError('Authenticated health failed')
            finally:
                conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
                conn.commit()
        return {'commit': manifest['commit'], 'assets': manifest['assets'], 'auth': True, 'data': True,
                'service': run(['systemctl', '--user', 'show', 'linko-bot-manager.service',
                                '--property=MainPID,ActiveEnterTimestamp,ControlGroup'])}

    def execute(self):
        self.check_external()
        with lock(self.root / 'helper.lock'):
            value = self.handoff.status()
            if value['phase'] in TERMINAL | {'idle', 'waiting_idle'}:
                return value
            # A helper crash during mutation stays fenced. Never infer successful activation.
            if value['phase'] == 'rollback_failed':
                return value
            if value['phase'] != 'acknowledged':
                return self.state(value, 'rollback_failed', error='Interrupted deployment; external inspection required')
            manifest = read(self.root / 'releases' / value['release_id'] / 'manifest.json')
            stopped = changed = False
            try:
                self.validate(manifest)
                with sqlite3.connect(f'file:{self.data / "manager.sqlite3"}?mode=ro', uri=True) as conn:
                    auto_guard(conn, value.get('origin'), manifest, self.root / 'releases' / manifest['id'])
                    if any(idle(conn).values()):
                        raise ValueError('Idle acknowledgement no longer valid')
                directory = self.root / 'backups' / value['nonce']
                self.state(value, 'backing_up')
                self.snapshot(directory, manifest)
                # Recheck acknowledgement and persistent idle immediately before stopping.
                if self.handoff.status().get('nonce') != value['nonce']:
                    raise ValueError('Handoff changed')
                with sqlite3.connect(self.data / 'manager.sqlite3') as conn:
                    auto_guard(conn, value.get('origin'), manifest, self.root / 'releases' / manifest['id'])
                    if any(idle(conn).values()):
                        raise ValueError('Work appeared before stop')
                stopped = True
                self.service('stop')
                with sqlite3.connect(self.data / 'manager.sqlite3') as conn:
                    auto_guard(conn, value.get('origin'), manifest, self.root / 'releases' / manifest['id'])
                    if any(idle(conn).values()):
                        raise ValueError('Work appeared at cutover')
                self.validate(manifest)
                backup(self.data / 'manager.sqlite3', directory / 'final.sqlite3')
                self.state(value, 'activating', backup=str(directory))
                changed = True
                run(['git', 'merge', '--ff-only', manifest['commit']], self.repo)
                if rehearsed(manifest) and manifest['schema']['dependencies']:
                    self.sync_dependencies()
                self.service('start')
                self.state(value, 'verifying')
                health = self.wait_health(manifest, directory / 'final.sqlite3')
                return self.state(value, 'succeeded', health=health)
            except Exception as error:
                if stopped:
                    self.state(value, 'rolling_back', error=str(error))
                    restored = {}
                    try:
                        self.service('stop')
                        if changed:
                            # Checkout refuses dirty changes and preserves the forward main branch.
                            run(['git', 'checkout', '--detach', manifest['base']], self.repo)
                            if rehearsed(manifest) or manifest.get('restore_database'):
                                # The stopped final snapshot predates every candidate write; admission was fenced.
                                restored['database'] = restore(directory / 'final.sqlite3', self.data / 'manager.sqlite3')
                                if (manifest.get('schema') or {}).get('dependencies'):
                                    self.restore_venv(directory)
                                    restored['venv'] = str(self.venv())
                        self.service('start')
                        old = {**manifest, 'commit': manifest['base'], 'assets': manifest['base_assets'], 'rollback': True}
                        self.wait_health(old, directory / 'final.sqlite3' if (directory / 'final.sqlite3').exists()
                                         else directory / 'manager.sqlite3')
                        return self.state(value, 'rolled_back', error=str(error), **({'restored': restored} if restored else {}))
                    except Exception as rollback_error:
                        return self.state(value, 'rollback_failed', error=str(error), rollback_error=str(rollback_error))
                return self.state(value, 'failed', error=str(error))

    def wait_health(self, manifest, snapshot):
        deadline = time.monotonic() + 45
        while True:
            try:
                return self.verify(manifest, snapshot)
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    helper = Helper(read(args.config))
    while True:
        try:
            helper.execute()
        except Exception as error:
            # Lock contention is normal; keep the service alive without touching the request.
            print(type(error).__name__ + ': ' + str(error), flush=True)
        if args.once:
            break
        time.sleep(2)


if __name__ == '__main__':
    main()

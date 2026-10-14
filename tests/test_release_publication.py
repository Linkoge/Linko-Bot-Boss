"""Real immutable import, ownership/lifecycle and evidence binding regressions."""
import json
from pathlib import Path

import pytest
from test_verification import git
from test_verification import system as system_fixture

from ai_manager.deployment import Handoff, atomic, lock, sha
from ai_manager.deployment_intent import assignment_evidence
from ai_manager.release_publication import inventory, publish
from ai_manager.tasks import Tasks

system = system_fixture


@pytest.fixture
def release(system):
    db, registry, _, _, workers, _ = system
    project = registry.list()[0]
    repo = Path(project['path'])
    (repo / '.gitignore').write_text('.data/\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'ignore local artifacts')
    base = git(repo, 'rev-parse', 'HEAD')
    task = Tasks(db).add(project['id'], 'Change color', selected={'constraints': ['Keep shape']})
    claimed = Tasks(db).claim(2)
    workspace = Path(claimed['workspace'])
    (workspace / 'style.css').write_text('a{color:red}')
    git(workspace, 'commit', '-am', 'red')
    commit = git(workspace, 'rev-parse', 'HEAD')
    workers.finish(claimed['lease_owner'], 'completed', 'validated', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
    source = workspace / '.data/release'
    source.mkdir(parents=True)
    git(workspace, 'bundle', 'create', str(source / 'release.bundle'), 'HEAD')
    (source / 'validation.log').write_text('complete validation evidence\n')
    with db.connect() as conn:
        digest = assignment_evidence(conn, conn.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone())
    atomic(source / 'manifest.json', {'id': 'candidate-1', 'base': base, 'commit': commit, 'assets': {},
                                    'base_assets': {}, 'assignments': {task['id']: digest},
                                    'assignment_evidence_version': 2,
                                    'bundle_sha256': sha(source / 'release.bundle'),
                                    'validation_sha256': sha(source / 'validation.log')})
    atomic(source / 'evidence-index.json', {'release_id': 'candidate-1', 'candidate': commit, 'base': base,
                                          'files': {p.name: sha(p) for p in source.iterdir()}})
    atomic(Handoff(db.path.parent).root / 'config.json', {'repo': str(repo)})
    return db, task['id'], source, repo, workspace


def register(release):
    db, task, source, _, _ = release
    return publish(db, source, sha(source / 'manifest.json'), sha(source / 'evidence-index.json'), task)


def test_publication_waits_for_cleanup_then_registers_exactly_once(release):
    db, task, source, _, _ = release
    db.set_setting('assignment_integration', {'task_id': task})
    with pytest.raises(ValueError, match='cleanup'):
        register(release)
    # Simulate the normal cleanup boundary; publisher never deletes reservations.
    with db.transaction() as conn:
        conn.execute("DELETE FROM settings WHERE key='assignment_integration'")
    first = register(release)
    target = Path(first['path'])
    assert inventory(target, sha(source / 'manifest.json'), sha(source / 'evidence-index.json')) == inventory(
        source, sha(source / 'manifest.json'), sha(source / 'evidence-index.json'))
    assert register(release)['reused']
    assert len(db.all("SELECT * FROM events WHERE kind='release.published'")) == 1
    assert Handoff(db.path.parent).status()['phase'] == 'idle'
    assert not db.all("SELECT * FROM settings WHERE key LIKE 'deployment_intent:%'")


@pytest.mark.parametrize('fault', ['lease', 'held', 'incomplete', 'scope', 'base', 'index', 'bundle', 'symlink',
                                  'reservation', 'pending', 'lock', 'wrong_branch', 'candidate'])
def test_publication_guards(release, fault):
    db, task, source, repo, workspace = release
    with db.transaction() as conn:
        if fault == 'lease':
            conn.execute("UPDATE tasks SET lease_owner='owned' WHERE id=?", (task,))
        elif fault == 'incomplete':
            conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task,))
        elif fault == 'scope':
            conn.execute("UPDATE tasks SET acceptance='different scope' WHERE id=?", (task,))
        elif fault == 'held':
            conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps({'state': 'owner_wait'}),
                                                                      'assignment_workflow:' + task))
    if fault == 'base':
        (repo / 'style.css').write_text('changed live')
    elif fault == 'index':
        (source / 'evidence-index.json').write_text('{}')
    elif fault == 'bundle':
        (source / 'release.bundle').write_bytes(b'bad')
    elif fault == 'symlink':
        (source / 'link').symlink_to(repo / 'style.css')
    elif fault == 'reservation':
        db.set_setting('release_preparation', {'pid': 1})
    elif fault == 'pending':
        atomic(Handoff(db.path.parent).root / 'active.json', {'phase': 'waiting_idle'})
    elif fault == 'wrong_branch':
        git(workspace, 'checkout', '-b', 'wrong')
    elif fault == 'candidate':
        (workspace / 'style.css').write_text('changed')
    if fault == 'lock':
        with lock(Handoff(db.path.parent).root / 'preparation.lock'), pytest.raises(BlockingIOError):
            register(release)
    else:
        with pytest.raises(ValueError):
            register(release)
    assert not (Handoff(db.path.parent).root / 'releases/candidate-1').exists()


def test_import_retry_never_overwrites_mutated_registration(release):
    target = Path(register(release)['path'])
    (target / 'validation.log').write_text('changed')
    with pytest.raises(ValueError, match='checksum'):
        register(release)
    assert (target / 'validation.log').read_text() == 'changed'


def test_v2_binding_ignores_resume_receipts_but_not_scope(release):
    db, task, _, _, _ = release
    with db.transaction() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        before = assignment_evidence(conn, row)
        legacy = assignment_evidence(conn, row, 1)
        db.event(conn, 'manager.continued', {'call_id': 'handoff'}, row['project_id'], task)
        db.event(conn, 'task.context', {'constraints': ['Keep shape']}, row['project_id'], task)
        assert assignment_evidence(conn, row) == before
        assert assignment_evidence(conn, row, 1) != legacy
        db.event(conn, 'task.context', {'constraints': ['New requirement']}, row['project_id'], task)
        assert assignment_evidence(conn, row) != before
    with pytest.raises(ValueError, match='scope/evidence'):
        register(release)


@pytest.mark.asyncio
async def test_manager_publishes_without_continuing_task(release, system):
    db, task, source, _, _ = release
    scheduler = system[-1]
    project = db.one('SELECT project_id FROM tasks WHERE id=?', (task,))['project_id']
    before = db.one('SELECT description,acceptance,revision FROM tasks WHERE id=?', (task,))
    params = {'tool': 'manager_action', 'callId': 'publication', 'manager_execution_id': 'publication',
              'mode': 'auto', 'internal': True, 'arguments': {'action': 'publish_release', 'task_id': task,
              'release_id': 'candidate-1', 'path': str(source), 'manifest_sha256': sha(source / 'manifest.json'),
              'index_sha256': sha(source / 'evidence-index.json')}}
    with pytest.raises(ValueError, match='read-only'):
        await scheduler.manager_action(project, {**params, 'mode': 'conversation'})
    result = await scheduler.manager_action(project, params)
    assert result['phase'] == 'registered'
    assert db.one('SELECT description,acceptance,revision FROM tasks WHERE id=?', (task,)) == before
    assert not db.all("SELECT * FROM events WHERE kind='manager.continued'")


def test_legacy_import_keeps_original_hash_rules(release):
    db, task, source, _, _ = release
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest.pop('assignment_evidence_version')
    with db.connect() as conn:
        manifest['assignments'][task] = assignment_evidence(conn, conn.execute(
            'SELECT * FROM tasks WHERE id=?', (task,)).fetchone(), 1)
    atomic(source / 'manifest.json', manifest)
    index = json.loads((source / 'evidence-index.json').read_text())
    index['files']['manifest.json'] = sha(source / 'manifest.json')
    atomic(source / 'evidence-index.json', index)
    assert register(release)['phase'] == 'registered'
    with db.transaction() as conn:
        db.event(conn, 'manager.continued', {'call_id': 'new'}, task_id=task)
    with pytest.raises(ValueError, match='scope/evidence'):
        register(release)


def test_supported_cli_import_uses_existing_database_without_migration(release):
    import os
    import subprocess
    import sys

    db, task, source, _, _ = release
    root = Path(__file__).resolve().parents[1]
    before = db.all('SELECT * FROM schema_version')
    result = subprocess.run([sys.executable, 'scripts/publish_release.py', '--data', str(db.path.parent),
                             '--source', str(source), '--task-id', task,
                             '--manifest-sha256', sha(source / 'manifest.json'),
                             '--index-sha256', sha(source / 'evidence-index.json')],
                            cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
                            capture_output=True, text=True, check=True)
    assert 'registered' in result.stdout
    assert db.all('SELECT * FROM schema_version') == before
    assert Handoff(db.path.parent).status()['phase'] == 'idle'


def test_interrupted_copy_does_not_register_partial_release(release, monkeypatch):
    import ai_manager.release_publication as module

    db = release[0]
    monkeypatch.setattr(module.shutil, 'copyfileobj', lambda *args: (_ for _ in ()).throw(OSError('copy failed')))
    with pytest.raises(OSError, match='copy failed'):
        register(release)
    store = Handoff(db.path.parent).root / 'releases'
    assert not (store / 'candidate-1').exists()
    assert not list(store.glob('.publication-*'))
    assert not db.all("SELECT * FROM events WHERE kind='release.published'")


def test_intent_candidate_pins_complete_published_index(release):
    from ai_manager.deployment_intent import Intents

    db = release[0]
    target = Path(register(release)['path'])
    candidate, _ = Intents(db).candidate('candidate-1')
    assert candidate['index_sha256'] == sha(target / 'evidence-index.json')
    (target / 'validation.log').write_text('tampered')
    with pytest.raises(ValueError):
        Intents(db).candidate('candidate-1')


def test_inventory_includes_nested_index_files(release):
    _, _, source, _, _ = release
    nested = source / 'provenance/evidence-index.json'
    nested.parent.mkdir()
    nested.write_text('nested baseline index')
    index = json.loads((source / 'evidence-index.json').read_text())
    index['files']['provenance/evidence-index.json'] = sha(nested)
    atomic(source / 'evidence-index.json', index)
    assert register(release)['phase'] == 'registered'

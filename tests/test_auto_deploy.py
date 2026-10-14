"""Isolated owner request → tier evidence → real preparation → activation/DB rollback.

The throwaway candidate has a tiny real pytest suite; only its release check list and the provider
review are replaced. The final repository release gate runs the full application suites separately.
Systemd is replaced by a real disposable HTTP server. No shared runtime writes or activation.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fake_codex import FakeServer
from test_deployment_intent import call, finish_owner, owner
from test_rollback_rehearsal import SERVER, git, served
from test_rollback_rehearsal import rehearsal as rehearsal_fixture

from ai_manager.auto_deploy import AutoDeploy
from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.deployment import Helper, atomic, read, sha
from ai_manager.deployment_intent import rows
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.workers import Workers
from scripts import prepare_deployment

rehearsal = rehearsal_fixture

BASE_DB = '''import sqlite3
class Database:
    def __init__(self, path):
        with sqlite3.connect(path) as conn:
            conn.execute('SELECT 1')
'''
MIGRATION = BASE_DB.replace("conn.execute('SELECT 1')", "conn.execute('CREATE TABLE IF NOT EXISTS candidate_data(value TEXT)')\n            conn.execute('INSERT OR IGNORE INTO schema_version VALUES(13)')")


@pytest.fixture
def automatic(rehearsal, monkeypatch):
    live, data, db, systemd, port = rehearsal
    (live / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (live / 'ai_manager/__init__.py').write_text('')
    (live / 'ai_manager/db.py').write_text(BASE_DB)
    (live / 'ai_manager/deployment.py').write_bytes((Path(__file__).parents[1] / 'ai_manager/deployment.py').read_bytes())
    (live / 'test_candidate.py').write_text('import pytest\n@pytest.mark.smoke\ndef test_candidate():\n    assert 1 + 1 == 2\n')
    git(live, 'add', '.')
    git(live, 'commit', '-m', 'base migration contract')
    # The existing fixture's server reports its earlier HEAD until restarted.
    systemd.stop()
    systemd('systemctl --user start linko-bot-manager.service'.split())
    settings = Settings(data_dir=data, project_roots=(live.parent,))
    registry = Registry(db, (live.parent,))
    project = registry.add(live, name='Linko-Bot-Boss')['id']
    chat = Chat(db, registry, FakeServer(settings))
    chat.launch = lambda *args: None
    workers = Workers(db, registry, settings, lambda s, native=False: FakeServer(s))
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    atomic(scheduler.deployment.root / 'config.json', {'repo': str(live)})
    scheduler.auto_deploy.set(project, True)
    monkeypatch.setattr(prepare_deployment, 'checks', lambda python: [[str(python), '-m', 'pytest', '-q']])
    async def prepare(checkout, live, data, release_id, task_id):
        execution = db.one('SELECT id FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1', (task_id,))['id']
        return await asyncio.to_thread(prepare_deployment.prepare, checkout, live, data, release_id,
                                       Path(sys.executable), [task_id], execution)
    scheduler.auto_deploy.prepare = prepare
    return db, registry, chat, workers, scheduler, project, live, port, systemd


async def complete(automatic, healthy=True, migration=True, deployment=None):
    db, _, chat, workers, scheduler, project, live, _, _ = automatic
    params = await owner(chat, project, 'Implement my requested improvement and deploy verified result')
    deployment_args = {}
    if deployment == 'quote':
        deployment_args['owner_deployment_quote'] = 'deploy verified result'
    elif deployment in {'explicit', 'discovered'}:
        intent = scheduler.intents.record(project, params, 'deploy verified result',
                                          task_request=params['manager_execution_id'] + ':' + project)
        if deployment == 'explicit':
            deployment_args['intent_id'] = intent['id']
    task = await call(scheduler, project, params, 'delegate', objective='Implement requested improvement',
                      change_paths=['ai_manager/', 'server.py', 'test_candidate.py'], **deployment_args)
    finish_owner(db, params)
    claim = scheduler.tasks.claim(2)
    assert claim and claim['workspace']
    workspace = Path(claim['workspace'])
    if migration:
        (workspace / 'ai_manager/db.py').write_text(MIGRATION)
    code = SERVER.replace('HEALTHY = True', 'HEALTHY = ' + str(healthy))
    # Migration runs on service startup. Runtime writes happen after rehearsal, so rollback must restore data.
    code = code.replace('HEALTHY = ', "import sys\nsys.path.insert(0, str(ROOT))\nfrom ai_manager.db import Database\nDatabase(os.environ['DB'])\nwith sqlite3.connect(os.environ['DB']) as conn:\n    conn.execute(\"INSERT OR REPLACE INTO settings VALUES('candidate-runtime', '\\\"written\\\"')\")\nHEALTHY = ", 1)
    (workspace / 'server.py').write_text(code)
    (workspace / 'ai_manager/static/app.js').write_text('console.log("candidate")\n')
    git(workspace, 'add', '.')
    git(workspace, 'commit', '-m', 'owner improvement')
    async def review(*args):
        return {'status': 'completed', 'findings': 'No blocking findings. Fixture review.'}
    workers.review = review
    await workers.verify(claim, claim['lease_owner'], workspace, {})
    workers.finish(claim['lease_owner'], 'completed', 'Improvement complete; checks passed', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
        conn.execute("UPDATE executions SET delivery='delivered' WHERE id=?", (claim['lease_owner'],))
        from ai_manager.assignment_workflow import update
        from ai_manager.parallel import retain_source
        retain_source(db, conn, task['id'])
        update(db, conn, task['id'], 'complete', 'Verified finished assignment')
    return task, claim['lease_owner'], params


async def prepare_and_dispatch(automatic):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    pipeline = scheduler.auto_deploy
    pipeline.step()
    assert pipeline.busy()
    await pipeline.job
    with db.connect() as conn:
        intent = next(v for v in rows(conn) if v.get('kind') == 'auto')
    assert intent['auto']['state'] == 'prepared', intent
    release = scheduler.deployment.root / 'releases' / intent['auto']['release_id']
    # External upgrade receipt simulated in this disposable runtime only.
    (scheduler.deployment.root / 'helper-v3.py').write_bytes((release / 'helper-v3.py').read_bytes())
    atomic(scheduler.deployment.root / 'helper-v3-upgrade.json', {'phase': 'succeeded'})
    pipeline.step()
    await pipeline.job
    assert scheduler.deployment.status()['phase'] == 'waiting_idle'
    return intent, release


@pytest.mark.parametrize('healthy,migration', [(True, True), (False, True), (False, False)])
async def test_owner_work_auto_preparation_activation_and_code_database_rollback(automatic, healthy, migration):
    db, _, chat, _, scheduler, project, live, port, systemd = automatic
    task, execution, params = await complete(automatic, healthy, migration)
    # Unrelated owner chat is neither a correction to this task nor a consent revocation.
    later = await owner(chat, project, 'What time is it?', mode='conversation')
    finish_owner(db, later)
    intent, release = await prepare_and_dispatch(automatic)
    assert not scheduler.intents.list(project)[0]['needs_owner_review']
    manifest = read(release / 'manifest.json')
    assert manifest['auto_deploy']['execution_id'] == execution
    assert manifest['assignments'] and manifest['restore_database']
    if migration:
        assert manifest['rehearsal']['passed'] and 13 in manifest['rehearsal']['versions_after']
    scheduler.deployment.acknowledge(db)
    assert scheduler.deployment.status()['phase'] == 'acknowledged'
    helper = Helper({'repo': str(live), 'data': str(db.path.parent), 'native': str(db.path.parent / 'native'),
                     'preserve_files': [], 'loopback': f'http://127.0.0.1:{port}', 'https': f'http://localhost:{port}'})
    result = helper.execute()
    receipt = read(scheduler.deployment.root / 'results' / (result['nonce'] + '.json'))
    assert result == receipt
    assert result['origin']['owner_message_id'] == intent['source_message_id']
    if healthy:
        assert result['phase'] == 'succeeded', result
        assert git(live, 'rev-parse', 'HEAD') == manifest['commit']
        assert db.setting('candidate-runtime') == 'written'
        assert served(port)['release_commit'] == manifest['commit']
    else:
        assert result['phase'] == 'rolled_back', result
        assert git(live, 'rev-parse', 'HEAD') == manifest['base']
        assert git(live, 'rev-parse', 'main') == manifest['commit']
        assert db.setting('candidate-runtime') is None
        assert db.one("SELECT name FROM sqlite_master WHERE name='candidate_data'") is None
        assert result['restored']['database'] == sha(scheduler.deployment.root / 'backups' / result['nonce'] / 'final.sqlite3')
        assert served(port)['release_commit'] == manifest['base']
    scheduler.deployment.report(db)
    scheduler.deployment.report(db)
    assert len(db.all("SELECT * FROM events WHERE kind='deployment.result'")) == 1
    assert any('Auto-deploy' in m['content'] for m in db.all('SELECT content FROM messages'))
    operations = list(systemd.operations)
    scheduler.auto_deploy.step()
    assert helper.execute()['phase'] == result['phase']
    assert systemd.operations == operations  # Failure never retries.
    assert task['id'] == result['origin']['task_id']


@pytest.mark.parametrize('reason', ['review', 'tier', 'missing_record', 'wrong_commit', 'dirty'])
async def test_unverified_work_never_prepares(automatic, reason):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    task, execution, _ = await complete(automatic)
    with db.transaction() as conn:
        row = conn.execute("SELECT seq,payload FROM events WHERE execution_id=? AND kind='task.verification'", (execution,)).fetchone()
        record = json.loads(row['payload'])
        if reason == 'missing_record':
            conn.execute('DELETE FROM events WHERE seq=?', (row['seq'],))
        else:
            if reason == 'review':
                record['review']['findings'] = 'Blocking data issue'
            if reason == 'tier':
                record['escalated'] = True
            if reason == 'wrong_commit':
                record['commit'] = '0' * 40
            conn.execute('UPDATE events SET payload=? WHERE seq=?', (json.dumps(record), row['seq']))
    if reason == 'dirty':
        (Path(scheduler.tasks.get(task['id'])['workspace']) / 'dirty.txt').write_text('unverified')
    scheduler.auto_deploy.step()
    assert not scheduler.auto_deploy.busy()
    assert scheduler.deployment.status()['phase'] == 'idle'
    assert not (scheduler.deployment.root / 'releases').exists()


@pytest.mark.parametrize('failure', ['full_suite', 'rehearsal'])
async def test_failed_verification_or_rehearsal_never_deploys_or_retries(automatic, failure, monkeypatch):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    task, execution, _ = await complete(automatic)
    if failure == 'full_suite':
        monkeypatch.setattr(prepare_deployment, 'checks', lambda python: [[str(python), '-c', 'raise SystemExit(1)']])
    else:
        def fail(*args):
            raise ValueError('Migration rehearsal failed')
        monkeypatch.setattr(prepare_deployment, 'rehearse', fail)
    scheduler.auto_deploy.step()
    await scheduler.auto_deploy.job
    with db.connect() as conn:
        intent = next(v for v in rows(conn) if v.get('kind') == 'auto')
    assert intent['status'] == 'failed'
    scheduler.auto_deploy.step()
    assert not scheduler.auto_deploy.busy()
    assert scheduler.deployment.status()['phase'] == 'idle'
    assert not list((scheduler.deployment.root / 'releases').glob('*/manifest.json'))
    assert task and execution


@pytest.mark.parametrize('hold', ['off', 'project_pause', 'scope', 'stop', 'manifest'])
async def test_pending_cutover_respects_off_holds_and_exact_candidate(automatic, hold):
    db, registry, _, _, scheduler, project, _, _, _ = automatic
    task, _, _ = await complete(automatic)
    intent, release = await prepare_and_dispatch(automatic)
    if hold == 'off':
        scheduler.auto_deploy.set(project, False)
    elif hold == 'project_pause':
        registry.update(project, registry.get(project)['revision'], {'status': 'paused'})
    elif hold == 'stop':
        with db.transaction() as conn:
            from ai_manager.deployment_intent import revoke
            revoke(db, conn, 'Owner stopped this assignment', task_id=task['id'])
    elif hold == 'scope':
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET description=description||' actual scope change' WHERE id=?", (task['id'],))
    else:
        value = read(release / 'manifest.json')
        value['commit'] = 'a' * 40
        atomic(release / 'manifest.json', value)
    scheduler.deployment.acknowledge(db)
    assert scheduler.deployment.status()['phase'] == 'cancelled'
    assert intent['id']


async def test_off_manual_flow_and_persistent_seed(automatic):
    db, registry, chat, _, scheduler, project, _, _, _ = automatic
    scheduler.auto_deploy.seed()
    assert next(v for v in scheduler.auto_deploy.settings() if v['project_id'] == project)['enabled']
    scheduler.auto_deploy.set(project, False)
    reopened = AutoDeploy(Database(db.path), registry, scheduler.deployment, scheduler.intents)
    reopened.seed()
    assert not next(v for v in reopened.settings() if v['project_id'] == project)['enabled']
    params = await owner(chat, project, 'Do requested work')
    await call(scheduler, project, params, 'delegate', objective='Do requested work', change_paths=['file'])
    with db.connect() as conn:
        assert not [v for v in rows(conn) if v.get('kind') == 'auto']
    assert scheduler.deployment.status()['phase'] == 'idle'


async def test_prepared_waits_for_bootstrap_without_repeating_checks(automatic):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    await complete(automatic)
    scheduler.auto_deploy.step()
    await scheduler.auto_deploy.job
    scheduler.auto_deploy.step()
    assert not scheduler.auto_deploy.busy() and scheduler.deployment.status()['phase'] == 'idle'
    with db.connect() as conn:
        intent = next(v for v in rows(conn) if v.get('kind') == 'auto')
    assert intent['auto']['state'] == 'prepared'
    assert intent['auto']['reason'] == 'helper v3 bootstrap required'
    release = scheduler.deployment.root / 'releases' / intent['auto']['release_id']
    digest = sha(release / 'validation.log')
    scheduler.auto_deploy.step()
    assert sha(release / 'validation.log') == digest


async def test_durable_work_intent_survives_chat_cleanup_and_restart(automatic):
    db, registry, _, _, scheduler, _, _, _, _ = automatic
    await complete(automatic)
    with db.transaction() as conn:
        intent = next(v for v in rows(conn) if v.get('kind') == 'auto')
        conn.execute('DELETE FROM messages WHERE id=?', (intent['source_message_id'],))
    scheduler.auto_deploy = AutoDeploy(Database(db.path), registry, scheduler.deployment, scheduler.intents,
                                       prepare=scheduler.auto_deploy.prepare)
    scheduler.auto_deploy.recover()
    await prepare_and_dispatch(automatic)
    assert scheduler.deployment.status()['phase'] == 'waiting_idle'


async def test_interrupted_preparation_is_failed_and_not_retried(automatic):
    db, registry, _, _, scheduler, _, _, _, _ = automatic
    await complete(automatic)
    with db.transaction() as conn:
        value = next(v for v in rows(conn) if v.get('kind') == 'auto')
        scheduler.auto_deploy.mark(conn, value, 'preparing')
    reopened = AutoDeploy(Database(db.path), registry, scheduler.deployment, scheduler.intents)
    reopened.recover()
    reopened.step()
    with db.connect() as conn:
        assert next(v for v in rows(conn) if v.get('kind') == 'auto')['status'] == 'failed'
    assert not reopened.busy() and scheduler.deployment.status()['phase'] == 'idle'


async def test_actual_destructive_migration_fails_copy_without_changing_runtime(automatic):
    db, _, _, workers, scheduler, _, _, _, _ = automatic
    task, execution, _ = await complete(automatic)
    workspace = Path(scheduler.tasks.get(task['id'])['workspace'])
    (workspace / 'ai_manager/db.py').write_text(BASE_DB.replace("conn.execute('SELECT 1')", "conn.execute('DELETE FROM conversations')"))
    git(workspace, 'commit', '-am', 'bad migration')
    await workers.verify(scheduler.tasks.get(task['id']), execution, workspace, {})
    from ai_manager.parallel import retain_source
    with db.transaction() as conn:
        retain_source(db, conn, task['id'])
    before = db.all('SELECT * FROM conversations')
    scheduler.auto_deploy.step()
    await scheduler.auto_deploy.job
    assert db.all('SELECT * FROM conversations') == before
    with db.connect() as conn:
        value = next(v for v in rows(conn) if v.get('kind') == 'auto')
    assert value['status'] == 'failed' and ('Migration changes existing rows' in value['reason']
                                          or 'integrity/foreign-key' in value['reason'])
    assert scheduler.deployment.status()['phase'] == 'idle'


async def test_schema_drift_prevents_activation(automatic):
    db, _, _, _, scheduler, _, live, _, systemd = automatic
    await complete(automatic)
    await prepare_and_dispatch(automatic)
    scheduler.deployment.acknowledge(db)
    with db.transaction() as conn:
        conn.execute('CREATE TABLE unexpected(value TEXT)')
    helper = Helper({'repo': str(live), 'data': str(db.path.parent)})
    operations = list(systemd.operations)
    result = helper.execute()
    assert result['phase'] == 'failed' and 'schema differs' in result['error']
    assert systemd.operations == operations


async def test_dependency_environment_is_rehearsed_and_restored_on_health_failure(automatic, monkeypatch, tmp_path):
    db, _, _, workers, scheduler, _, live, port, _ = automatic
    task, execution, _ = await complete(automatic, healthy=False)
    workspace = Path(scheduler.tasks.get(task['id'])['workspace'])
    (workspace / 'uv.lock').write_text('candidate dependency lock')
    git(workspace, 'add', 'uv.lock')
    git(workspace, 'commit', '-m', 'dependency update')
    await workers.verify(scheduler.tasks.get(task['id']), execution, workspace, {})
    from ai_manager.parallel import retain_source
    with db.transaction() as conn:
        retain_source(db, conn, task['id'])
    executable = tmp_path / 'tools/uv'
    executable.parent.mkdir()
    executable.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
assert sys.argv[1:] == ['sync', '--frozen']
venv = Path(os.environ['UV_PROJECT_ENVIRONMENT'])
(venv / 'bin').mkdir(parents=True, exist_ok=True)
(venv / 'marker').write_text(Path('uv.lock').read_text())
if not (venv / 'bin/python').exists():
    (venv / 'bin/python').write_text("#!" + {sys.executable!r} + "\\nimport os, sys\\nos.execv(" + repr({sys.executable!r}) + ", [" + repr({sys.executable!r}) + "] + sys.argv[1:])\\n")
    (venv / 'bin/python').chmod(0o700)
''')
    executable.chmod(0o700)
    import os
    monkeypatch.setenv('PATH', str(executable.parent) + ':' + os.environ['PATH'])
    venv = live / '.venv'
    venv.mkdir()
    (venv / 'marker').write_text('base dependencies')
    # Like the service environment: deliberately outside the source inventory.
    (live / '.git/info/exclude').write_text('.venv/\n')
    intent, release = await prepare_and_dispatch(automatic)
    manifest = read(release / 'manifest.json')
    assert manifest['schema']['dependencies']['uv.lock']
    assert manifest['rehearsal']['passed']
    assert not (release / 'rehearsal-venv').exists()
    scheduler.deployment.acknowledge(db)
    helper = Helper({'repo': str(live), 'data': str(db.path.parent), 'native': str(db.path.parent / 'native'),
                     'preserve_files': [], 'venv': str(venv), 'uv': str(executable),
                     'loopback': f'http://127.0.0.1:{port}', 'https': f'http://localhost:{port}'})
    result = helper.execute()
    assert result['phase'] == 'rolled_back', result
    assert result['restored']['venv'] == str(venv)
    assert (venv / 'marker').read_text() == 'base dependencies'
    assert not (live / 'uv.lock').exists()
    assert db.setting('candidate-runtime') is None
    assert intent['id']


async def test_scope_change_cannot_reuse_previous_verification(automatic):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    task, _, _ = await complete(automatic)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET acceptance=acceptance||' new requirement' WHERE id=?", (task['id'],))
    scheduler.auto_deploy.step()
    assert not scheduler.auto_deploy.busy()
    assert scheduler.deployment.status()['phase'] == 'idle'


async def test_wrong_release_cannot_consume_auto_intent(automatic):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    _, execution, _ = await complete(automatic)
    scheduler.auto_deploy.step()
    await scheduler.auto_deploy.job
    with db.connect() as conn:
        intent = next(v for v in rows(conn) if v.get('kind') == 'auto')
    with pytest.raises(ValueError, match='differs from the pipeline'):
        scheduler.intents.consume_auto(intent['id'], 'other-release', intent['auto']['commit'], execution)
    assert scheduler.deployment.status()['phase'] == 'idle'


async def test_request_sql_crash_gap_is_reconciled_without_retry(automatic):
    db, registry, _, _, scheduler, _, _, _, _ = automatic
    await complete(automatic)
    intent, _ = await prepare_and_dispatch(automatic)
    receipt = scheduler.deployment.status()
    # Durable protocol request, rolled-back SQL consumption (e.g. a process crash).
    with db.transaction() as conn:
        value = next(v for v in rows(conn) if v.get('kind') == 'auto')
        value['status'] = 'recorded'
        value['auto']['state'] = 'prepared'
        conn.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value), 'deployment_intent:' + intent['id']))
    reopened = AutoDeploy(Database(db.path), registry, scheduler.deployment, scheduler.intents)
    reopened.recover()
    with db.connect() as conn:
        value = next(v for v in rows(conn) if v.get('kind') == 'auto')
    assert value['status'] == 'dispatched' and value['nonce'] == receipt['nonce']
    assert scheduler.deployment.status() == receipt
    reopened.step()
    assert not reopened.busy()


@pytest.mark.parametrize('deployment', ['quote', 'explicit', 'discovered'])
async def test_on_explicit_new_work_deployment_uses_auto_policy_without_manual_review(automatic, deployment):
    db, _, chat, _, scheduler, project, _, _, _ = automatic
    task, _, _ = await complete(automatic, deployment=deployment)
    with db.connect() as conn:
        values = rows(conn)
    automatic_intent = next(v for v in values if v.get('kind') == 'auto')
    manual = next(v for v in values if v.get('kind') != 'auto')
    assert task['deployment_intent_id'] == automatic_intent['id']
    assert manual['status'] == 'superseded' and manual['auto_intent_id'] == automatic_intent['id']
    assert manual['assignment_hashes'].keys() == {task['id']}
    assert automatic_intent['manual_intent_id'] == manual['id']
    assert automatic_intent['source_message_id'] == manual['source_message_id']
    later = await owner(chat, project, 'Unrelated later chat', mode='conversation')
    finish_owner(db, later)
    await prepare_and_dispatch(automatic)
    assert scheduler.deployment.status()['origin']['intent_id'] == automatic_intent['id']
    assert not next(v for v in scheduler.intents.list(project) if v['id'] == automatic_intent['id'])['needs_owner_review']


async def test_on_original_manual_intent_cancellation_still_cancels_linked_auto_work(automatic):
    db, _, _, _, scheduler, _, _, _, _ = automatic
    await complete(automatic, deployment='quote')
    with db.transaction() as conn:
        from ai_manager.deployment_intent import revoke
        manual = next(v for v in rows(conn) if v.get('kind') != 'auto')
        revoke(db, conn, 'Owner cancelled the original scoped instruction', intent_id=manual['id'])
    scheduler.auto_deploy.step()
    with db.connect() as conn:
        assert all(v['status'] == 'revoked' for v in rows(conn))
    assert not scheduler.auto_deploy.busy() and scheduler.deployment.status()['phase'] == 'idle'


async def test_off_explicit_new_work_remains_manual(automatic):
    db, _, _, _, scheduler, project, _, _, _ = automatic
    scheduler.auto_deploy.set(project, False)
    task, _, _ = await complete(automatic, deployment='quote')
    with db.connect() as conn:
        values = rows(conn)
    assert len(values) == 1 and values[0]['status'] == 'recorded'
    assert values[0].get('kind') != 'auto' and task['deployment_intent_id'] == values[0]['id']
    scheduler.auto_deploy.step()
    assert not scheduler.auto_deploy.busy() and scheduler.deployment.status()['phase'] == 'idle'


async def test_on_new_explicit_work_does_not_revoke_another_assignments_standing_intent(automatic):
    db, _, chat, _, scheduler, project, _, _, _ = automatic
    first, _, _ = await complete(automatic)
    second = await owner(chat, project, 'Implement another change and deploy verified result')
    await call(scheduler, project, second, 'delegate', objective='Another requested change',
               change_paths=['file'], owner_deployment_quote='deploy verified result')
    with db.connect() as conn:
        original = next(v for v in rows(conn) if v.get('kind') == 'auto' and v['task_ids'] == [first['id']])
    assert original['status'] == 'recorded'

import sqlite3
from pathlib import Path

import pytest

from ai_manager.db import Database
from ai_manager.deployment import Handoff, Helper, atomic, backup, idle, lock, read, run


def setup(tmp_path):
    data = tmp_path / 'data'
    db = Database(data / 'manager.sqlite3')
    handoff = Handoff(data)
    atomic(handoff.root / 'releases' / 'release-1' / 'manifest.json', {'id': 'release-1'})
    atomic(handoff.root / 'releases' / 'release-2' / 'manifest.json', {'id': 'release-2'})
    return db, handoff


def test_id_scope_overlap_and_retry(tmp_path):
    db, handoff = setup(tmp_path)
    for invalid in ('../release-1', '/tmp/test', 'release-1;sh', 'A', ''):
        with pytest.raises(ValueError):
            handoff.request(invalid)
    with pytest.raises(ValueError):
        handoff.request('missing')
    first = handoff.request('release-1')
    assert handoff.request('release-1') == first
    with pytest.raises(ValueError):
        handoff.request('release-2')
    handoff.acknowledge(db, busy=True)
    assert handoff.status()['phase'] == 'waiting_idle'
    handoff.acknowledge(db)
    assert handoff.status()['phase'] == 'acknowledged'
    assert Handoff(db.path.parent).ready_to_stop()


@pytest.mark.parametrize('sql', [
    "INSERT INTO executions(id,agent_id,status,started_at) VALUES('e','execution','recovering',1)",
    "INSERT INTO executions(id,agent_id,status,started_at,finished_at,delivery) VALUES('e','execution','completed',1,2,'pending')",
    "INSERT INTO questions VALUES('q','e','{}','input',NULL,'pending',1)",
    "INSERT INTO messages VALUES('m','c','assistant','','streaming',NULL,1)",
])
def test_idle_blocks_every_unresolved_record(tmp_path, sql):
    db, handoff = setup(tmp_path)
    # Fixture deliberately permits dangling IDs to cover conservative read-only gates.
    with sqlite3.connect(db.path) as conn:
        conn.execute(sql)
    handoff.request('release-1')
    handoff.acknowledge(db)
    assert handoff.status()['phase'] == 'waiting_idle'


def test_backup_integrity_and_exact_rows(tmp_path):
    db, _ = setup(tmp_path)
    db.set_setting('preserved', {'value': 12})
    target = tmp_path / 'backup.sqlite3'
    assert len(backup(db.path, target)) == 64
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='preserved'").fetchone()[0] == '{"value": 12}'
        assert not any(idle(conn).values())


class Simulated(Helper):
    def check_external(self):
        pass

    def validate(self, manifest):
        if self.reject:
            raise ValueError('dirty live source')

    def snapshot(self, directory):
        directory.mkdir(parents=True, mode=0o700)
        backup(self.data / 'manager.sqlite3', directory / 'manager.sqlite3')

    def service(self, operation):
        self.operations.append(operation)

    def wait_health(self, manifest, snapshot):
        assert Path(snapshot).exists()
        if manifest['commit'] == self.target and self.bad_health:
            raise ValueError('bad served assets')
        if manifest['commit'] != self.target and self.bad_rollback:
            raise ValueError('rollback health failed')
        return {'verified': manifest['commit']}


def simulation(tmp_path):
    db, handoff = setup(tmp_path)
    repo = tmp_path / 'repo'
    repo.mkdir()
    run(['git', 'init'], repo)
    run(['git', 'config', 'user.email', 'test@example.invalid'], repo)
    run(['git', 'config', 'user.name', 'Test'], repo)
    (repo / 'file').write_text('before')
    run(['git', 'add', '.'], repo)
    run(['git', 'commit', '-m', 'base'], repo)
    base = run(['git', 'rev-parse', 'HEAD'], repo)
    (repo / 'file').write_text('after')
    run(['git', 'commit', '-am', 'candidate'], repo)
    target = run(['git', 'rev-parse', 'HEAD'], repo)
    run(['git', 'checkout', '--detach', base], repo)
    atomic(handoff.root / 'releases/release-1/manifest.json',
           {'id': 'release-1', 'commit': target, 'base': base, 'base_assets': {}})
    handoff.request('release-1')
    handoff.acknowledge(db)
    helper = Simulated({'data': str(db.path.parent), 'repo': str(repo)})
    helper.target, helper.operations = target, []
    helper.reject = helper.bad_health = helper.bad_rollback = False
    return db, handoff, helper, repo, base


def test_activation_and_durable_result(tmp_path):
    _, handoff, helper, repo, _ = simulation(tmp_path)
    assert helper.execute()['phase'] == 'succeeded'
    assert (repo / 'file').read_text() == 'after'
    assert helper.operations == ['stop', 'start']
    assert not handoff.gated()
    assert read(handoff.root / 'results' / (handoff.status()['nonce']+'.json'))['health']['verified'] == helper.target
    helper.execute()
    assert helper.operations == ['stop', 'start']


def test_failed_health_rolls_back_without_restoring_database(tmp_path):
    db, handoff, helper, repo, base = simulation(tmp_path)
    db.set_setting('new-data', 'preserve')
    helper.bad_health = True
    assert helper.execute()['phase'] == 'rolled_back'
    assert run(['git', 'rev-parse', 'HEAD'], repo) == base
    assert (repo / 'file').read_text() == 'before'
    assert db.setting('new-data') == 'preserve'
    assert helper.operations == ['stop', 'start', 'stop', 'start']
    assert not handoff.gated()


def test_rollback_failure_remains_fenced(tmp_path):
    _, handoff, helper, _, _ = simulation(tmp_path)
    helper.bad_health = helper.bad_rollback = True
    assert helper.execute()['phase'] == 'rollback_failed'
    assert handoff.ready_to_stop()
    helper.execute()
    assert helper.operations == ['stop', 'start', 'stop', 'start']


def test_revalidation_failure_never_stops_service(tmp_path):
    _, handoff, helper, _, _ = simulation(tmp_path)
    helper.reject = True
    assert helper.execute()['phase'] == 'failed'
    assert helper.operations == []
    assert not handoff.gated()


def test_stale_idle_and_concurrent_helpers(tmp_path):
    db, handoff, helper, _, _ = simulation(tmp_path)
    with lock(handoff.root / 'helper.lock'):
        with pytest.raises(BlockingIOError):
            helper.execute()
    with sqlite3.connect(db.path) as conn:
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at) VALUES('new','execution','running',1)")
    assert helper.execute()['phase'] == 'failed'
    assert helper.operations == []


def test_interrupted_activation_never_restarts_or_unfences(tmp_path):
    _, handoff, helper, _, _ = simulation(tmp_path)
    helper.state(handoff.status(), 'activating')
    assert helper.execute()['phase'] == 'rollback_failed'
    assert helper.operations == []
    assert handoff.gated()


def test_report_exactly_once_in_origin_and_evidence(tmp_path):
    db, handoff = setup(tmp_path)
    with db.transaction() as conn:
        conn.execute("INSERT INTO conversations(id,context,created_at) VALUES('c','global',1)")
    value = handoff.request('release-1', {'conversation_id': 'c', 'execution_id': 'e'})
    value['phase'] = 'succeeded'
    atomic(handoff.root / 'active.json', value)
    Handoff(db.path.parent).report(db)
    Handoff(db.path.parent).report(db)
    assert db.one("SELECT count(*) AS n FROM messages WHERE conversation_id='c'")['n'] == 1
    assert db.one("SELECT count(*) AS n FROM events WHERE kind='deployment.result'")['n'] == 1


@pytest.mark.parametrize('column,value', [('lifecycle', 'queued'), ('lease_owner', 'e')])
def test_queued_work_and_claims_block_ack(tmp_path, column, value):
    db, handoff = setup(tmp_path)
    with sqlite3.connect(db.path) as conn:
        conn.execute("INSERT INTO tasks(id,project_id,description,created_at,updated_at) VALUES('t','p','existing',1,1)")
        conn.execute('UPDATE tasks SET '+column+'=?', (value,))
    handoff.request('release-1')
    handoff.acknowledge(db)
    assert handoff.status()['phase'] == 'waiting_idle'


@pytest.mark.asyncio
async def test_manager_request_mode_scope_and_status(tmp_path):
    from types import SimpleNamespace

    from ai_manager.orchestration import Scheduler
    db, handoff = setup(tmp_path)
    settings = SimpleNamespace(data_dir=db.path.parent)
    registry = SimpleNamespace(get=lambda key: {'path': '/other' if key == 'other' else '/manager'})
    scheduler = Scheduler(db, registry, settings, None, None)
    atomic(handoff.root / 'config.json', {'repo': '/manager'})
    params = {'tool': 'manager_action', 'arguments': {'action': 'deploy_release', 'release_id': 'release-1'},
              'mode': 'conversation', 'conversation_id': 'c', 'manager_execution_id': 'e'}
    with pytest.raises(ValueError, match='Auto'):
        await scheduler.manager_action(None, params)
    params['mode'] = 'auto'
    with pytest.raises(ValueError, match='only'):
        await scheduler.manager_action('other', params)
    with pytest.raises(ValueError, match='missing'):
        await scheduler.manager_action('manager', params)
    handoff.request('release-1')  # Low-level protocol fixture; scoped owner flow is tested separately.
    params['arguments'] = {'action': 'deployment_status'}
    assert (await scheduler.manager_action(None, params))['release_id'] == 'release-1'


def test_http_gate_auth_and_mutations(tmp_path):
    from fake_codex import FakeServer
    from fastapi.testclient import TestClient
    from test_web import sign_in

    from ai_manager.config import Settings
    from ai_manager.web import create_app
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(tmp_path,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/deployment').status_code == 401
        sign_in(client, app, settings)
        handoff = app.state.scheduler.deployment
        atomic(handoff.root / 'releases/release-1/manifest.json', {'id': 'release-1'})
        # Keep the scheduler from acknowledging while exercising admission.
        app.state.deployment_mutations = 1
        handoff.request('release-1')
        assert client.post('/api/chat', json={'content': 'new'}).status_code == 503
        assert client.post('/api/projects/discover').status_code == 503
        assert client.patch('/api/tasks/t', json={'action': 'continue', 'revision': 0}).status_code == 503
        assert client.patch('/api/tasks/t', json={'action': 'stop', 'revision': 0}).status_code == 409
        assert client.get('/api/deployment').json()['phase'] == 'waiting_idle'
        handoff.acknowledge(app.state.db)
        assert client.post('/api/login', json={'username': 'owner', 'password': 'a-long-private-password'}).status_code == 503
        assert client.get('/api/deployment').json()['phase'] == 'acknowledged'


@pytest.mark.asyncio
async def test_scheduler_fence_and_inflight_mutation_prevents_ack(tmp_path):
    from types import SimpleNamespace

    from ai_manager.orchestration import Scheduler
    db, handoff = setup(tmp_path)
    calls = []
    async def called():
        calls.append(True)
    chat = SimpleNamespace(jobs={}, recover=called, deliver_results=called)
    workers = SimpleNamespace(jobs={}, servers={}, recover=called)
    scheduler = Scheduler(db, None, SimpleNamespace(data_dir=db.path.parent, max_workers=2), workers, chat)
    async def observe(_):
        pass
    scheduler.observer.observe = observe
    handoff.request('release-1')
    scheduler.deployment_busy = lambda: True
    await scheduler.tick()
    assert handoff.status()['phase'] == 'waiting_idle'
    scheduler.deployment_busy = lambda: False
    await scheduler.tick()
    assert handoff.status()['phase'] == 'acknowledged'
    calls.clear()
    await scheduler.tick()
    assert not calls


def test_cancel_only_exact_pending_request(tmp_path):
    db, handoff = setup(tmp_path)
    value = handoff.request('release-1')
    with pytest.raises(ValueError):
        handoff.cancel('stale')
    handoff.acknowledge(db)
    with pytest.raises(ValueError):
        handoff.cancel(value['nonce'])
    value['phase'] = 'waiting_idle'
    atomic(handoff.root / 'active.json', value)
    assert handoff.cancel(value['nonce'])['phase'] == 'cancelled'
    assert not handoff.gated()
    assert handoff.request('release-2')['phase'] == 'waiting_idle'


def test_bootstrap_refuses_live_service_and_unfinished_work(tmp_path, monkeypatch):
    from scripts import bootstrap_deployment as module
    db, handoff = setup(tmp_path)
    config = handoff.root / 'config.json'
    atomic(config, {'repo': str(tmp_path), 'data': str(db.path.parent)})
    monkeypatch.setattr(module.Helper, 'check_external', lambda self: None)
    monkeypatch.setattr(module, 'run', lambda argv: 'active')
    with pytest.raises(ValueError, match='first stop'):
        module.bootstrap(config, 'release-1')
    assert handoff.status()['phase'] == 'idle'
    monkeypatch.setattr(module, 'run', lambda argv: 'inactive')
    with sqlite3.connect(db.path) as conn:
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at) VALUES('e','execution','running',1)")
    with pytest.raises(ValueError, match='not idle'):
        module.bootstrap(config, 'release-1')
    assert handoff.status()['phase'] == 'idle'


def test_bootstrap_acknowledges_only_stopped_idle_validated_release(tmp_path, monkeypatch):
    from scripts import bootstrap_deployment as module
    db, handoff = setup(tmp_path)
    config = handoff.root / 'config.json'
    atomic(config, {'repo': str(tmp_path), 'data': str(db.path.parent)})
    monkeypatch.setattr(module.Helper, 'check_external', lambda self: None)
    monkeypatch.setattr(module, 'run', lambda argv: 'inactive')
    validated = []
    monkeypatch.setattr(module.Helper, 'validate', lambda self, manifest: validated.append(manifest['id']))
    assert module.bootstrap(config, 'release-1')['phase'] == 'acknowledged'
    assert validated == ['release-1']


def test_real_health_auth_assets_and_preservation_probe(tmp_path, monkeypatch):
    import hashlib

    from fake_codex import FakeServer
    from fastapi.testclient import TestClient
    from test_web import sign_in

    import ai_manager.deployment as module
    from ai_manager.config import Settings
    from ai_manager.web import create_app
    settings = Settings(data_dir=tmp_path/'data', project_roots=(tmp_path,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as client:
        sign_in(client, app, settings)
        commit = client.get('/api/auth-status').json()['release_commit']
        base_sessions = app.state.db.all('SELECT * FROM sessions')
        snapshot = tmp_path/'saved.sqlite3'
        backup(settings.db_path, snapshot)
        helper = Helper({'data': str(settings.data_dir), 'repo': str(tmp_path),
                         'loopback': 'http://localhost', 'https': 'https://localhost'})
        def http(path, cookie=None, base=None):
            # Keep owner credentials out of unauthenticated probes.
            client.cookies.clear()
            response = client.get(path, headers={'Cookie': 'ai_manager_session='+cookie} if cookie else {})
            return response.status_code, response.content
        helper.http = http
        monkeypatch.setattr(module, 'run', lambda argv, cwd=None: 'active' if 'is-active' in argv else 'PID=fixture')
        manifest = {'commit': commit, 'assets': {'app.js': hashlib.sha256(client.get('/static/app.js').content).hexdigest()}}
        assert helper.verify(manifest, snapshot)['auth']
        assert app.state.db.all('SELECT * FROM sessions') == base_sessions  # Probe session removed.
        bad = {**manifest, 'assets': {'app.js': 'wrong'}}
        with pytest.raises(ValueError, match='asset mismatch'):
            helper.verify(bad, snapshot)
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE owner SET username='changed'")
        with pytest.raises(ValueError, match='Preserved data'):
            helper.verify(manifest, snapshot)

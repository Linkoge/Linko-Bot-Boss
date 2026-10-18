"""One consumer for saved project setups; real commands, Git, backup and rollback on disposable data."""
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from test_core import git, run_next
from test_core import world as world_fixture
from test_deploy_script import deploy_script

from ai_manager.codex import MANAGER_INSTRUCTIONS
from ai_manager.deploy_setup import validate_setup
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline, atomic

world = world_fixture
HOOK = '''import os, shutil, sys
from pathlib import Path
action, live = sys.argv[1], Path(sys.argv[2])
backup = Path(os.environ['DEPLOY_BACKUP_DIR']) / 'release.txt'
if action == 'backup':
    shutil.copyfile(live, backup)
elif action == 'deploy':
    live.write_text(Path('shared.txt').read_text())
elif action == 'health':
    assert live.read_text() != 'bad', 'unhealthy release'
elif action == 'rollback':
    shutil.copyfile(backup, live)
else:
    raise SystemExit(4)
'''


@pytest.fixture
def project_runtime(world, tmp_path):
    script, live = tmp_path / 'release.py', tmp_path / 'live.txt'
    script.write_text(HOOK)
    live.write_text('previous live release')
    def command(action):
        return [sys.executable, str(script), action, str(live)]

    setup = {key + '_command': command(action) for key, action in
             [('deploy', 'deploy'), ('backup', 'backup'), ('health', 'health'), ('rollback', 'rollback')]}
    world.pipeline.save_setup(world.project['id'], setup)
    config = {'repo': str(tmp_path / 'unrelated-manager'), 'data': str(world.settings.data_dir),
              'health_seconds': 0, 'idle_seconds': 0, 'test_cmd': [sys.executable, '-c', 'raise SystemExit(99)']}
    return world, live, setup, config


@pytest.mark.asyncio
@pytest.mark.parametrize('health_url', [False, True])
async def test_passing_merge_deploys_project_and_checks_health(project_runtime, health_url):
    world, live, setup, config = project_runtime
    server = None
    if health_url:
        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if live.read_text() != 'bad' else 503)
                self.end_headers()

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        del setup['health_command']
        setup['health_url'] = f'http://127.0.0.1:{server.server_port}/health'
        world.pipeline.save_setup(world.project['id'], setup)
    try:
        world.tasks.add(world.project['id'], 'deploy another application')
        done = await run_next(world)
        assert done['state'] == 'done' and done['merge']['deploy']
        request = world.pipeline.status()['request']
        assert request['setup'] == setup and Path(request['repo']) == world.app
        result = deploy_script.Deploy(config).main()
        assert result['status'] == 'success' and 'health check passed' in result['reason']
        assert live.read_text() == (world.app / 'shared.txt').read_text()
        assert Path(result['backup'], 'release.txt').read_text() == 'previous live release'
        assert Path(result['backup'], 'code.bundle').is_file()
        assert not world.pipeline.pending()
        assert not (Path(config['data']) / 'deploy/last_good.json').exists()  # independent project receipt
        assert Pipeline(world.db, world.registry).setup(world.project['id']) == setup
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_explicit_deploy_health_failure_restores_live_release_and_code(project_runtime):
    world, live, _, config = project_runtime
    world.pipeline.request(world.project['id'], 'owner')
    good = deploy_script.Deploy(config).main()
    assert good['status'] == 'success'
    previous = live.read_text()
    (world.app / 'shared.txt').write_text('bad')
    git(world.app, 'commit', '-qam', 'bad release')
    bad = git(world.app, 'rev-parse', 'HEAD')
    world.pipeline.request(world.project['id'], 'owner')
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'rolled_back' and 'health check failed' in result['reason']
    assert live.read_text() == previous
    assert git(world.app, 'rev-parse', 'main') == good['commit']
    assert git(world.app, 'rev-parse', 'linko-failed-' + result['id']) == bad
    assert not world.pipeline.pending()


def test_first_deploy_also_rolls_back_without_prior_receipt(project_runtime):
    world, live, _, config = project_runtime
    (world.app / 'shared.txt').write_text('bad')
    git(world.app, 'commit', '-qam', 'first unhealthy deployment')
    world.pipeline.request(world.project['id'], 'owner')
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'rolled_back'
    assert live.read_text() == 'previous live release'


def test_service_setup_uses_the_same_backup_health_and_rollback_consumer(project_runtime, tmp_path, monkeypatch):
    world, live, setup, config = project_runtime
    service_control = tmp_path / 'systemctl'
    service_control.write_text(f'#!{sys.executable}\n' + '''import os, sys
from pathlib import Path
assert sys.argv[1] == '--user' and sys.argv[3] == 'fixture.service'
if sys.argv[2] == 'start':
    Path(os.environ['TEST_RELEASE']).write_text(Path('shared.txt').read_text())
''')
    service_control.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + ':/usr/bin:/bin')
    monkeypatch.setenv('TEST_RELEASE', str(live))
    del setup['deploy_command']
    setup['service'] = 'fixture.service'
    world.pipeline.save_setup(world.project['id'], setup)
    world.pipeline.request(world.project['id'], 'owner')
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'success' and Path(result['backup'], 'release.txt').is_file()
    (world.app / 'shared.txt').write_text('bad')
    git(world.app, 'commit', '-qam', 'unhealthy service')
    previous = live.read_text()
    world.pipeline.request(world.project['id'], 'owner')
    assert deploy_script.Deploy(config).main()['status'] == 'rolled_back'
    assert live.read_text() == previous


@pytest.mark.parametrize('failure,expected', [('backup', 'failed'), ('deploy', 'rolled_back'),
                                               ('rollback', 'rollback_failed')])
def test_hook_failures_preserve_backup_and_report_recovery(project_runtime, failure, expected):
    world, live, setup, config = project_runtime
    setup[failure + '_command'] = [sys.executable, '-c', 'raise SystemExit(3)']
    if failure == 'rollback':
        (world.app / 'shared.txt').write_text('bad')
        git(world.app, 'commit', '-qam', 'unhealthy')
    world.pipeline.save_setup(world.project['id'], setup)
    world.pipeline.request(world.project['id'], 'owner')
    result = deploy_script.Deploy(config).main()
    assert result['status'] == expected
    if failure != 'rollback':
        assert live.read_text() == 'previous live release'
    assert not world.pipeline.pending()


@pytest.mark.smoke
@pytest.mark.parametrize('missing', ['deploy_command', 'health_command', 'backup_command', 'rollback_command'])
def test_setup_requires_deploy_backup_health_and_rollback(missing):
    setup = {field: ['true'] for field in ('deploy_command', 'health_command', 'backup_command', 'rollback_command')}
    del setup[missing]
    with pytest.raises(ValueError):
        validate_setup(setup)


@pytest.mark.smoke
def test_one_saved_setup_can_be_replaced_removed_and_is_frozen_while_pending(world):
    project = world.project['id']
    original = world.pipeline.setup(project)
    replacement = {**original, 'deploy_command': ['echo', 'new setup']}
    assert world.pipeline.save_setup(project, replacement)['setup'] == replacement
    assert Pipeline(world.db, world.registry).setup(project) == replacement
    assert world.pipeline.request(project)['setup'] == replacement
    with pytest.raises(ValueError, match='pending deploy'):
        world.pipeline.save_setup(project, original)
    world.pipeline.cancel()
    assert world.pipeline.save_setup(project, None)['deployment'].startswith('no deploy setup')
    assert world.pipeline.request(project)['status'] == 'no deploy setup'
    assert not world.pipeline.pending()


def test_installed_runtime_setup_migrates_once_and_removal_is_durable(world):
    project = world.project['id']
    world.db.run('DELETE FROM settings WHERE key=?', ('deploy_setup:' + project,))
    config = {'repo': str(world.app), 'data': str(world.settings.data_dir), 'service': 'fixture.service'}
    atomic(world.pipeline.root / 'installed.json', config)
    assert world.pipeline.setup(project) == {'runtime': config}
    assert world.pipeline.request(project)['setup'] == {'runtime': config}
    world.pipeline.cancel()
    world.pipeline.save_setup(project, None)
    assert Pipeline(world.db, world.registry).setup(project) is None


def test_host_cli_resolves_registered_projects_saved_setup_including_removal(project_runtime):
    world, live, _, config = project_runtime
    config['repo'] = str(world.app)
    # --now's legacy request shape must resolve the registered setup rather than restart a different service.
    atomic(world.pipeline.root / 'request.json', {'id': 'manual', 'source': 'owner', 'requested_at': time.time()})
    assert deploy_script.Deploy(config).main()['status'] == 'success'
    assert live.read_text() == (world.app / 'shared.txt').read_text()
    world.pipeline.save_setup(world.project['id'], None)
    atomic(world.pipeline.root / 'request.json', {'id': 'removed', 'source': 'owner', 'requested_at': time.time()})
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'failed' and 'no deploy setup' in result['reason']


def test_runtime_requires_a_known_release_before_it_can_guarantee_rollback(world, monkeypatch):
    config = {'repo': str(world.app), 'data': str(world.settings.data_dir)}
    request = {'id': 'unknown-release', 'source': 'owner', 'requested_at': time.time(),
               'repo': str(world.app), 'setup': {'runtime': config}}
    atomic(world.pipeline.root / 'request.json', request)
    monkeypatch.setattr(deploy_script.Deploy, 'running_commit', lambda self: None)
    monkeypatch.setattr(deploy_script.Deploy, 'service', lambda *args: pytest.fail('Must not stop or start'))
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'failed' and 'cannot guarantee' in result['reason']
    assert not (world.pipeline.root / 'backups').exists()


def test_pending_request_is_scoped_to_its_project_and_expiry_releases_queue(project_runtime):
    world, live, setup, config = project_runtime
    other = world.app.parent / 'other'
    other.mkdir()
    project = world.registry.add(other)
    world.pipeline.save_setup(project['id'], setup)
    request = world.pipeline.request(world.project['id'])
    with pytest.raises(ValueError, match='Another project'):
        world.pipeline.request(project['id'])
    request['requested_at'] = time.time() - 1801
    atomic(world.pipeline.root / 'request.json', request)
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'cancelled' and '30 minutes' in result['reason']
    assert not world.pipeline.pending() and live.read_text() == 'previous live release'


def test_each_project_reports_only_its_own_receipt(project_runtime):
    world, _, setup, config = project_runtime
    project_id = world.project['id']
    world.pipeline.request(project_id, 'owner')
    result = deploy_script.Deploy(config).main()
    other = world.app.parent / 'other'
    other.mkdir()
    other_id = world.registry.add(other)['id']
    world.pipeline.save_setup(other_id, setup)
    assert world.pipeline.status(project_id)['last_result'] == result
    assert world.pipeline.status(other_id)['last_result'] is None
    assert world.pipeline.summary(other_id) == 'no deploy yet'
    world.pipeline.request(other_id, 'owner')
    assert world.pipeline.summary(project_id) == "another project's deploy holds the queue"


def test_invalid_or_emergency_request_never_falls_back_to_another_runtime(project_runtime):
    world, live, setup, config = project_runtime
    request = world.pipeline.request(world.project['id'], 'owner')
    request.pop('setup')
    world.db.set_setting('deploy_setup:' + world.project['id'], None)
    atomic(world.pipeline.root / 'request.json', request)
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'failed' and 'no deploy setup' in result['reason']
    world.pipeline.save_setup(world.project['id'], setup)
    request = world.pipeline.request(world.project['id'], 'owner')
    request['setup'] = {key: value for key, value in setup.items() if key != 'rollback_command'}
    atomic(world.pipeline.root / 'request.json', request)
    assert deploy_script.Deploy(config).main()['status'] == 'failed'
    request = world.pipeline.request(world.project['id'], 'owner')
    request['emergency'] = True
    atomic(world.pipeline.root / 'request.json', request)
    result = deploy_script.Deploy(config).main()
    assert result['status'] == 'failed' and 'cannot use host emergency mode' in result['reason']
    assert live.read_text() == 'previous live release'


@pytest.mark.asyncio
async def test_manager_uses_selected_project_setup_and_never_emergency_for_normal_deploy(world):
    scheduler = Scheduler(world.db, world.registry, world.settings, world.core, None)
    project = world.project['id']
    params = {'tool': 'manager_action', 'conversation_id': None}

    async def action(name, **fields):
        return await scheduler.manager_action(project, {**params, 'arguments': {'action': name, **fields}})

    assert (await action('deployment_status'))['setup'] == world.pipeline.setup(project)
    assert (await action('deploy'))['project_id'] == project
    world.pipeline.cancel()
    await action('save_deploy_setup', setup=None)
    assert (await action('deploy'))['status'] == 'no deploy setup'
    with pytest.raises(ValueError, match='another project'):
        await action('save_deploy_setup', setup=None, project_id='elsewhere')
    with pytest.raises(ValueError, match='not explicit owner authorization'):
        await scheduler.manager_action(project, {**params, 'internal': True,
                                                'arguments': {'action': 'save_deploy_setup', 'setup': None}})
    assert 'Never use emergency_repair for ordinary deployment' in MANAGER_INSTRUCTIONS
    assert not world.pipeline.pending()
    assert not list(world.settings.data_dir.glob('repair/requests/*'))

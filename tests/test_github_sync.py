"""Incoming updates use disposable bare origins, never GitHub or a live checkout."""
import asyncio

import pytest
from test_core import git
from test_core import world as world_fixture
from test_web import client as client_fixture
from test_web import sign_in

from ai_manager import github_sync

world = world_fixture
client = client_fixture


@pytest.fixture
def origin(world, tmp_path, monkeypatch):
    bare = tmp_path / 'origin.git'
    git(tmp_path, 'init', '--bare', '-q', str(bare))
    git(world.app, 'remote', 'add', 'origin', str(bare))
    git(world.app, 'push', '-q', 'origin', 'main')
    peer = tmp_path / 'peer'
    git(tmp_path, 'clone', '-q', '-b', 'main', str(bare), str(peer))
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: url == str(bare))
    world.peer, world.bare = peer, bare
    return world


def advance(root, name):
    (root / name).write_text(name)
    git(root, 'add', name)
    git(root, 'commit', '-qm', name)
    return git(root, 'rev-parse', 'HEAD')


def behind(w):
    head = advance(w.peer, 'remote-change')
    git(w.peer, 'push', '-q', 'origin', 'main')
    return head


async def test_fast_forward_requests_normal_deploy(origin):
    target = behind(origin)
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'updated'
    assert git(origin.app, 'rev-parse', 'HEAD') == target
    request = origin.pipeline.status()['request']
    assert request['source'] == 'github-sync' and request['setup']['backup_command']


async def test_running_task_prevents_update(origin):
    before = git(origin.app, 'rev-parse', 'HEAD')
    behind(origin)
    origin.tasks.add(origin.project['id'], 'busy')
    origin.tasks.claim()
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'busy'
    assert git(origin.app, 'rev-parse', 'HEAD') == before


async def test_divergence_stays_flagged_without_mutation(origin):
    remote = behind(origin)
    local = advance(origin.app, 'local-change')
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'diverged'
    git(origin.app, 'remote', 'set-url', 'origin', str(origin.bare))
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'diverged'
    assert git(origin.app, 'rev-parse', 'HEAD') == local
    assert git(origin.peer, 'rev-parse', 'HEAD') == remote
    assert not origin.pipeline.pending()


@pytest.mark.parametrize('dirty', ['tracked', 'untracked'])
async def test_uncommitted_changes_are_preserved(origin, dirty):
    before = git(origin.app, 'rev-parse', 'HEAD')
    behind(origin)
    path = origin.app / ('shared.txt' if dirty == 'tracked' else 'new-file')
    path.write_text('keep me')
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'warning' and 'Uncommitted' in result['reason']
    assert path.read_text() == 'keep me'
    assert git(origin.app, 'rev-parse', 'HEAD') == before


async def test_offline_is_never_fetched(origin, monkeypatch):
    origin.db.set_setting('github_tier:' + origin.project['id'], 'offline')
    monkeypatch.setattr(github_sync, 'git_bytes', lambda *a, **k: pytest.fail('offline git call'))
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'excluded'


async def preflight(w):
    assert w.tasks.claim(w.pipeline.sync.ready) is None
    await asyncio.gather(*w.pipeline.sync.jobs.values())


async def test_update_precedes_dispatch_and_deploy_holds_queue(origin):
    target = behind(origin)
    task = origin.tasks.add(origin.project['id'], 'start latest')
    await preflight(origin)
    assert git(origin.app, 'rev-parse', 'HEAD') == target
    assert origin.tasks.get(task['id'])['state'] == 'queued'
    assert origin.tasks.claim(origin.pipeline.sync.ready) is None
    # Simulate the existing deploy protocol finishing; the next tick can dispatch.
    (origin.pipeline.root / 'request.json').unlink()
    claimed = origin.tasks.claim(origin.pipeline.sync.ready)
    assert claimed['id'] == task['id']


async def test_unreachable_origin_warns_but_dispatch_proceeds(origin, monkeypatch):
    git(origin.app, 'remote', 'set-url', 'origin', str(origin.bare / 'missing'))
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: True)
    task = origin.tasks.add(origin.project['id'], 'continue offline')
    await preflight(origin)
    assert origin.pipeline.sync.status(origin.project['id'])['state'] == 'warning'
    assert origin.tasks.claim(origin.pipeline.sync.ready)['id'] == task['id']


async def test_idle_tick_updates(origin):
    behind(origin)
    origin.pipeline.sync.tick()
    await asyncio.gather(*origin.pipeline.sync.jobs.values())
    assert origin.pipeline.pending()


def test_manual_pull_route_auth_csrf_result_and_warning(client, monkeypatch):
    browser, app, settings = client
    p = app.state.registry.add(settings.project_roots[0] / 'one')
    endpoint = f"/api/projects/{p['id']}/pull"
    assert browser.post(endpoint).status_code == 401
    sign_in(browser, app, settings)
    assert browser.post(endpoint, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    called = []
    def pull(sync, project_id):
        called.append(project_id)
        return {'state': 'diverged', 'reason': 'Out of sync with GitHub, needs attention'}
    monkeypatch.setattr(github_sync.GitHubSync, 'pull', pull)
    response = browser.post(endpoint)
    assert response.status_code == 200 and response.json()['state'] == 'diverged'
    assert p['id'] in called
    projects = browser.get('/api/portfolio').json()['projects']
    assert next(x for x in projects if x['id'] == p['id'])['github_sync']['state'] == 'diverged'



async def test_divergence_remains_visible_during_work_and_skips_fetch(origin, monkeypatch):
    behind(origin)
    advance(origin.app, 'local-change')
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'diverged'
    original = github_sync.git_bytes
    def no_fetch(root, *args, **kwargs):
        assert args[0] != 'fetch'
        return original(root, *args, **kwargs)
    monkeypatch.setattr(github_sync, 'git_bytes', no_fetch)
    origin.tasks.add(origin.project['id'], 'allowed with warning')
    await preflight(origin)
    claimed = origin.tasks.claim(origin.pipeline.sync.ready)
    assert claimed is not None
    (origin.app / 'shared.txt').write_text('owner edit')
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'diverged'


async def test_runner_dispatches_latest_main_after_preflight(origin, monkeypatch):
    origin.deployable(False)
    target = behind(origin)
    origin.tasks.add(origin.project['id'], 'start latest')
    # Record the main commit at dispatch without starting a native worker.
    dispatched = []
    monkeypatch.setattr(origin.core, 'launch', lambda task: dispatched.append(git(origin.app, 'rev-parse', 'HEAD')))
    monkeypatch.setattr(origin.pipeline.backups, 'tick', lambda: None)
    await origin.core.runner.tick()
    assert dispatched == []
    await asyncio.gather(*origin.pipeline.sync.jobs.values())
    await origin.core.runner.tick()
    assert dispatched == [target]


def test_manual_pull_fetches_bare_origin_and_queues_deploy(client, tmp_path, monkeypatch):
    browser, app, settings = client
    sign_in(browser, app, settings)
    monkeypatch.setattr(app.state.github_projects.pipeline.sync, 'tick', lambda: None)
    root = settings.project_roots[0] / 'one'
    git(root, 'init', '-q', '-b', 'main')
    advance(root, 'base')
    bare = tmp_path / 'manual.git'
    git(tmp_path, 'init', '--bare', '-q', str(bare))
    git(root, 'remote', 'add', 'origin', str(bare))
    git(root, 'push', '-q', 'origin', 'main')
    p = app.state.registry.add(root)
    pipeline = app.state.github_projects.pipeline
    pipeline.save_setup(p['id'], {'deploy_command': ['true'], 'backup_command': ['true'],
                                  'health_command': ['true'], 'rollback_command': ['true']})
    peer = tmp_path / 'manual-peer'
    git(tmp_path, 'clone', '-q', '-b', 'main', str(bare), str(peer))
    target = advance(peer, 'new-release')
    git(peer, 'push', '-q', 'origin', 'main')
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: url == str(bare))
    result = browser.post(f"/api/projects/{p['id']}/pull")
    assert result.status_code == 200 and result.json()['state'] == 'updated'
    assert git(root, 'rev-parse', 'HEAD') == target
    assert pipeline.status()['request']['source'] == 'github-sync'


async def test_new_task_cannot_reuse_canceled_tasks_preflight(origin):
    origin.deployable(False)
    first = origin.tasks.add(origin.project['id'], 'first')
    await preflight(origin)
    origin.tasks.control(first['id'], 'cancel')
    target = behind(origin)
    next_task = origin.tasks.add(origin.project['id'], 'second')
    await preflight(origin)
    assert git(origin.app, 'rev-parse', 'HEAD') == target
    assert origin.tasks.claim(origin.pipeline.sync.ready)['id'] == next_task['id']


async def test_slow_fetch_does_not_hold_another_projects_dispatch(origin, monkeypatch):
    import threading
    other = origin.settings.project_roots[0] / 'independent'
    other.mkdir()
    git(other, 'init', '-q', '-b', 'main')
    advance(other, 'base')
    project = origin.registry.add(other)
    origin.tasks.add(origin.project['id'], 'fetch first')
    origin.tasks.add(project['id'], 'independent work')
    entered, release = threading.Event(), threading.Event()
    pull = origin.pipeline.sync.pull
    def slow_pull(pid):
        if pid == origin.project['id']:
            entered.set()
            assert release.wait(timeout=5)
        return pull(pid)
    monkeypatch.setattr(origin.pipeline.sync, 'pull', slow_pull)
    monkeypatch.setattr(origin.pipeline.backups, 'tick', lambda: None)
    dispatched = []
    monkeypatch.setattr(origin.core, 'launch', lambda task: dispatched.append(task['project_id']))
    try:
        await origin.core.runner.tick()
        assert await asyncio.to_thread(entered.wait, 2)
        assert dispatched == [project['id']]
        assert not origin.pipeline.sync.jobs[origin.project['id']].done()
    finally:
        release.set()
        await asyncio.gather(*origin.pipeline.sync.jobs.values())
    await origin.core.runner.tick()
    assert dispatched == [project['id'], origin.project['id']]

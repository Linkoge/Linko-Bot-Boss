"""Owner override 'Push to GitHub now': disposable local bare remote only; never contacts GitHub."""
import secrets

import pytest
from test_core import git
from test_core import world as world_fixture
from test_github_backup import commit, deploy_result, remote  # noqa: F401
from test_github_projects import hub  # noqa: F401
from test_offline_projects import create_offline

from ai_manager.github_backup import PREFIX

world = world_fixture


def receipt(world):
    return world.registry.get(world.project['id'])['github_backup']


@pytest.mark.asyncio
async def test_manual_push_sends_head_while_gate_waits_then_deploy_verifies_without_duplicate(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    backup.request(pid)  # deploy gate: waiting
    assert receipt(world)['state'] == 'waiting-for-deploy'
    head = commit(world.app, {'fix.txt': 'trivial fix'})
    pushes = []
    original = backup.push
    backup.push = staticmethod(lambda *a, **k: pushes.append(k) or original(*a, **k))
    result = await backup.push_now(pid)
    assert result['state'] == 'synced' and result['commit'] == head and result['unverified'] is True
    assert git(remote, 'rev-parse', 'main') == head
    status = receipt(world)
    assert status['state'] == 'synced' and status['unverified'] is True and status['commit'] == head
    assert status['unverified_at'] > 0
    backup.tick()
    assert backup.job is None  # gate no longer waiting, nothing to retry
    # The matching deploy verifies the commit; the remote is already there, so no second push happens.
    deploy_result(world, 'success', head)
    backup.tick()
    await backup.close()
    status = receipt(world)
    assert status['state'] == 'synced' and status['commit'] == head and 'unverified' not in status
    assert git(remote, 'rev-parse', 'main') == head
    assert len(pushes) == 2  # manual + the verifying attempt; the latter found nothing to send
    assert [e for e in world.db.all("SELECT * FROM events WHERE kind='github_backup.manual'")]


@pytest.mark.asyncio
async def test_older_deploy_is_not_pushed_over_manual_push(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    older = commit(world.app, {'a.txt': '1'})
    head = commit(world.app, {'b.txt': '2'})
    backup.request(pid)
    await backup.push_now(pid)
    assert git(remote, 'rev-parse', 'main') == head
    deploy_result(world, 'success', older)
    backup.tick()
    await backup.close()
    status = receipt(world)
    assert git(remote, 'rev-parse', 'main') == head
    assert status['state'] == 'synced' and status['commit'] == head and status['unverified'] is True
    # A later deploy of HEAD verifies it.
    deploy_result(world, 'success', head, 'second')
    backup.tick()
    await backup.close()
    assert 'unverified' not in receipt(world) and receipt(world)['commit'] == head


@pytest.mark.asyncio
async def test_manual_push_clears_pending_retry_for_same_commit_as_verified(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    head = commit(world.app, {'x.txt': '1'})
    backup.request(pid, head)  # deploy-verified target waiting to be pushed
    result = await backup.push_now(pid)
    assert result['commit'] == head
    assert 'unverified' not in receipt(world) and receipt(world)['state'] == 'synced'


@pytest.mark.asyncio
async def test_manual_push_rejection_shows_parsed_reason_and_keeps_gate(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    backup.request(pid)
    commit(world.app, {'k.txt': 'x'})
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "remote: error: GH013: Repository rule violations found for refs/heads/main." >&2\n'
                    'echo "remote:   - Push cannot contain secrets" >&2\nexit 1\n')
    hook.chmod(0o700)
    result = await backup.push_now(pid)
    assert result['state'] == 'needs-attention' and result['reason'] == 'github-push-protection'
    assert git(remote, 'for-each-ref') == ''
    assert receipt(world)['state'] == 'waiting-for-deploy' and not backup.manual


@pytest.mark.asyncio
async def test_manual_push_never_forces_when_remote_diverged(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    commit(world.app, {'one.txt': '1'})
    await backup.push_now(pid)
    remote_head = git(remote, 'rev-parse', 'main')
    git(world.app, 'reset', '-q', '--hard', 'HEAD~1')
    commit(world.app, {'two.txt': '2'})
    result = await backup.push_now(pid)
    assert result['state'] == 'needs-attention' and result['reason'] == 'remote-history-diverged'
    assert git(remote, 'rev-parse', 'main') == remote_head


@pytest.mark.asyncio
async def test_background_job_stays_out_while_manual_push_runs(world, remote):
    backup = world.pipeline.backups
    pid = world.project['id']
    world.db.set_setting(PREFIX + pid, {'state': 'pending', 'target_commit': git(world.app, 'rev-parse', 'main'),
                                        'retry_at': 0, 'generation': 1})
    backup.manual.add(pid)
    backup.tick()
    assert backup.job is None
    backup.manual.clear()


@pytest.mark.asyncio
async def test_push_function_has_no_force_option():
    import inspect

    from ai_manager.github_backup import GitHubBackup
    source = inspect.getsource(GitHubBackup.push)
    assert '--force' not in source and '+refs' not in source and "'-f'" not in source


def test_endpoint_requires_owner_session_and_csrf(hub):
    browser, app, settings, github = hub
    project = create_offline(browser)['project']
    stale = browser.post(f"/api/projects/{project['id']}/push", headers={'X-CSRF-Token': secrets.token_hex(8)})
    assert stale.status_code == 403
    browser.cookies.clear()
    assert browser.post(f"/api/projects/{project['id']}/push").status_code == 401


def test_offline_projects_are_refused(hub):
    browser, app, settings, github = hub
    project = create_offline(browser)['project']
    refused = browser.post(f"/api/projects/{project['id']}/push")
    assert refused.status_code == 409 and 'offline' in refused.json()['detail'].lower()
    assert app.state.registry.get(project['id'])['github_backup'] == {}

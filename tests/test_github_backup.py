"""Synthetic credentials and disposable local remotes only; never contact GitHub."""
import asyncio
import json
import re
import threading
from pathlib import Path

import pytest
from test_core import git, run_next
from test_core import world as world_fixture

from ai_manager import github_backup as backup_module
from ai_manager.codex import RULES as MANAGER_RULES
from ai_manager.codex import WORKER_RULES
from ai_manager.github_backup import PREFIX, GitHubBackup, github_remote

world = world_fixture
TOKEN = 'ghp_' + 'A' * 36
PRIVATE = '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'


def commit(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'synthetic fixture')
    return git(root, 'rev-parse', 'HEAD')


def test_runtime_data_is_ignored_and_untracked():
    root = Path(__file__).resolve().parents[1]
    assert git(root, 'check-ignore', '--no-index', '.data/backup-regression.txt') == '.data/backup-regression.txt'
    assert git(root, 'ls-files', '--', '.data') == ''


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_delivery_merges_key_shaped_content_without_repair(world):
    world.deployable(False)

    async def implement(root, prompt):
        commit(root, {'.env': TOKEN + '\n', 'private.key': PRIVATE + '\n'})
        return 'implemented'

    world.lab.actions = [implement]
    task = world.tasks.add(world.project['id'], 'synthetic delivery')
    done = await run_next(world)
    assert done['id'] == task['id'] and done['state'] == 'done' and done['rounds'] == 0
    assert len(world.lab.turns) == 1
    assert (world.app / '.env').read_text() == TOKEN + '\n'
    assert (world.app / 'private.key').read_text() == PRIVATE + '\n'
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'pending'


@pytest.fixture
def remote(world, tmp_path, monkeypatch):
    bare = tmp_path / 'remote.git'
    bare.mkdir()
    git(bare, 'init', '-q', '--bare')
    git(world.app, 'remote', 'add', 'origin', str(bare))
    # Allow disposable local remotes in tests only; production requires a github.com URL.
    monkeypatch.setattr(backup_module, 'github_remote', lambda url: url == str(bare))
    return bare


@pytest.mark.asyncio
async def test_first_push_verifies_backlog_and_retry_includes_later_merges(world, remote):
    world.deployable(False)
    backlog = commit(world.app, {'backlog.txt': 'retained work\n'})
    git(world.app, 'config', 'push.followTags', 'true')
    git(world.app, 'tag', '-am', 'unrelated tag', 'local-only')
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "' + TOKEN + '" >&2\nexit 1\n')
    hook.chmod(0o700)
    backup = world.pipeline.backups
    backup.tick()
    await backup.job
    status = world.registry.get(world.project['id'])['github_backup']
    assert status['state'] == 'needs-attention' and status['retry_at'] > status['attempted_at']
    assert git(world.app, 'rev-parse', 'main') == backlog
    assert TOKEN not in json.dumps(world.db.all('SELECT * FROM settings'))
    assert TOKEN not in json.dumps(world.db.all("SELECT * FROM events WHERE kind='github_backup.result'"))
    old_job = backup.job
    backup.tick()
    assert backup.job is old_job  # retry deadline respected
    # The failure cannot stall queue execution, merge or the manager's deploy request.
    world.deployable(True)
    world.tasks.add(world.project['id'], 'next task')
    done = await run_next(world)
    assert done['state'] == 'done' and done['merge']['deploy']
    assert world.pipeline.pending()
    backup.tick()
    assert backup.job is old_job  # the merge is gated until deploy succeeds
    deploy_result(world, 'success', git(world.app, 'rev-parse', 'main'))
    hook.write_text('#!/bin/sh\nexit 0\n')
    backup.tick()  # successful deploy requests an immediate attempt
    await backup.job
    assert git(remote, 'rev-parse', 'main') == git(world.app, 'rev-parse', 'main')
    assert git(remote, 'merge-base', '--is-ancestor', backlog, 'main') == ''
    assert not git(remote, 'tag', '--list')
    assert world.registry.list()[0]['github_backup']['state'] == 'synced'


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_key_shaped_history_pushes_and_legacy_scan_failure_retries(world, remote, monkeypatch):
    world.deployable(False)
    commit(world.app, {'config.txt': TOKEN + '\n'})
    git(world.app, 'rm', 'config.txt')
    git(world.app, 'commit', '-qm', 'removed')
    head = commit(world.app, {'.env': 'sk-' + 'S' * 40, 'private.key': PRIVATE,
                              'config.txt': 'AKIA' + 'Z' * 16})
    # Existing receipts retry on their normal deadline and discard obsolete findings on success.
    world.db.set_setting(PREFIX + world.project['id'], {
        'state': 'needs-attention', 'reason': 'secret-check', 'retry_at': 123,
        'generation': 1, 'findings': [{'file': 'config.txt', 'line': 1, 'rule': 'github-token'}],
    })
    backup = world.pipeline.backups
    monkeypatch.setattr(backup_module.time, 'time', lambda: 124)
    backup.tick()
    await backup.job
    status = world.registry.get(world.project['id'])['github_backup']
    assert status['state'] == 'synced' and 'findings' not in status
    assert git(remote, 'rev-parse', 'main') == head
    assert git(remote, 'show', 'main:.env') == 'sk-' + 'S' * 40
    assert git(remote, 'show', 'main~2:config.txt') == TOKEN
    later = commit(world.app, {'later.txt': TOKEN})
    backup.request(world.project['id'])
    backup.tick()
    await backup.job
    assert git(remote, 'rev-parse', 'main') == later


@pytest.mark.asyncio
async def test_remote_divergence_does_not_overwrite_local_or_remote(world, remote, tmp_path):
    assert GitHubBackup.push(world.app)['state'] == 'synced'
    other = tmp_path / 'other'
    git(tmp_path, 'clone', '-q', '-b', 'main', str(remote), str(other))
    remote_head = commit(other, {'remote.txt': 'remote only\n'})
    git(other, 'push', '-q', 'origin', 'main')
    local_head = commit(world.app, {'local.txt': 'local only\n'})
    assert GitHubBackup.push(world.app)['reason'] == 'remote-history-diverged'
    assert git(world.app, 'rev-parse', 'main') == local_head
    assert git(remote, 'rev-parse', 'main') == remote_head


@pytest.mark.asyncio
async def test_backup_network_wait_does_not_delay_queue_or_merge(world, remote, monkeypatch):
    world.deployable(False)
    entered, release = threading.Event(), threading.Event()
    push = GitHubBackup.push

    def waiting(root):
        entered.set()
        assert release.wait(10)
        return push(root)

    monkeypatch.setattr(GitHubBackup, 'push', staticmethod(waiting))
    world.pipeline.backups.tick()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        world.tasks.add(world.project['id'], 'work during push')
        await asyncio.wait_for(world.core.runner.tick(), 5)
        await asyncio.wait_for(asyncio.gather(*list(world.core.jobs.values())), 10)
        assert world.tasks.list()[0]['state'] == 'done'
    finally:
        release.set()
        await world.pipeline.backups.close()
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'pending'
    world.pipeline.backups.tick()
    await world.pipeline.backups.close()
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'synced'


def test_github_destinations_only():
    assert github_remote('git@github.com:owner/repo.git')
    assert github_remote('https://github.com/owner/repo.git')
    assert github_remote('ssh://git@github.com/owner/repo.git')
    assert not github_remote('https://github.com.example/owner/repo.git')
    assert not github_remote('/tmp/repo.git')


@pytest.mark.parametrize('branch', ['main', 'master'])
def test_empty_repository_syncs_without_creating_history(tmp_path, monkeypatch, branch):
    root, remote = tmp_path / 'empty', tmp_path / 'empty.git'
    root.mkdir()
    remote.mkdir()
    git(root, 'init', '-q', '-b', branch)
    git(remote, 'init', '-q', '--bare')
    git(root, 'remote', 'add', 'origin', str(remote))
    monkeypatch.setattr(backup_module, 'github_remote', lambda url: url == str(remote))
    assert GitHubBackup.push(root) == {'state': 'synced', 'commit': None, 'reason': 'empty-repository'}
    assert git(root, 'for-each-ref') == git(remote, 'for-each-ref') == ''
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == branch
    # A subsequent first commit still takes the normal push path.
    head = commit(root, {'readme': 'first real work\n'})
    assert GitHubBackup.push(root) == {'state': 'synced', 'commit': head, 'reason': 'backlog-verified'}
    assert git(remote, 'rev-parse', branch) == head


def test_missing_main_does_not_hide_local_work_or_remote_history(tmp_path, monkeypatch):
    root, remote = tmp_path / 'empty', tmp_path / 'remote.git'
    root.mkdir()
    remote.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(remote, 'init', '-q', '--bare')
    git(root, 'remote', 'add', 'origin', str(remote))
    monkeypatch.setattr(backup_module, 'github_remote', lambda url: url == str(remote))
    (root / 'readme').write_text('uncommitted work\n')
    assert GitHubBackup.push(root)['reason'] == 'uncommitted-files'
    head = commit(root, {'readme': 'retained work\n'})
    git(root, 'branch', '-m', 'task')
    assert GitHubBackup.push(root)['reason'] == 'missing-main-branch'
    git(root, 'push', '-q', 'origin', 'task:main')
    # A fresh checkout must not claim that existing published history is backed up locally.
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    git(fresh, 'init', '-q', '-b', 'main')
    git(fresh, 'remote', 'add', 'origin', str(remote))
    assert GitHubBackup.push(fresh)['reason'] == 'local-history-missing'
    assert git(remote, 'rev-parse', 'main') == head


@pytest.mark.asyncio
async def test_timed_retry_survives_restart_and_covers_multiple_projects(world, remote, monkeypatch):
    world.deployable(False)
    backup = world.pipeline.backups
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o700)
    backup.tick()
    await backup.close()
    failed = world.registry.get(world.project['id'])['github_backup']
    restarted = GitHubBackup(world.db, world.registry)
    restarted.tick()
    assert restarted.job is None  # durable retry deadline
    second = world.app.parent / 'second'
    second.mkdir()
    git(second, 'init', '-q', '-b', 'main')
    commit(second, {'new.txt': 'work\n'})
    other_remote = second.parent / 'second.git'
    other_remote.mkdir()
    git(other_remote, 'init', '-q', '--bare')
    git(second, 'remote', 'add', 'origin', str(other_remote))
    project2 = world.registry.add(second)
    monkeypatch.setattr(backup_module, 'github_remote', lambda url: url in {str(remote), str(other_remote)})
    restarted.request(project2['id'])
    restarted.tick()
    await restarted.close()
    assert world.registry.get(project2['id'])['github_backup']['state'] == 'synced'
    hook.write_text('#!/bin/sh\nexit 0\n')
    monkeypatch.setattr(backup_module.time, 'time', lambda: failed['retry_at'] + 1)
    restarted.tick()
    await restarted.close()
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'synced'
    assert git(remote, 'rev-parse', 'main') == git(world.app, 'rev-parse', 'main')


def test_ui_exposes_safe_backup_status(world):
    from pathlib import Path

    status = {'state': 'needs-attention', 'reason': 'remote-history-diverged', 'retry_at': 123}
    world.db.set_setting(PREFIX + world.project['id'], status)
    assert world.registry.get(world.project['id'])['github_backup'] == status
    assert world.registry.list()[0]['github_backup'] == status
    source = (Path(__file__).parents[1] / 'ai_manager/static/app.js').read_text()
    assert "p?.github_backup?.state==='needs-attention'" in source
    assert 'GitHub waiting for deploy' in source
    assert 'GitHub commit:' in source
    assert 'GitHub needs attention' in source
    assert 'Local commits retained; retrying automatically.' in source


@pytest.mark.parametrize('rules', [
    (Path(__file__).parent.parent / 'RULES.md').read_text(), MANAGER_RULES, WORKER_RULES,
], ids=['repository', 'manager', 'worker'])
def test_rules_require_real_credentials_outside_git_and_runtime_key_fixtures(rules):
    # Later rules and role markers are not part of the credential rule.
    match = re.search(r'(?ms)^10\. (.*?)(?=^\d+\. |^<!--|\Z)', rules)
    assert match is not None, 'Credential rule 10 must be present'
    rule10 = ' '.join(match[1].split())
    assert 'Never commit real credentials' in rule10
    assert 'environment variables or ignored files' in rule10
    assert 'Tests needing key-shaped values build them at runtime' in rule10
    assert len(rule10.split()) < 30
    assert 'Application checks do not scan project content for credentials' in rules
    readme = ' '.join((Path(__file__).parent.parent / 'README.md').read_text().split())
    assert 'Never commit real credentials' in readme
    assert 'Tests needing key-shaped values build them at runtime' in readme


def deploy_result(world, status, head, receipt_id='deploy-test'):
    from ai_manager.pipeline import atomic
    (world.pipeline.root / 'request.json').unlink(missing_ok=True)
    atomic(world.pipeline.root / 'result.json', {
        'id': receipt_id, 'project_id': world.project['id'], 'status': status,
        'commit': head, 'reason': 'fixture deploy outcome',
    })
    return world.pipeline.report()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_deploy_gates_merge_and_pushes_receipt_commit(world, remote):
    backup = world.pipeline.backups
    backup.tick()  # startup must also wait
    assert backup.job is None
    world.tasks.add(world.project['id'], 'deploy this change')
    done = await run_next(world)
    assert done['merge']['deploy']
    deployed = git(world.app, 'rev-parse', 'main')
    backup.tick()
    assert backup.job is None and git(remote, 'for-each-ref') == ''
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'waiting-for-deploy'
    deploy_result(world, 'success', deployed)
    newer = commit(world.app, {'not-deployed.txt': 'later change'})
    backup.tick()
    await backup.close()
    status = world.registry.get(world.project['id'])['github_backup']
    assert git(remote, 'rev-parse', 'main') == status['commit'] == deployed != newer
    assert world.pipeline.report() is None  # receipt is consumed exactly once


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['failed', 'rolled_back', 'rollback_failed', 'cancelled'])
async def test_failed_deploy_never_pushes_then_successful_repair_does(world, remote, outcome):
    backup = world.pipeline.backups
    world.tasks.add(world.project['id'], 'change')
    await run_next(world)
    failed = git(world.app, 'rev-parse', 'main')
    deploy_result(world, outcome, failed)
    backup.tick()
    assert backup.job is None and git(remote, 'for-each-ref') == ''
    repaired = commit(world.app, {'repair.txt': 'fixed'})
    deploy_result(world, 'success', repaired, 'repair-deploy')
    backup.tick()
    await backup.close()
    assert git(remote, 'rev-parse', 'main') == repaired


@pytest.mark.asyncio
async def test_legacy_pending_backup_with_setup_waits_for_deploy(world, remote):
    world.db.set_setting(PREFIX + world.project['id'], {'state': 'pending', 'generation': 1})
    backup = GitHubBackup(world.db, world.registry)
    backup.tick()
    assert backup.job is None and git(remote, 'for-each-ref') == ''
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'waiting-for-deploy'


@pytest.mark.asyncio
async def test_verified_retry_survives_restart_and_newer_main(world, remote, monkeypatch):
    deployed = git(world.app, 'rev-parse', 'main')
    deploy_result(world, 'success', deployed)
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o700)
    backup = world.pipeline.backups
    backup.tick()
    await backup.close()
    state = world.registry.get(world.project['id'])['github_backup']
    assert state['state'] == 'needs-attention' and state['target_commit'] == deployed
    commit(world.app, {'later.txt': 'unverified'})
    hook.write_text('#!/bin/sh\nexit 0\n')
    monkeypatch.setattr(backup_module.time, 'time', lambda: state['retry_at'] + 1)
    restarted = GitHubBackup(world.db, world.registry)
    restarted.tick()
    await restarted.close()
    assert git(remote, 'rev-parse', 'main') == deployed


@pytest.mark.asyncio
async def test_push_finishing_during_merge_preserves_waiting_state(world, remote, monkeypatch):
    backup = world.pipeline.backups
    deployed = git(world.app, 'rev-parse', 'main')
    deploy_result(world, 'success', deployed)
    entered, release = threading.Event(), threading.Event()
    push = GitHubBackup.push

    def waiting(root, **kwargs):
        entered.set()
        assert release.wait(10)
        return push(root, **kwargs)

    monkeypatch.setattr(GitHubBackup, 'push', staticmethod(waiting))
    backup.tick()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        world.tasks.add(world.project['id'], 'new merge')
        await run_next(world)
    finally:
        release.set()
        await backup.close()
    state = world.registry.get(world.project['id'])['github_backup']
    assert state['state'] == 'waiting-for-deploy' and state['commit'] == deployed
    assert git(remote, 'rev-parse', 'main') == deployed
    job = backup.job
    backup.tick()
    assert backup.job is job


def test_backup_request_migrates_installed_setup_before_write_transaction(world):
    from ai_manager.pipeline import atomic
    pid = world.project['id']
    world.db.run('DELETE FROM settings WHERE key=?', ('deploy_setup:' + pid,))
    config = {'repo': str(world.app), 'data': str(world.settings.data_dir)}
    atomic(world.pipeline.root / 'installed.json', config)
    world.pipeline.backups.request(pid)
    assert world.pipeline.setup(pid) == {'runtime': config}
    assert world.registry.get(pid)['github_backup']['state'] == 'waiting-for-deploy'

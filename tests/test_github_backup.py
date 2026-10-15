"""Synthetic credentials and disposable local remotes only; never contact GitHub."""
import asyncio
import json
import subprocess
import threading

import pytest
from test_core import git, run_next
from test_core import world as world_fixture

from ai_manager import github_backup as backup_module
from ai_manager.github_backup import PREFIX, GitHubBackup, github_remote
from ai_manager.secret_check import ALLOWLIST, RULES, scan

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


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    base = commit(root, {'readme': 'initial\n'})
    return root, base


def test_builtin_detection_without_optional_scanner(repository, monkeypatch):
    root, base = repository
    # Only Git is needed. No gitleaks invocation, download or dependency is permitted.
    original = subprocess.run

    def only_git(argv, **kwargs):
        assert argv[0] == 'git'
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, 'run', only_git)
    head = commit(root, {'nested/config.txt': '\n' + TOKEN + '\n' + PRIVATE + '\n'
                        + 'AKIA' + 'Z' * 16 + '\npassword = "' + 'synthetic-credential' + '"\n'
                        + 'sk-' + 'S' * 40 + '\nAPI_KEY=' + 'unquoted_fixture' * 2 + '\n',
                         '.env': 'fixture\n', '.env.example': 'example\n', 'nested/.env.local': 'fixture', 'tls.pem': 'fixture',
                         'cert.key': 'fixture', 'id_rsa.pub': 'fixture'})
    hits = scan(root, base, head)
    assert {h['rule'] for h in hits} == {*RULES, 'sensitive-filename'}
    assert {h['file'] for h in hits if h['rule'] == 'sensitive-filename'} == {
        '.env', 'nested/.env.local', 'tls.pem', 'cert.key', 'id_rsa.pub'}
    assert {'file': 'nested/config.txt', 'line': 2, 'rule': 'github-token'} in hits
    assert all(set(h) == {'file', 'line', 'rule'} for h in hits)
    assert TOKEN not in json.dumps(hits) and PRIVATE not in json.dumps(hits)


def test_only_new_commit_lines_but_includes_deleted_later_secrets(repository):
    root, _ = repository
    base = commit(root, {'old.txt': TOKEN + '\nnormal\n'})
    head = commit(root, {'old.txt': TOKEN + '\nchanged\n'})
    assert scan(root, base, head) == []  # unchanged old credentials are outside delivery scope
    bad = commit(root, {'temporary.txt': TOKEN + '\n'})
    git(root, 'rm', 'temporary.txt')
    git(root, 'commit', '-qm', 'remove later')
    assert scan(root, base, 'HEAD') == [{'file': 'temporary.txt', 'line': 1, 'rule': 'github-token'}]
    assert scan(root, bad, 'HEAD') == []


def test_exact_fixture_allowlist_and_invalid_policy(repository):
    root, base = repository
    entries = [{'path': 'fixtures/token.txt', 'line': 1, 'rule': 'github-token', 'reason': 'synthetic fixture'},
               {'path': 'fixtures/test.key', 'line': 0, 'rule': 'sensitive-filename', 'reason': 'empty fixture'}]
    head = commit(root, {'fixtures/token.txt': TOKEN + '\n', 'fixtures/test.key': '',
                         ALLOWLIST: json.dumps(entries)})
    assert scan(root, base, head) == []
    head = commit(root, {'real/token.txt': TOKEN + '\n', 'fixtures/token.txt': TOKEN + '\n' + TOKEN + '\n'})
    assert {(h['file'], h['line']) for h in scan(root, base, head)} == {
        ('real/token.txt', 1), ('fixtures/token.txt', 2)}
    head = commit(root, {ALLOWLIST: '[{"path":"*"}]'})
    assert scan(root, base, head) == [{'file': ALLOWLIST, 'line': 0, 'rule': 'invalid-allowlist'}]


def test_merge_inherited_main_lines_excluded_and_resolution_checked(repository):
    root, base = repository
    git(root, 'checkout', '-qb', 'task')
    commit(root, {'feature.txt': 'feature\n'})
    git(root, 'checkout', '-q', 'main')
    commit(root, {'existing.txt': TOKEN + '\n'})
    git(root, 'checkout', '-q', 'task')
    git(root, 'merge', '--no-edit', 'main')
    assert scan(root, 'main', 'HEAD') == []
    git(root, 'checkout', '-q', 'main')
    commit(root, {'readme': 'main edit\n'})
    git(root, 'checkout', '-q', 'task')
    commit(root, {'readme': 'branch edit\n'})
    result = subprocess.run(['git', '-C', str(root), 'merge', 'main'], capture_output=True, timeout=60)
    assert result.returncode != 0
    commit(root, {'readme': TOKEN + '\n'})
    assert {'file': 'readme', 'line': 1, 'rule': 'github-token'} in scan(root, 'main', 'HEAD')


@pytest.mark.asyncio
async def test_secret_hits_use_normal_repair_and_never_disclose_values(world):
    world.deployable(False)

    async def bad(root, prompt):
        commit(root, {'config.txt': TOKEN + '\n'})
        return 'implemented'

    async def repair(root, prompt):
        assert prompt['repair']['failed_checks'] == ['secrets']
        assert TOKEN not in json.dumps(prompt)
        (root / 'config.txt').write_text('safe\n')
        git(root, 'add', '-A')
        git(root, 'commit', '--amend', '--no-edit')
        return 'repaired'

    world.lab.actions = [bad, repair]
    task = world.tasks.add(world.project['id'], 'synthetic delivery')
    done = await run_next(world)
    assert done['id'] == task['id'] and done['state'] == 'done' and done['rounds'] == 1
    assert TOKEN not in json.dumps(done['history'])
    assert world.registry.get(world.project['id'])['github_backup']['state'] == 'pending'


@pytest.mark.asyncio
async def test_secret_repair_budget_is_bounded(world):
    world.deployable(False)

    async def bad(root, prompt):
        commit(root, {'config.txt': PRIVATE + '\n'})
        return 'implemented'

    world.lab.actions = [bad]
    world.tasks.add(world.project['id'], 'never repaired')
    done = await run_next(world)
    assert done['state'] == 'failed' and done['rounds'] == 5
    assert len(world.lab.turns) == 6
    assert PRIVATE not in json.dumps(done['history'])
    assert not (world.app / 'config.txt').exists()


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
    hook.write_text('#!/bin/sh\nexit 0\n')
    backup.tick()  # new merge also requests an immediate attempt, even with a deploy pending
    await backup.job
    assert git(remote, 'rev-parse', 'main') == git(world.app, 'rev-parse', 'main')
    assert git(remote, 'merge-base', '--is-ancestor', backlog, 'main') == ''
    assert not git(remote, 'tag', '--list')
    assert world.registry.list()[0]['github_backup']['state'] == 'synced'


@pytest.mark.asyncio
async def test_existing_unsafe_history_never_pushed_even_when_deleted(world, remote):
    commit(world.app, {'config.txt': TOKEN + '\n'})
    git(world.app, 'rm', 'config.txt')
    git(world.app, 'commit', '-qm', 'removed')
    backup = world.pipeline.backups
    backup.tick()
    await backup.job
    status = world.registry.get(world.project['id'])['github_backup']
    assert status['state'] == 'needs-attention' and status['reason'] == 'secret-check'
    assert status['findings'] == [{'file': 'config.txt', 'line': 1, 'rule': 'github-token'}]
    assert TOKEN not in json.dumps(status)
    assert not git(remote, 'for-each-ref')


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


@pytest.mark.asyncio
async def test_timed_retry_survives_restart_and_covers_multiple_projects(world, remote, monkeypatch):
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


def test_allowlist_does_not_cross_repositories(world, repository):
    root, base = repository
    entries = [{'path': 'fixture.txt', 'line': 1, 'rule': 'github-token', 'reason': 'synthetic fixture'}]
    commit(world.app, {ALLOWLIST: json.dumps(entries)})
    head = commit(root, {'fixture.txt': TOKEN + '\n'})
    assert scan(root, base, head) == [{'file': 'fixture.txt', 'line': 1, 'rule': 'github-token'}]


def test_ui_exposes_safe_backup_status(world):
    from pathlib import Path

    status = {'state': 'needs-attention', 'reason': 'secret-check', 'retry_at': 123,
              'findings': [{'file': 'config', 'line': 1, 'rule': 'github-token'}]}
    world.db.set_setting(PREFIX + world.project['id'], status)
    assert world.registry.get(world.project['id'])['github_backup'] == status
    assert world.registry.list()[0]['github_backup'] == status
    source = (Path(__file__).parents[1] / 'ai_manager/static/app.js').read_text()
    assert "p?.github_backup?.state==='needs-attention'" in source
    assert 'GitHub needs attention' in source
    assert 'Local commits retained; retrying automatically.' in source

"""Incoming sync failures keep the failing operation and a redacted cause through the persisted status and API."""
import json

import pytest
from test_core import git
from test_github_sync import advance, behind, origin as origin_fixture
from test_github_sync import client as client_fixture
from test_github_sync import world as world_fixture
from test_web import sign_in

from ai_manager import github_sync
from ai_manager.git_commands import GitError
from ai_manager.push_diagnosis import diagnose_incoming

origin, world, client = origin_fixture, world_fixture, client_fixture


def token():
    return 'ghp_' + 'Ab3dE6gH9jK2'.lower() * 3  # key-shaped, built at runtime


def stored(w):
    return w.pipeline.sync.status(w.project['id'])


def failing(monkeypatch, command, output):
    original = github_sync.git_bytes
    calls = []

    def wrapper(root, *args, **kwargs):
        if args[0] == command:
            calls.append(args)
            raise GitError(output=output)
        return original(root, *args, **kwargs)
    monkeypatch.setattr(github_sync, 'git_bytes', wrapper)
    return calls


CASES = [
    ('fetch', 'fatal: Authentication failed for https://github.com/o/r.git/', 'auth-denied'),
    ('fetch', "fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 403", 'auth-denied'),
    ('fetch', 'ERROR: Permission denied (publickey).', 'auth-denied'),
    ('fetch', "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com", 'network'),
    ('fetch', 'git operation timed out', 'network'),
    ('fetch', 'remote: Repository not found.\nfatal: repository not found', 'repository-not-found'),
    ('fetch', "fatal: couldn't find remote ref refs/heads/main", 'missing-main'),
    ('fetch', 'something unrecognised happened', 'unknown'),
    ('merge', 'error: Your local changes to the following files would be overwritten by merge:\n\tshared.txt', 'local-changes'),
    ('merge', 'fatal: Not possible to fast-forward, aborting.', 'not-fast-forward'),
    ('rev-parse', "fatal: ambiguous argument 'refs/remotes/origin/main': unknown revision", 'missing-ref'),
]


@pytest.mark.parametrize('command,output,cause', CASES)
async def test_failure_names_operation_and_cause(origin, monkeypatch, command, output, cause):
    behind(origin)
    calls = failing(monkeypatch, command, output)
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'warning' and result['cause'] == cause
    assert result['operation'] == {'fetch': 'fetch', 'merge': 'merge', 'rev-parse': 'refs'}[command]
    assert stored(origin)['cause'] == cause and stored(origin)['reason'] == result['reason']
    assert 'push' not in result['reason'].lower()
    if command == 'fetch':
        assert len(calls) == 2 and 'after 2 attempts' in result['reason']  # only fetch is retried
    else:
        assert len(calls) == 1 and 'attempts' not in result['reason']
    if cause == 'unknown':
        assert 'something unrecognised happened' in result['reason']
    assert git(origin.app, 'status', '--porcelain') == ''


async def test_missing_repository_and_missing_main_against_real_origins(origin, monkeypatch, tmp_path):
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: True)
    git(origin.app, 'remote', 'set-url', 'origin', str(origin.bare / 'missing'))
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert (result['operation'], result['cause']) == ('fetch', 'repository-not-found')
    other = tmp_path / 'dev-only.git'
    git(tmp_path, 'init', '--bare', '-q', str(other))
    git(origin.app, 'push', '-q', str(other), 'main:dev')
    git(origin.app, 'remote', 'set-url', 'origin', str(other))
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert (result['operation'], result['cause']) == ('fetch', 'missing-main')
    # The next successful sync clears every trace of the failure.
    git(origin.app, 'remote', 'set-url', 'origin', str(origin.bare))
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: url == str(origin.bare))
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'synced'
    assert set(stored(origin)) == {'state', 'reason', 'attempted_at'}


async def test_real_checkout_lock_fails_merge_without_touching_files(origin):
    before = git(origin.app, 'rev-parse', 'HEAD')
    behind(origin)
    (origin.app / '.git' / 'index.lock').write_text('')
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert (result['operation'], result['cause']) == ('merge', 'local-lock')
    assert git(origin.app, 'rev-parse', 'HEAD') == before
    (origin.app / '.git' / 'index.lock').unlink()
    assert (await origin.pipeline.sync.attempt(origin.project['id']))['state'] == 'updated'
    assert 'cause' not in stored(origin)


async def test_secrets_never_reach_the_persisted_status(origin, monkeypatch):
    secret = token()
    behind(origin)
    origin.db.set_setting('github_tier:' + origin.project['id'], 'personal')
    origin.pipeline.backups.credentials = lambda project: secret
    url = f'https://user:{secret}@github.com/o/r.git'
    failing(monkeypatch, 'fetch', f"fatal: unable to access '{url}': The requested URL returned error: 403\nAuthorization: Bearer {secret}\n"
                                  f'GITHUB_TOKEN={secret}\n' + 'x' * 5000)
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['cause'] == 'auth-denied'
    dumped = json.dumps(stored(origin)) + json.dumps(result)
    assert secret not in dumped and 'user:' not in dumped
    assert len(dumped) < 2000


def test_unknown_output_is_bounded_and_redacted():
    secret = token()
    raw = '\n'.join(f'line {i} ' + 'y' * 400 for i in range(50)) + f'\nsecret {secret}\n'
    found = diagnose_incoming('fetch', raw.encode(), [secret], attempts=2)
    assert found['cause'] == 'unknown' and len(found['message']) <= 400
    assert len(found['excerpt']) <= 3 and all(len(line) <= 200 for line in found['excerpt'])
    assert secret not in json.dumps(found)
    assert 'could not classify' in diagnose_incoming('merge', b'')['message']


async def test_unexpected_error_is_labelled_and_redacted(origin, monkeypatch):
    secret = token()

    def boom(sync, project_id):
        raise RuntimeError(f'oops {secret}')
    monkeypatch.setattr(github_sync.GitHubSync, 'pull', boom)
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['operation'] == 'update' and result['cause'] == 'unexpected-error'
    assert 'RuntimeError' in result['reason'] and secret not in json.dumps(stored(origin))


async def test_busy_is_informational_and_accurate(origin):
    behind(origin)
    origin.tasks.add(origin.project['id'], 'busy')
    origin.tasks.claim()
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result == {'state': 'busy', 'reason': 'Work or deploy is active; update deferred.'}
    assert 'cause' not in stored(origin) and 'operation' not in stored(origin)


@pytest.mark.parametrize('count', [2, 8])
async def test_dirty_checkout_names_bounded_paths_and_keeps_files(origin, count):
    before = git(origin.app, 'rev-parse', 'HEAD')
    behind(origin)
    secret = token()
    names = [f'note{i}.txt' for i in range(count - 1)] + [secret + '.txt']
    for name in names:
        (origin.app / name).write_text('private contents ' + secret)
    result = await origin.pipeline.sync.attempt(origin.project['id'])
    assert result['state'] == 'warning' and result['cause'] == 'uncommitted-changes'
    assert result['path_count'] == count and len(result['paths']) == min(count, 5)
    assert 'private contents' not in json.dumps(result) and secret not in json.dumps(result)
    assert ('and 3 more' in result['reason']) == (count == 8)
    assert all((origin.app / name).read_text().startswith('private contents') for name in names)
    assert git(origin.app, 'rev-parse', 'HEAD') == before
    # A tracked modification is named too.
    (origin.app / 'shared.txt').write_text('edited')
    assert 'shared.txt' in dirty(origin.app)


def dirty(root):
    return ', '.join(github_sync.dirty_paths(root))


def test_failure_is_visible_through_the_portfolio_api(client, tmp_path, monkeypatch):
    browser, app, settings = client
    sign_in(browser, app, settings)
    monkeypatch.setattr(app.state.github_projects.pipeline.sync, 'tick', lambda: None)
    root = settings.project_roots[0] / 'one'
    git(root, 'init', '-q', '-b', 'main')
    advance(root, 'base')
    git(root, 'remote', 'add', 'origin', str(tmp_path / 'missing.git'))
    project = app.state.registry.add(root)
    monkeypatch.setattr(github_sync, 'github_remote', lambda url: True)
    response = browser.post(f"/api/projects/{project['id']}/pull")
    assert response.status_code == 200 and response.json()['operation'] == 'fetch'
    listed = next(p for p in browser.get('/api/portfolio').json()['projects'] if p['id'] == project['id'])
    assert listed['github_sync']['cause'] == 'repository-not-found'
    assert 'fetch failed after 2 attempts' in listed['github_sync']['reason']

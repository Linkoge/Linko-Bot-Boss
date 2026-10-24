"""Worker-check secret scan and automatic push-protection recovery. Key-shaped values are built at runtime; GitHub is mocked."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_core import git
from test_core import world as world_fixture  # noqa: F401

from ai_manager.core import secret_check
from ai_manager.github_backup import GitHubBackup
from ai_manager.push_diagnosis import diagnose

world = world_fixture
ROOT = Path(__file__).resolve().parents[1]
FAKE = 'ghp_' + 'Q' * 36


def gh013(*paths):
    unblock = 'https://github.com/acme/app/security/secret-scanning/unblock-secret/2abcDEF123xyz'
    locations = ''.join(f"remote:          - commit: {'a' * 40}\nremote:            path: {p}\n" for p in paths)
    return diagnose('remote: error: GH013: Repository rule violations found\n'
                    'remote:       —— GitHub Personal Access Token ————————\n'
                    f'remote:        locations:\n{locations}remote:        {unblock}\n')


def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    (root / 'a.txt').write_text('x\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'base')
    return root, git(root, 'rev-parse', 'HEAD')


def add(root, text):
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / 'sample.txt').write_text(text)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'more')


def test_secret_check_fails_on_literal_with_location_and_rule(tmp_path):
    root, base = repo(tmp_path)
    add(root, 'a\n' + FAKE + '\n')
    result = secret_check(root, base)
    assert result['check'] == 'secrets'
    assert 'tests/sample.txt:2' in result['failures'][0]
    assert 'build key-shaped test values at runtime (rule 10)' in result['failures'][0]
    assert FAKE not in json.dumps(result)


def test_secret_check_passes_on_runtime_built_values(tmp_path):
    root, base = repo(tmp_path)
    add(root, "token = 'ghp_' + 'Q' * 36\n")
    assert secret_check(root, base) is None


def test_worker_check_script_exits_nonzero_on_literal(tmp_path):
    root, base = repo(tmp_path)
    add(root, FAKE + '\n')
    run = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'worker_check.py'), '--root', str(root), '--base', base],
                         capture_output=True, text=True, timeout=120)
    report = json.loads(run.stdout)
    assert run.returncode == 1 and report['ok'] is False and 'secrets' in report['checks']


class Recorder:
    def __init__(self, status=201):
        self.calls, self.status = [], status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, json.loads(body)))
        return self.status, {}, None


def setup_backup(world, outcomes, status=201):
    backup, pid = world.pipeline.backups, world.project['id']
    world.db.set_setting('github_tier:' + pid, 'personal')
    backup.credentials = lambda project: 'tok'
    backup.fetch = Recorder(status)
    pushes = []

    def push(root, token, **extra):
        pushes.append(extra)
        return outcomes.pop(0)
    backup.push_with_token = staticmethod(push)
    return backup, pid, pushes


def notices(world):
    return [r['content'] for r in world.db.all("SELECT content FROM messages WHERE id LIKE 'pushblock-%'")]


@pytest.mark.asyncio
async def test_tests_only_hits_bypass_once_and_retry_once(world):
    synced = {'state': 'synced', 'commit': 'c' * 40, 'reason': 'backlog-verified'}
    backup, pid, pushes = setup_backup(world, [gh013('tests/test_a.py:3'), dict(synced)])
    result = await backup.run_push(world.registry.get(pid))
    assert result['state'] == 'synced' and len(pushes) == 2
    assert backup.fetch.calls == [('POST', 'https://api.github.com/repos/acme/app/secret-scanning/push-protection-bypasses',
                                   {'reason': 'used_in_tests', 'placeholder_id': '2abcDEF123xyz'})]
    assert result['bypass']['ok'] and result['bypass']['hits'][0]['path'] == 'tests/test_a.py'
    assert result['bypass']['hits'][0]['line'] == 3 and result['bypass']['hits'][0]['commit']
    assert notices(world) == []


@pytest.mark.asyncio
async def test_non_test_hit_is_not_bypassed_and_notified_once(world):
    blocked = gh013('tests/test_a.py:3', 'src/config.py:7')
    backup, pid, pushes = setup_backup(world, [dict(blocked), dict(blocked)])
    for _ in range(2):
        result = await backup.run_push(world.registry.get(pid))
        assert result['reason'] == 'github-push-protection' and 'src/config.py:7' in result['message']
    assert backup.fetch.calls == [] and len(pushes) == 2
    assert len(notices(world)) == 1 and 'src/config.py:7' in notices(world)[0]


@pytest.mark.asyncio
async def test_bypass_failure_keeps_needs_attention_and_is_not_repeated(world):
    blocked = gh013('tests/test_a.py:3')
    backup, pid, pushes = setup_backup(world, [dict(blocked), dict(blocked)], status=403)
    first = await backup.run_push(world.registry.get(pid))
    second = await backup.run_push(world.registry.get(pid))
    assert first['reason'] == second['reason'] == 'github-push-protection'
    assert first['bypass'] == {'attempted': True, 'ok': False, 'reason': 'used_in_tests', 'status': 403}
    assert len(backup.fetch.calls) == 1 and len(pushes) == 2  # no retry push, no second bypass
    assert len(notices(world)) == 1


def test_no_secret_values_stored(world):
    assert FAKE not in json.dumps(gh013('tests/test_a.py:3'))
    assert isinstance(GitHubBackup.blocked(gh013('tests/test_a.py:3'))[2], str)

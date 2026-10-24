"""Fixture Git outputs only (key-shaped values are built at runtime); never contacts GitHub."""
import json
from pathlib import Path

import pytest
from test_core import git
from test_core import world as world_fixture
from test_github_backup import PREFIX, commit, remote  # noqa: F401  (fixture reuse)

from ai_manager.push_diagnosis import clean_output, diagnose, precheck, redact

world = world_fixture
SECRET = 'ghp_' + 'Q' * 36
UNBLOCK = 'https://github.com/acme/app/security/secret-scanning/unblock-secret/2abcDEF123xyz'
GH013 = f"""remote: error: GH013: Repository rule violations found for refs/heads/main.
remote:
remote: - GITHUB PUSH PROTECTION
remote:   —————————————————————————————————————————
remote:     Resolve the following violations before pushing again
remote:
remote:     - Push cannot contain secrets
remote:
remote:      (?) Learn how to resolve a blocked push
remote:      https://docs.github.com/code-security/secret-scanning/working-with-secret-scanning-and-push-protection
remote:
remote:       —— GitHub Personal Access Token ————————————————————
remote:        locations:
remote:          - commit: abc1234def5678abc1234def5678abc1234def56
remote:            path: tests/foo.py:18
remote:          - commit: 9999999999999999999999999999999999999999
remote:            path: src/config.py:7
remote:
remote:        (?) To push, remove secret from commit(s) or follow this URL to allow the secret.
remote:        {UNBLOCK}
remote:
remote:       —— Stripe API Key ——————————————————————————————————
remote:        locations:
remote:          - commit: abc1234def5678abc1234def5678abc1234def56
remote:            path: .env:2
remote:
To github.com:acme/app.git
 ! [remote rejected] abc:main -> main (push declined due to repository rule violations)
error: failed to push some refs to 'github.com:acme/app.git'
"""


def test_gh013_with_multiple_locations():
    result = diagnose(GH013)
    assert result['reason'] == 'github-push-protection'
    detail = result['detail']
    assert detail['rule'] == 'GH013' and detail['unblock_urls'] == [UNBLOCK]
    assert [s['type'] for s in detail['secrets']] == ['GitHub Personal Access Token', 'Stripe API Key']
    assert detail['secrets'][0]['locations'] == [
        {'commit': 'abc1234def56', 'path': 'tests/foo.py', 'line': 18},
        {'commit': '999999999999', 'path': 'src/config.py', 'line': 7}]
    assert detail['secrets'][1]['locations'] == [{'commit': 'abc1234def56', 'path': '.env', 'line': 2}]
    assert 'GitHub Personal Access Token in tests/foo.py:18 (commit abc1234def56)' in result['message']
    assert result['message'].startswith('GitHub blocked the push: secret scanning found') and UNBLOCK in result['message']


@pytest.mark.parametrize('output, kind', [
    (" ! [rejected]        a:main -> main (non-fast-forward)\nerror: failed to push some refs", 'rejected-non-fast-forward'),
    ("remote: Invalid username or password.\nfatal: Authentication failed for 'https://github.com/a/b.git/'", 'auth-denied'),
    ("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.", 'auth-denied'),
    ("remote: Repository not found.\nfatal: repository 'https://github.com/a/b.git/' not found", 'repository-not-found'),
    ("fatal: unable to access 'https://github.com/a/b.git/': Could not resolve host: github.com", 'network'),
    ("git operation timed out", 'network'),
    ("remote: error: pre-receive hook declined\n ! [remote rejected] a:main -> main (pre-receive hook declined)", 'hook-declined'),
    ("something odd happened", 'git-push-or-verification-failed'),
])
def test_other_failures_are_classified(output, kind):
    result = diagnose(output)
    assert result['detail']['kind'] == (kind if kind != 'git-push-or-verification-failed' else 'unknown')
    assert result['reason'] == kind and result['message']


def test_redaction_masks_key_shaped_values():
    values = [SECRET, 'github_pat_' + 'a1' * 20, 'sk-' + 'x' * 30, 'xai-' + 'y' * 30, 'AKIA' + 'B' * 16,
              'MY_API_KEY=hunter2hunter2', 'https://user:pw@github.com/a/b', 'f' * 40 + 'e' * 30]
    for value in values:
        assert value.split('=')[-1].split(':')[-1] not in redact(f'remote: line {value} end').replace('MY_API_KEY=***', '')
    pem = '-----BEGIN ' + 'RSA PRIVATE KEY-----\nMIIEabc\n-----END ' + 'RSA PRIVATE KEY-----'
    assert 'MIIE' not in redact(pem)
    assert 'tok' not in redact('push with tok123', ['tok123'])


def test_secret_never_reaches_output_of_diagnosis():
    noisy = GH013 + f'remote: leaked {SECRET}\nremote: TOKEN=abc{SECRET}\n'
    result = diagnose(clean_output(noisy.encode()))
    assert SECRET not in json.dumps(result)
    assert len(result['excerpt']) <= 3


def test_precheck_reports_location_not_value():
    diff = (f'@@commit {"c" * 40}\n+++ b/tests/foo.py\n@@ -0,0 +5,2 @@\n+x = 1\n+key = "{SECRET}"\n').encode()
    assert precheck(diff) == [{'type': 'GitHub token', 'path': 'tests/foo.py', 'line': 6, 'commit': 'c' * 12}]


@pytest.mark.asyncio
async def test_failed_push_stores_only_parsed_reason(world, remote):  # noqa: F811
    world.deployable(False)
    commit(world.app, {'tests/foo.py': 'line\nkey = "' + SECRET + '"\n'})
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\ncat >&2 <<\'EOF\'\n' + GH013.replace('remote: ', '') + f'echo {SECRET}\n'
                    f'{SECRET}\nEOF\nexit 1\n')
    hook.chmod(0o700)
    backup = world.pipeline.backups
    backup.tick()
    await backup.job
    status = world.registry.get(world.project['id'])['github_backup']
    assert status['state'] == 'needs-attention'
    assert status['reason'] == 'github-push-protection'
    assert status['detail']['secrets'][0]['locations'][0]['path'] == 'tests/foo.py'
    assert [(w['path'], w['line']) for w in status['warnings']] == [('tests/foo.py', 2)]
    assert 'GitHub blocked the push' in status['message'] and UNBLOCK in status['message']
    events = world.db.all("SELECT * FROM events WHERE kind='github_backup.result'")
    assert 'github-push-protection' in json.dumps(events)
    for blob in (world.db.all('SELECT * FROM settings'), events, status, world.registry.list()):
        assert SECRET not in json.dumps(blob, default=str)


def test_ui_shows_parsed_reason():
    source = (Path(__file__).parents[1] / 'ai_manager/static/app.js').read_text()
    assert 'g.message||g.reason' in source and 'backupWarning' in source

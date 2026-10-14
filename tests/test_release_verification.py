"""Focused policy regression: selection, scope floors and exact attestations."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from ai_manager.deployment import release_attested
from ai_manager.verification import BROWSERS, release_policy, review_reasons
from scripts.prepare_deployment import checks, selected_policy


@pytest.mark.parametrize('paths', [['docs/x.md'], ['tests/test_verification.py'],
                                  ['ai_manager/static/style.css'], ['ai_manager/static/app.js']])
def test_small_release_omits_full_suites_and_retains_smoke_and_targeted(paths):
    policy = release_policy(paths, [{'tier': 'small'}])
    commands = checks(Path(sys.executable), policy)
    assert policy['tier'] == 'small' and not policy['full_backend']
    assert [sys.executable, '-m', 'pytest', '-q'] not in commands
    assert [sys.executable, '-m', 'pytest', '-q', '-m', 'smoke'] in commands
    assert len(policy['browsers']) < len(BROWSERS)
    if paths[0].startswith('tests/'):
        assert [sys.executable, '-m', 'pytest', '-q', paths[0]] in commands
    if paths[0].endswith('.js'):
        assert ['node', '--check', paths[0]] in commands


@pytest.mark.parametrize('records,tier', [([{'tier': 'small'}, {'tier': 'normal'}], 'normal'),
                                         ([{'tier': 'small'}, {'tier': 'risky'}], 'risky')])
def test_combined_release_preserves_highest_included_floor(records, tier):
    policy = release_policy(['docs/x.md'], records)
    assert policy['tier'] == tier
    assert checks(Path(sys.executable), policy).count([sys.executable, '-m', 'pytest', '-q']) == 1


def test_actual_release_diff_escalates_and_feature_consumers_are_covered():
    policy = release_policy(['ai_manager/deployment.py'], [{'tier': 'small'}])
    assert policy['tier'] == 'risky' and policy['full_backend']
    assert policy['browsers'] == ['deployment', 'auto_deploy']
    assert policy['coverage']['auto_deploy'] == ['ai_manager/deployment.py: feature consumers']
    shared = release_policy(['ai_manager/web.py'])
    assert shared['browsers'] == list(BROWSERS)  # HTTP primitives underpin every feature.
    audio = release_policy(['ai_manager/static/audio.js'])
    assert audio['browsers'] == ['composer', 'audio']


@pytest.mark.parametrize('path,diff', [('ai_manager/auth.py', '+return True'),
                                     ('migrations/002.sql', '+ALTER TABLE tasks'),
                                     ('ai_manager/db.py', '+SCHEMA = 2'),
                                     ('ai_manager/deployment.py', '+return True'),
                                     ('ai_manager/tasks.py', '+LEASE = 1'),
                                     ('ai_manager/workers.py', '+recover_lease()')])
def test_sensitive_actual_changes_keep_review(path, diff):
    assert review_reasons([path], diff=diff)


@pytest.mark.parametrize('path', ['ai_manager/manager.py', 'ai_manager/web.py', 'ai_manager/chat.py',
                                  'ai_manager/provider.py', 'ai_manager/tasks.py', 'ai_manager/db.py',
                                  'docs/security.md', 'tests/test_auth.py'])
def test_ordinary_broad_names_do_not_alone_trigger_review(path):
    assert not review_reasons([path], 'Refine ordinary text', '+label = "Done"')


def test_explicit_sensitive_behavior_keeps_review_without_sensitive_filename():
    assert review_reasons(['ai_manager/helpers.py'], 'Fix worker concurrency recovery')
    assert not review_reasons([], 'Fix authentication')


def test_exact_scoped_attestation_and_historical_receipts():
    common = {'execution_id': 'execution', 'commit': 'candidate', 'tier_checks': 'passed'}
    manifest = {'commit': 'candidate', 'verification': {'version': 2},
                'auto_deploy': {**common, 'release_checks': 'passed'}}
    assert release_attested(manifest, 'execution')
    assert not release_attested(manifest, 'other-execution')
    manifest['verification']['version'] = 1
    assert not release_attested(manifest, 'execution')
    manifest['auto_deploy'] = {**common, 'full_suite': 'passed'}
    assert release_attested(manifest, 'execution')


def test_preparation_reads_all_assignment_floors_and_actual_diff(tmp_path, monkeypatch):
    repo = tmp_path / 'candidate'
    repo.mkdir()
    (repo / 'test_changed.py').write_text('')
    with sqlite3.connect(tmp_path / 'manager.sqlite3') as conn:
        conn.execute('CREATE TABLE settings(key TEXT,value TEXT)')
        for task, tier in [('small', 'small'), ('normal', 'normal'), ('risky', 'risky')]:
            conn.execute('INSERT INTO settings VALUES(?,?)', ('assignment_policy:' + task,
                         json.dumps({'verification': {'tier': tier}})))
    with sqlite3.connect(tmp_path / 'manager.sqlite3') as conn:
        conn.execute('INSERT INTO settings VALUES(?,?)', ('assignment_policy:integration',
                     json.dumps({'integration': True, 'verification': {'tier': 'small'}})))
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'docs/x.md\ntest_changed.py')
    result = selected_policy(repo, 'live', 'candidate', tmp_path, ['small', 'normal'])
    assert result['tier'] == 'normal' and result['existing_files'] == ['test_changed.py']
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['small', 'risky'])['tier'] == 'risky'
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['missing'])['tier'] == 'risky'
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['integration'])['tier'] == 'risky'
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'ai_manager/deployment.py')
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['small'])['tier'] == 'risky'


def test_real_small_preparation_has_only_smoke_and_exact_evidence(tmp_path):
    import subprocess

    from ai_manager.db import Database
    from ai_manager.deployment import sha
    from scripts.prepare_deployment import prepare
    repo, live, data = (tmp_path / name for name in ('repo', 'live', 'data'))
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True)
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (repo / 'ai_manager').mkdir()
    (repo / 'ai_manager/deployment.py').write_text('# fixture pinned helper\n')
    (repo / 'test_fixture.py').write_text('import pytest\n@pytest.mark.smoke\ndef test_smoke():\n    assert True\n')
    (repo / 'README.md').write_text('Before\n')
    git('add', '.')
    git('commit', '-m', 'base')
    subprocess.run(['git', 'clone', '-q', str(repo), str(live)], check=True)
    (repo / 'README.md').write_text('After\n')
    git('commit', '-am', 'docs')
    Database(data / 'manager.sqlite3')
    manifest = prepare(repo, live, data, 'small-policy', Path(sys.executable))
    target = data / 'deployment-helper/releases/small-policy'
    log = (target / 'validation.log').read_text()
    assert manifest['verification']['tier'] == 'small'
    assert not manifest['verification']['full_backend'] and not manifest['verification']['browsers']
    assert log.count('Check: ') == 1 and '1 passed' in log
    assert manifest['validation_sha256'] == sha(target / 'validation.log')
    assert manifest['bundle_sha256'] == sha(target / 'release.bundle')
    index = json.loads((target / 'evidence-index.json').read_text())
    assert index['candidate'] == manifest['commit'] and index['base'] == manifest['base']
    assert index['files']['manifest.json'] == sha(target / 'manifest.json')



def test_review_selection_uses_full_per_file_diff_and_ignores_test_context():
    diff = 'diff --git a/tests/test_db.py b/tests/test_db.py\n+SCHEMA = 2\n'
    diff += 'diff --git a/ai_manager/tasks.py b/ai_manager/tasks.py\n+label = "Done"\n'
    assert not review_reasons(['tests/test_db.py', 'ai_manager/tasks.py'], diff=diff)
    diff += 'diff --git a/ai_manager/workers.py b/ai_manager/workers.py\n' + '+padding\n' * 10000 + '+await gather()\n'
    assert review_reasons(['ai_manager/tasks.py', 'ai_manager/workers.py'], diff=diff) == [
        'sensitive actual path ai_manager/workers.py']
    assert not review_reasons(['docs/security.md'], 'Explain authentication')



def test_retained_private_audit_artifacts_do_not_select_unrelated_browser_suites():
    policy = release_policy(['.data/auto-deploy-bootstrap/audit.py',
                             '.data/auto-deploy-bootstrap/audit.json',
                             'ai_manager/verification.py'], [{'tier': 'risky'}])
    assert policy['browsers'] == ['worker_delivery', 'agent_controls', 'deployment', 'auto_deploy']
    commands = checks(Path(sys.executable), policy)
    assert not any('.data/auto-deploy-bootstrap/audit.py' in argv for argv in commands)



def test_small_changed_test_helpers_select_consumers_and_root_tests(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    (repo / 'tests').mkdir(parents=True)
    (repo / 'tests/helper.py').write_text('VALUE = 1')
    (repo / 'tests/test_consumer.py').write_text('from helper import VALUE')
    (repo / 'tests/test_unrelated.py').write_text('')
    (repo / 'test_root.py').write_text('')
    with sqlite3.connect(tmp_path / 'manager.sqlite3') as conn:
        conn.execute('CREATE TABLE settings(key TEXT,value TEXT)')
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'tests/helper.py')
    policy = selected_policy(repo, 'base', 'candidate', tmp_path, [])
    assert policy['tier'] == 'small' and policy['target_tests'] == ['tests/test_consumer.py']
    assert [sys.executable, '-m', 'pytest', '-q', 'tests/test_consumer.py'] in checks(Path(sys.executable), policy)
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'test_root.py')
    assert selected_policy(repo, 'base', 'candidate', tmp_path, [])['target_tests'] == ['test_root.py']

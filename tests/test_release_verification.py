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
def test_every_release_runs_full_backend_once_including_small(paths):
    policy = release_policy(paths, [{'tier': 'small'}])
    commands = checks(Path(sys.executable), policy)
    assert policy['tier'] == 'deploy' and policy['full_backend']
    assert commands.count([sys.executable, '-m', 'pytest', '-q']) == 1
    assert [sys.executable, '-m', 'pytest', '-q', '-m', 'smoke'] in commands
    if paths[0].endswith('.js'):
        assert ['node', '--check', paths[0]] in commands


@pytest.mark.parametrize('records,tier', [([{'tier': 'small'}, {'tier': 'normal'}], 'normal'),
                                         ([{'tier': 'small'}, {'tier': 'risky'}], 'risky')])
def test_combined_release_preserves_highest_included_floor(records, tier):
    policy = release_policy(['docs/x.md'], records)
    assert policy['tier'] == 'deploy'
    assert checks(Path(sys.executable), policy).count([sys.executable, '-m', 'pytest', '-q']) == 1


def test_actual_release_diff_escalates_and_feature_consumers_are_covered():
    policy = release_policy(['ai_manager/deployment.py'], [{'tier': 'small'}])
    assert policy['tier'] == 'deploy' and policy['full_backend']
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
    assert result['tier'] == 'deploy' and result['existing_files'] == ['test_changed.py']
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['small', 'risky'])['tier'] == 'deploy'
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['missing'])['tier'] == 'deploy'
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['integration'])['tier'] == 'deploy'
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'ai_manager/deployment.py')
    assert selected_policy(repo, 'live', 'candidate', tmp_path, ['small'])['tier'] == 'deploy'


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
    assert manifest['verification']['tier'] == 'deploy'
    assert manifest['verification']['full_backend'] and not manifest['verification']['browsers']
    assert log.count('Check: ') == 2 and '1 passed' in log
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
        'sensitive worker concurrency/recovery diff ai_manager/workers.py']
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
    assert policy['tier'] == 'deploy' and policy['full_backend']
    commands = checks(Path(sys.executable), policy)
    assert commands.count([sys.executable, '-m', 'pytest', '-q']) == 1
    assert not any('tests/helper.py' in command for command in commands if 'pytest' in command)
    monkeypatch.setattr('scripts.prepare_deployment.run', lambda *args: 'test_root.py')
    assert selected_policy(repo, 'base', 'candidate', tmp_path, [])['full_backend']


@pytest.mark.parametrize('path,changed,category', [
    ('ai_manager/routes.py', '-if not require_owner(request):\n-    raise HTTPException(401)\n+return result', 'auth/security'),
    ('ai_manager/manager.py', '+if session.token_hash != supplied_hash:\n+    return False', 'auth/security'),
    ('ai_manager/helpers.py', '+value = request.headers["Authorization"]', 'auth/security'),
    ('ai_manager/routes.py', '+conn.execute("ALTER TABLE tasks ADD COLUMN lease TEXT")', 'database migration'),
    ('ai_manager/manager.py', '+SCHEMA_VERSION = 14', 'database migration'),
    ('ai_manager/helpers.py', '+subprocess.run(["systemctl", "restart", unit])', 'deployment/rollback'),
    ('ai_manager/routes.py', '+rollback(candidate)', 'deployment/rollback'),
    ('ai_manager/manager.py', '+asyncio.create_task(run_worker(task))', 'worker concurrency/recovery'),
    ('ai_manager/routes.py', '-await lock.acquire()\n+await proceed()', 'worker concurrency/recovery'),
    ('ai_manager/helpers.py', '+task.lease_owner = execution_id', 'worker concurrency/recovery'),
    ('ai_manager/manager.py', '+recover_assignment(task)', 'worker concurrency/recovery'),
])
def test_actual_sensitive_code_selects_review_without_sensitive_filename_or_request(path, changed, category):
    diff = f'diff --git a/{path} b/{path}\n@@ -1,2 +1,2 @@ def update():\n{changed}\n'
    assert 'sensitive ' + category + ' diff ' + path in review_reasons([path], 'Tidy output', diff)


@pytest.mark.parametrize('changed', [
    '+label = "Security settings"', '+label = "Authorization"',
    '+# Fix auth and worker lease recovery later',
    '+async def refresh():\n+    return await api.get("/api/portfolio")',
    '+total_tokens = usage.total_tokens + 1', '+count = len(items)',
    '+author = record.author', '+count = len(items) # "systemctl restart"',
])
def test_ordinary_code_still_skips_review_in_ordinary_risky_filename(changed):
    path = 'ai_manager/manager.py'
    diff = f'diff --git a/{path} b/{path}\n@@ -1 +1 @@ def render():\n{changed}\n'
    assert not review_reasons([path], 'Tidy output', diff)


def test_sensitive_enclosing_scope_and_permission_decision_context():
    path = 'ai_manager/routes.py'
    scope = f'diff --git a/{path} b/{path}\n@@ -3 +3 @@ def require_owner(request):\n-return False\n+return True\n'
    assert review_reasons([path], 'Tidy output', scope)
    context = f'diff --git a/{path} b/{path}\n@@ -3,3 +3,3 @@ def endpoint():\n if not session.valid:\n-    return 401\n+    return 200\n'
    assert review_reasons([path], 'Tidy output', context)
    unrelated = f'diff --git a/{path} b/{path}\n@@ -3,3 +3,3 @@ def endpoint():\n require_owner(request)\n-label = "Before"\n+label = "After"\n'
    assert not review_reasons([path], 'Tidy output', unrelated)



def test_sensitive_untracked_code_is_in_actual_diff_before_review_bounding(tmp_path):
    import subprocess

    from ai_manager.verification import changes
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Fixture')
    (tmp_path / 'README.md').write_text('base')
    git('add', '.')
    git('commit', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    (tmp_path / 'routes.py').write_text('if not require_owner(request):\n    raise HTTPException(401)\n')
    paths, diff, truncated = changes(tmp_path, base)
    assert paths == ['routes.py'] and not truncated
    assert 'sensitive auth/security diff routes.py' in review_reasons(paths, 'Tidy output', diff)



@pytest.mark.parametrize('before,after', [
    ('if user["role"] != "admin":', 'if False:'),
    ('if user["role"] != "admin":', 'if user["role"] != "member":'),
    ("if user.get('role') not in ('admin', 'owner'):", 'if False:'),
    ('if role != "admin":', 'if role != "reader":'),
    ('if user.role != "admin":', 'if True:'),
    ('user["role"] = "member"', 'user["role"] = "admin"'),
    ('user.role = "member"', 'user.role = "admin"'),
    ('if getattr(user, "role") != "admin":', 'if False:'),
    ('user["permissions"] = ["read"]', 'user["permissions"] = ["write"]'),
    ('if row["permissions"]:', 'if False:'),
    ('if claims.get("scopes") == ["read"]:', 'if True:'),
])
def test_role_and_permission_guards_in_ordinary_file_keep_security_review(before, after):
    path = 'ai_manager/routes.py'
    diff = f'diff --git a/{path} b/{path}\n@@ -1 +1 @@ def endpoint():\n-{before}\n+{after}\n'
    assert 'sensitive auth/security diff ' + path in review_reasons([path], 'Tidy output', diff)


@pytest.mark.parametrize('changed', [
    '+label = "admin"', '+label = "role"', '+label = "permissions"',
    '+labels["role"] = "admin"', '+usage["role"] = "assistant"',
    '+labels = {"role": "admin", "permissions": "Permissions"}',
    '+message = {"role": "assistant", "content": text}',
    '+message["role"] = "assistant"', '+if message["role"] == "assistant":',
    '+# if user["role"] != "admin":', '+count = 1 # user["permissions"] = ["write"]',
    '+usage = {"role": "assistant", "total_tokens": count}',
])
def test_display_comments_and_provider_role_metadata_still_skip_review(changed):
    path = 'ai_manager/routes.py'
    diff = f'diff --git a/{path} b/{path}\n@@ -1 +1 @@ def render():\n{changed}\n'
    assert not review_reasons([path], 'Tidy output', diff)



@pytest.mark.parametrize('context,sensitive', [
    ('if user["role"] != (', True),
    ('if user.get("role") in {', True),
    ('labels = [', False),
    ('labels["role"] = [', False),
])
def test_literal_only_role_comparison_uses_bound_context_without_display_guard_leak(context, sensitive):
    path = 'ai_manager/routes.py'
    diff = (f'diff --git a/{path} b/{path}\n@@ -1,5 +1,5 @@ def endpoint():\n'
            f' require_owner(request)\n {context}\n-    "admin"\n+    "viewer"\n )\n')
    assert bool(review_reasons([path], 'Tidy output', diff)) is sensitive


@pytest.mark.parametrize('before,after', [
    ('if current_user.is_admin:', 'if False:'),
    ('if current_user.is_admin():', 'if False:'),
    ('if principal.isAdmin():', 'if True:'),
    ('if account.is_staff:', 'if False:'),
    ('if actor.is_superuser:', 'if False:'),
    ('if user.has_permission("write"):', 'if False:'),
    ('if principal.has_perm("edit"):', 'if True:'),
    ('if user.has_role("admin"):', 'if True:'),
    ('if row["role"] != "user":', 'if False:'),
    ('if user.hasAnyRole("admin", "owner"):', 'if False:'),
    ('if user.has_all_permissions(requested):', 'if True:'),
    ('if user.is_owner_of(resource):', 'if False:'),
    ('if user.owns(resource):', 'if False:'),
    ('if account.can_manage(resource):', 'if False:'),
    ('if user.id == owner_id:', 'if True:'),
    ('if current_user.id != record.owner_id:', 'if False:'),
    ('if document.owner_id == request.user.id:', 'if document.owner_id != request.user.id:'),
    ('if resource.owner == principal:', 'if True:'),
    ('if actor_id == creator_id:', 'if False:'),
    ('if user.id in resource.members:', 'if False:'),
    ('if principal.id == requested_id:', 'if True:'),
    ('if user["id"] == row["owner_id"]:', 'if False:'),
    ('if account.get("id") == item.get("account_id"):', 'if True:'),
    ('allowed = user.id == owner_id', 'allowed = True'),
    ('user.is_staff = False', 'user.is_staff = True'),
    ('current_user.metadata.is_staff = False', 'current_user.metadata.is_staff = True'),
    ('is_admin = False', 'is_admin = True'),
    ('allowed = user.can_manage(resource)', 'allowed = True'),
    ('return user.id == owner_id', 'return True'),
])
def test_authorization_predicate_and_ownership_matrix_in_ordinary_file(before, after):
    path = 'ai_manager/routes.py'
    diff = f'diff --git a/{path} b/{path}\n@@ -1 +1 @@ def endpoint():\n-{before}\n+{after}\n'
    assert 'sensitive auth/security diff ' + path in review_reasons([path], 'Tidy output', diff)


@pytest.mark.parametrize('changed', [
    '+author = record.author', '+author_id = record.author_id',
    '+owner_label = "Owner"', '+label = "is_admin"',
    '+if row.id == selected_id:', '+if item.author == filter_author:',
    '+if labels["is_admin"]:', '+if usage["role"] == "assistant":',
    '+message.role = "assistant"', '+usage.permission = "display"',
    '+if message["role"] == "user":',
    '+# if user.id == owner_id:', '+count = 1 # if current_user.is_admin:',
])
def test_authorization_matrix_retains_ordinary_metadata_and_unrelated_id_negatives(changed):
    path = 'ai_manager/routes.py'
    diff = f'diff --git a/{path} b/{path}\n@@ -1 +1 @@ def render():\n{changed}\n'
    assert not review_reasons([path], 'Tidy output', diff)


def test_ownership_guard_context_change_keeps_security_review():
    path = 'ai_manager/routes.py'
    diff = (f'diff --git a/{path} b/{path}\n@@ -1,3 +1,3 @@ def endpoint():\n'
            ' if user.id == owner_id:\n-    return True\n+    return False\n')
    assert review_reasons([path], 'Tidy output', diff)



def test_multiline_ownership_decision_context_is_structurally_recognized():
    path = 'ai_manager/routes.py'
    diff = (f'diff --git a/{path} b/{path}\n@@ -1,5 +1,5 @@ def endpoint():\n'
            ' if (\n     user.id == resource.owner_id\n ):\n-    return True\n+    return False\n')
    assert review_reasons([path], 'Tidy output', diff)

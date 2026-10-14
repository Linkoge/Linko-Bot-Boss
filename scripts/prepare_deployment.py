"""Trusted local preparation only. Conversation receives an ID, never a path/argv."""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from ai_manager.deployment import ID, REHEARSED, Handoff, atomic, lock, rehearse, run, schema_change, sha


def prepare(repo, live, data, release_id, python, task_ids=(), auto_execution=None):
    # One global preparation, serialized with durable integration reservations.
    from ai_manager.codex import process_start
    with lock(Handoff(data).root / 'preparation.lock'):
        path = data / 'manager.sqlite3'
        if not path.exists():
            raise ValueError('Preparation requires the existing runtime database')
        if Handoff(data).gated():
            raise ValueError('Deployment is already pending')
        with sqlite3.connect(path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute("SELECT 1 FROM settings WHERE key='assignment_integration'").fetchone():
                raise ValueError('Serialized integration/combined validation is still reserved')
            import json
            conn.execute("INSERT INTO settings VALUES('release_preparation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps({'pid': os.getpid(), 'process_start': process_start(os.getpid()), 'release_id': release_id}),))
        try:
            return _prepare(repo, live, data, release_id, python, task_ids, auto_execution)
        finally:
            with sqlite3.connect(path) as conn:
                conn.execute("DELETE FROM settings WHERE key='release_preparation'")


def checks(python):
    """Fixed full backend + browser gate, run exactly once per release."""
    return [[str(python), '-m', 'pytest', '-q'], [str(python), '-m', 'ruff', 'check', '.'],
            ['node', '--check', 'ai_manager/static/app.js'],
            ['node', '--check', 'ai_manager/static/usage.js'],
            ['node', '--check', 'ai_manager/static/model-settings.js'],
            ['node', '--check', 'ai_manager/static/memory.js'],
            ['node', '--check', 'ai_manager/static/audio.js'],
            ['node', '--check', 'ai_manager/static/attachments.js'],
            [str(python), 'scripts/browser_model_scroll.py'],
            [str(python), 'scripts/browser_composer.py'],
            [str(python), 'scripts/browser_parallel_composer.py'],
            [str(python), 'scripts/browser_worker_delivery.py'],
            [str(python), 'scripts/browser_audio.py'],
            [str(python), 'scripts/browser_chat_reliability.py'],
            [str(python), 'scripts/browser_project_ui.py'],
            [str(python), 'scripts/browser_project_stash.py'],
            [str(python), 'scripts/browser_global_recency.py'],
            [str(python), 'scripts/browser_agent_controls.py'],
            [str(python), 'scripts/browser_todos.py'],
            [str(python), 'scripts/browser_todo_chat.py'],
            [str(python), 'scripts/browser_project_memory.py'],
            [str(python), 'scripts/browser_fetch_recovery.py'],
            [str(python), 'scripts/browser_smoke.py'], [str(python), 'scripts/browser_markdown.py'], [str(python), 'scripts/browser_deployment.py'],
            [str(python), 'scripts/browser_account_usage.py'],
            [str(python), 'scripts/browser_auto_deploy.py'],
            ['xvfb-run', '-a', str(python), 'scripts/browser_pwa.py']]


def _prepare(repo, live, data, release_id, python, task_ids=(), auto_execution=None):
    if not ID.fullmatch(release_id):
        raise ValueError('Invalid release ID')
    if run(['git', 'status', '--porcelain'], repo):
        raise ValueError('Commit reviewed candidate before preparing it')
    base = run(['git', 'rev-parse', 'HEAD'], live)
    commit = run(['git', 'rev-parse', 'HEAD'], repo)
    run(['git', 'merge-base', '--is-ancestor', base, commit], repo)
    schema = schema_change(repo, base, commit)
    target = Handoff(data).root / 'releases' / release_id
    target.mkdir(parents=True, mode=0o700)  # Never overwrite a prepared receipt.
    # Candidate code and tests must import the candidate, never the running manager's modules.
    env = {**os.environ, 'PYTHONPATH': str(repo)}
    for name in ('LINKO_EVIDENCE_DIR', 'LINKO_AGENT_EVIDENCE', 'LINKO_PWA_EVIDENCE', 'LINKO_USAGE_EVIDENCE',
                 'LINKO_PROJECT_EVIDENCE', 'LINKO_STASH_EVIDENCE', 'LINKO_MODEL_EVIDENCE', 'LINKO_MEMORY_EVIDENCE',
                 'LINKO_AUDIO_EVIDENCE', 'LINKO_BUNDLE_EVIDENCE', 'LINKO_CHAT_EVIDENCE', 'LINKO_MARKDOWN_EVIDENCE'):
        evidence = target / 'browser-evidence' / name.lower()
        evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        env[name] = str(evidence)
    scope = None
    if auto_execution:
        if len(task_ids) != 1:
            raise ValueError('Auto-deploy requires exactly one owner assignment')
        from ai_manager.auto_deploy import verified
        from ai_manager.deployment_intent import assignment_evidence
        with sqlite3.connect(f'file:{data / "manager.sqlite3"}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_ids[0],)).fetchone()
            if not task or verified(conn, task, auto_execution, commit):
                raise ValueError('Auto-deploy candidate has not passed required tier verification')
            scope = assignment_evidence(conn, task)
    rehearsal, venv = None, target / 'rehearsal-venv'
    try:
        with (target / 'validation.log').open('w') as log:
            if schema and schema.get('dependencies'):
                # Dependency rehearsal: the exact candidate lock in a throwaway environment runs every check.
                uv = shutil.which('uv') or str(Path.home() / '.local/bin/uv')
                if subprocess.run([uv, 'sync', '--frozen'], cwd=repo, stdout=log, stderr=log, timeout=600,
                                  env={**env, 'UV_PROJECT_ENVIRONMENT': str(venv)}).returncode:
                    raise ValueError('Dependency rehearsal failed; see private validation.log')
                python = venv / 'bin/python'
            if schema and schema['migration'] == REHEARSED:
                rehearsal = rehearse(data / 'manager.sqlite3', repo, python, target / 'rehearsal', base, commit)
                log.write('Migration rehearsal passed on a private database copy\n')
                log.flush()
            if auto_execution:
                result = subprocess.run([str(python), '-m', 'pytest', '-q', '-m', 'smoke'], cwd=repo,
                                        stdout=log, stderr=log, timeout=30, env=env)
                if result.returncode:
                    raise ValueError('Required tier smoke verification failed')
            for argv in checks(python):
                result = subprocess.run(argv, cwd=repo, stdout=log, stderr=log, timeout=180, env=env)
                if result.returncode:
                    raise ValueError('Release validation failed; see private validation.log')
    finally:
        shutil.rmtree(venv, ignore_errors=True)
    if run(['git', 'status', '--porcelain'], repo) or run(['git', 'rev-parse', 'HEAD'], repo) != commit:
        raise ValueError('Candidate changed during validation; reprepare exact reviewed commit')
    if run(['git', 'rev-parse', 'HEAD'], live) != base:
        raise ValueError('Live base advanced during validation; reprepare with a fresh ID')
    run(['git', 'bundle', 'create', str(target / 'release.bundle'), 'HEAD'], repo)
    def assets(ref):
        names = run(['git', 'ls-tree', '-r', '--name-only', ref, '--', 'ai_manager/static'], repo).splitlines()
        import hashlib
        return {name.removeprefix('ai_manager/static/'): hashlib.sha256(subprocess.run(
            ['git', 'show', ref+':'+name], cwd=repo, capture_output=True, check=True).stdout).hexdigest()
                for name in names}
    manifest = {'id': release_id, 'base': base, 'commit': commit,
                'assets': assets(commit), 'base_assets': assets(base),
                'bundle_sha256': sha(target / 'release.bundle'), 'validation_sha256': sha(target / 'validation.log')}
    if schema:
        manifest['schema'] = schema
    # This second-stage release bootstraps helper v3 even for a code-only diff.
    helper_version = 'v3'
    if helper_version:
        helper = target / ('helper-' + helper_version + '.py')
        helper.write_bytes((repo / 'ai_manager/deployment.py').read_bytes())
        helper.chmod(0o600)
        manifest['required_helper_version'] = helper_version
        manifest['required_helper_sha256'] = sha(helper)
    if rehearsal:
        manifest['rehearsal'] = rehearsal
    if task_ids:

        from ai_manager.deployment_intent import assignment_evidence
        with sqlite3.connect(f'file:{data / "manager.sqlite3"}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            assignments = {}
            for task_id in task_ids:
                task = conn.execute('SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=? AND p.path=?',
                                    (task_id, str(live))).fetchone()
                if not task:
                    raise ValueError('Preparation assignment belongs to another project or is missing')
                assignments[task_id] = assignment_evidence(conn, task, 2)
            manifest['assignments'] = assignments
            manifest['assignment_evidence_version'] = 2
            if auto_execution:
                if assignments != {task_ids[0]: scope}:
                    raise ValueError('Assignment scope changed during release checks')
                manifest['auto_deploy'] = {'execution_id': auto_execution, 'commit': commit,
                                          'tier_checks': 'passed', 'full_suite': 'passed'}
                manifest['restore_database'] = True
    atomic(target / 'manifest.json', manifest)
    atomic(target / 'evidence-index.json', {'release_id': release_id, 'candidate': commit, 'base': base,
           'files': {p.relative_to(target).as_posix(): sha(p) for p in target.rglob('*')
                     if p.is_file() and p != target / 'evidence-index.json'}})
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('release_id')
    parser.add_argument('--live', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--task-id', action='append', default=[])
    parser.add_argument('--auto-execution')
    parser.add_argument('--candidate', type=Path, help='Clean candidate checkout (default: this checkout)')
    args = parser.parse_args()
    os.umask(0o077)
    prepare((args.candidate or Path(__file__).parents[1]).resolve(), args.live.resolve(), args.data.resolve(),
            args.release_id, Path(sys.executable), args.task_id, args.auto_execution)
    print('Prepared release ' + args.release_id)

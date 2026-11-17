"""The runtime deploy setup has one source: the live <data>/deploy/installed.json, never a copy frozen in the DB."""
import json

import pytest

from ai_manager.db import Database
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    db = Database(tmp_path / 'data' / 'manager.sqlite3')
    registry = Registry(db, (tmp_path,))
    project = registry.add(repo)
    pipeline = Pipeline(db, registry)
    pipeline.root.mkdir(parents=True, exist_ok=True)
    return pipeline, db, project['id'], repo


def write(pipeline, repo, url):
    (pipeline.root / 'installed.json').write_text(json.dumps({'repo': str(repo), 'url': url, 'data': str(pipeline.root.parent)}))


def test_url_change_reaches_status_and_next_request(env):
    pipeline, db, pid, repo = env
    write(pipeline, repo, 'http://127.0.0.1:8787')
    assert pipeline.status(pid)['setup']['runtime']['url'].endswith(':8787')
    write(pipeline, repo, 'http://127.0.0.1:31847')
    assert pipeline.status(pid)['setup']['runtime']['url'].endswith(':31847')
    assert pipeline.request(pid)['setup']['runtime']['url'].endswith(':31847')
    assert db.setting('deploy_setup:' + pid) == {'runtime': True}


def test_old_migrated_row_is_overridden_by_live_file(env):
    pipeline, db, pid, repo = env
    write(pipeline, repo, 'http://127.0.0.1:31847')
    db.set_setting('deploy_setup:' + pid, {'runtime': {'repo': str(repo), 'url': 'http://127.0.0.1:8787'}})
    assert pipeline.setup(pid)['runtime']['url'].endswith(':31847')
    assert db.setting('deploy_setup:' + pid) == {'runtime': True}  # migrated to the minimal marker


def test_explicit_setup_unchanged(env):
    pipeline, db, pid, repo = env
    write(pipeline, repo, 'http://127.0.0.1:31847')
    explicit = {'deploy_command': ['true'], 'health_url': 'http://127.0.0.1:1/h'}
    db.set_setting('deploy_setup:' + pid, explicit)
    assert pipeline.setup(pid) == explicit


@pytest.mark.parametrize('content', [None, 'not json', '{}'])
def test_missing_or_unreadable_installed_json_is_a_clear_error(env, content):
    pipeline, db, pid, repo = env
    db.set_setting('deploy_setup:' + pid, {'runtime': {'repo': str(repo), 'url': 'http://127.0.0.1:8787'}})
    if content is not None:
        (pipeline.root / 'installed.json').write_text(content)
    status = pipeline.status(pid)
    assert status['setup'] is None and 'installed.json' in status['setup_error']
    assert '8787' not in json.dumps(status)
    with pytest.raises(ValueError, match='installed.json'):
        pipeline.request(pid)
    assert not pipeline.pending()

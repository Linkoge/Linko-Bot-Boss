"""Main's global settings are the settings of every instance; an instance shows and accepts only Instructions and Sign out."""
import json
import sqlite3

import pytest
from test_instance_models import pair  # noqa: F401  (fixture: Main + one registered restricted instance)
from test_model_settings import SOL

from ai_manager import chat_memory, instance_sync
from ai_manager.instance_models import ExistingDatabase
from ai_manager.model_settings import KEY, current

GLOBAL_WRITES = [('put', '/api/model-settings', {}), ('put', '/api/model-picker/providers', {}),
                 ('put', '/api/chat-memory/settings', {}), ('put', '/api/manager-favorites/0', None),
                 ('post', '/api/manager-favorites/0/recall', None), ('post', '/api/sessions/revoke', None)]


@pytest.mark.parametrize('method,path,body', GLOBAL_WRITES)
def test_instance_rejects_global_setting_writes_on_the_server(pair, method, path, body):
    owner, client, *_ = pair
    response = getattr(client, method)(path, **({'json': body} if body is not None else {}))
    assert response.status_code == 403
    assert getattr(owner, method)(path, **({'json': body} if body is not None else {})).status_code != 403


def test_instance_keeps_instructions_and_sign_out(pair):
    client = pair[1]
    assert client.put('/api/settings/instructions', json={'content': 'Be brief'}).status_code == 200
    assert client.get('/api/settings').json()['global_instructions'] == 'Be brief'
    assert client.post('/api/logout').status_code == 200


def test_main_keeps_revoke_sessions(pair):
    owner = pair[0]
    assert owner.post('/api/sessions/revoke').json() == {'revoked': True}


def test_main_save_reaches_the_instance_effective_settings(pair):
    owner, client, main_app, user_app, main, user, entry = pair
    saved = owner.get('/api/model-settings').json()['settings']
    pick = {'model': SOL, 'effort': 'high'}
    assert owner.put('/api/model-settings', json={'revision': saved['revision'], 'manager': pick, 'worker': pick}).status_code == 200
    memory = owner.get('/api/chat-memory/settings').json()['settings']
    memory = {**memory, 'threshold_tokens': 123000}
    assert owner.put('/api/chat-memory/settings', json=memory).status_code == 200
    db = user_app.state.db
    assert current(db)['manager']['model'] == SOL and current(db)['manager']['effort'] == 'high'
    assert chat_memory.load_settings(db)['threshold_tokens'] == 123000
    assert client.get('/api/chat-memory/settings').json()['settings']['threshold_tokens'] == 123000


def test_sync_replaces_instance_overrides_and_survives_unreachable_instances(pair, tmp_path):
    owner, client, main_app, user_app, main, user, entry = pair
    db = user_app.state.db
    db.set_setting(KEY + ':proj', {'revision': 9, 'manager': {'effort': 'low'}, 'worker': {'effort': 'low'}})
    db.set_setting('chat_compaction', {'threshold_tokens': 777777})
    main_app.state.db.set_setting('chat_compaction', {'threshold_tokens': 222000, 'revision': 3})
    assert instance_sync.push(main_app.state.db, main.data_dir) == []
    assert db.setting(KEY + ':proj') is None
    assert db.setting('chat_compaction')['threshold_tokens'] == 222000
    main_app.state.db.run("DELETE FROM settings WHERE key='chat_compaction'")
    instance_sync.push(main_app.state.db, main.data_dir)
    assert db.setting('chat_compaction') is None  # unset on Main: the instance falls back to the defaults
    # An unreachable instance is reported and left as it was; Main never creates its database.
    (main.data_dir / 'deploy' / 'installed.json').write_text(json.dumps({'instances': [
        {'name': 'Gone', 'data': str(tmp_path / 'missing')}, entry]}))
    assert instance_sync.push(main_app.state.db, main.data_dir) == ['Gone']
    assert not (tmp_path / 'missing').exists()
    with pytest.raises(sqlite3.Error):
        ExistingDatabase(tmp_path / 'missing').connect()

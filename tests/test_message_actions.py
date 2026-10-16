"""Whole display-message deletion preserves execution/native evidence and owner guards."""
import time

import pytest
from test_chat_history import seed
from test_web import sign_in

from ai_manager.chat import Chat
from ai_manager.db import Database

pytest_plugins = ['test_web', 'test_chat_history']


def test_whole_response_delete_is_persistent_idempotent_and_excluded_from_exports(chat):
    conversation, assistant = seed(chat)
    text = '# Heading\n\nFirst **fragment**\n\n```py\nprint(42)\n```\n\nLast fragment'
    with chat.db.transaction() as conn:
        conn.execute('UPDATE messages SET content=? WHERE id=?', (text, assistant))
    original = chat.db.all('SELECT * FROM messages')
    for _ in range(2):
        chat.delete_message(None, conversation, assistant, True)
    restored = Chat(Database(chat.db.path), chat.registry, chat.server)
    assert restored.history()['deleted_message_ids'] == [assistant]
    assert [m['role'] for m in restored.history()['messages']] == ['user']
    assert [m['role'] for m in restored.saved_history(conversation)['messages']] == ['user']
    assert not restored.search_history(search='Last fragment')['conversations']
    assert restored.search_history()['conversations'][0]['message_count'] == 1
    assert restored.db.all('SELECT * FROM messages') == original
    assert len(restored.db.all("SELECT * FROM events WHERE kind='chat.message_deleted'")) == 1
    # Clear/archive guards use the visible tail, including after deletion of the last response.
    restored.reset(None, conversation, original[0]['id'], True, archive=True, title='Retained')
    assert len(restored.saved_history(conversation)['messages']) == 1



def test_archived_and_other_context_messages_remain_separate(chat, tmp_path):
    conversation, assistant = seed(chat)
    chat.reset(None, conversation, assistant, True, archive=True, title='Archive')
    fresh, tail = seed(chat)
    project_path = tmp_path / 'project'
    project_path.mkdir()
    chat.registry.roots = (tmp_path,)
    project = chat.registry.add(project_path)['id']
    other, other_tail = seed(chat, project)
    with pytest.raises(ValueError, match='not found'):
        chat.delete_message(None, fresh, assistant, True)
    with pytest.raises(ValueError, match='selected context'):
        chat.delete_message(project, conversation, assistant, True)
    chat.delete_message(None, conversation, assistant, True)
    assert [m['role'] for m in chat.saved_history(conversation)['messages']] == ['user']
    assert chat.history()['messages'][-1]['id'] == tail
    assert chat.history(project)['messages'][-1]['id'] == other_tail
    assert not chat.history()['deleted_message_ids']
    assert not chat.history(project)['deleted_message_ids']

def test_delete_confirmation_context_role_active_and_atomic_guards(chat, monkeypatch):
    conversation, assistant = seed(chat)
    before = chat.db.all('SELECT * FROM messages')
    for identity, message, confirmed, reason in [
        (conversation, assistant, False, 'Confirm'),
        ('other', assistant, True, 'selected context'),
        (conversation, 'missing', True, 'not found'),
    ]:
        with pytest.raises(ValueError, match=reason):
            chat.delete_message(None, identity, message, confirmed)
    with chat.db.transaction() as conn:
        conn.execute("UPDATE messages SET status='streaming' WHERE id=?", (assistant,))
    with pytest.raises(ValueError, match='finish'):
        chat.delete_message(None, conversation, assistant, True)
    with chat.db.transaction() as conn:
        conn.execute("UPDATE messages SET status='completed' WHERE id=?", (assistant,))
    def fail(*args, **kwargs):
        raise RuntimeError('fixture failure')
    with monkeypatch.context() as patch:
        patch.setattr(chat.db, 'event', fail)
        with pytest.raises(RuntimeError):
            chat.delete_message(None, conversation, assistant, True)
    assert chat.history()['deleted_message_ids'] == []
    assert chat.db.all('SELECT * FROM messages') == before
    with chat.db.transaction() as conn:
        conn.execute("UPDATE messages SET role='worker' WHERE id=?", (assistant,))
    with pytest.raises(ValueError, match='not found'):
        chat.delete_message(None, conversation, assistant, True)


def test_delete_endpoint_requires_owner_csrf_exact_context_and_boolean_confirmation(client):
    browser, app, settings = client
    payload = {'project_id': None, 'conversation_id': 'x', 'message_id': 'y', 'confirmed': True}
    assert browser.post('/api/chat/message/delete', json=payload).status_code == 401
    sign_in(browser, app, settings)
    conversation, assistant = seed(app.state.chat)
    payload.update(conversation_id=conversation, message_id=assistant)
    csrf = browser.headers.pop('X-CSRF-Token')
    assert browser.post('/api/chat/message/delete', json=payload).status_code == 403
    browser.headers['X-CSRF-Token'] = csrf
    assert browser.post('/api/chat/message/delete', json={**payload, 'confirmed': 'true'}).status_code == 422
    assert browser.post('/api/chat/message/delete', json={**payload, 'confirmed': False}).status_code == 409
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    assert browser.post('/api/chat/message/delete', json={**payload, 'project_id': project}).status_code == 409
    # An active execution blocks deleting its owner message too, regardless of saved message status.
    owner = app.state.db.one("SELECT * FROM messages WHERE role='user'")
    with app.state.db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,agent_id,model,effort,status,started_at) VALUES('active','manager','m','low','running',?)", (time.time(),))
        conn.execute("UPDATE messages SET execution_id='active' WHERE id=?", (owner['id'],))
    assert browser.post('/api/chat/message/delete', json={**payload, 'message_id': owner['id']}).status_code == 409
    assert browser.post('/api/chat/message/delete', json=payload).status_code == 200
    assert [m['id'] for m in browser.get('/api/history').json()['messages']] == [owner['id']]


def test_partial_archive_endpoint_authorization_validation_and_project_isolation(client):
    browser, app, settings = client
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    conversation, last = seed(app.state.chat, project, count=24)
    other, _ = seed(app.state.chat)
    other_before = app.state.db.all('SELECT * FROM messages WHERE conversation_id=?', (other,))
    payload = {'project_id': project, 'conversation_id': conversation, 'last_message_id': last,
               'confirmed': True, 'keep_last': 10, 'title': 'Project earlier messages'}
    assert browser.post('/api/chat/archive', json=payload).status_code == 401
    sign_in(browser, app, settings)
    csrf = browser.headers.pop('X-CSRF-Token')
    assert browser.post('/api/chat/archive', json=payload).status_code == 403
    browser.headers['X-CSRF-Token'] = csrf
    for keep in (-1, 5, 11, '10', True):
        assert browser.post('/api/chat/archive', json={**payload, 'keep_last': keep}).status_code in (409, 422)
    assert browser.post('/api/chat/archive', json={**payload, 'project_id': None}).status_code == 409
    assert browser.post('/api/chat/archive', json={**payload, 'confirmed': False}).status_code == 409
    assert browser.post('/api/chat/archive', json=payload).json()['conversation_id'] == conversation
    assert len(app.state.chat.history(project)['messages']) == 10
    assert app.state.db.all('SELECT * FROM messages WHERE conversation_id=?', (other,)) == other_before
    archive = app.state.db.one('SELECT conversation_id FROM chat_archives')['conversation_id']
    with pytest.raises(ValueError, match='selected context'):
        app.state.chat.saved_history(archive)
    assert len(app.state.chat.saved_history(archive, project)['messages']) == 14

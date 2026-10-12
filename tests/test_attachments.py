import base64
import time

from test_web import client as web_client
from test_web import sign_in

from ai_manager.attachments import MAX_BYTES

client = web_client
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def test_private_scoped_upload_receipt_and_native_image(client):
    c, app, settings = client
    assert c.post('/api/attachments?name=a.txt&conversation_id=x', content=b'a').status_code == 401
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    def upload(name, data, mime='application/octet-stream'):
        return c.post('/api/attachments', params={'name': name, 'conversation_id': conversation},
                      content=data, headers={**dict(c.headers), 'Content-Type': mime})
    assert upload('../escape', b'x').status_code == 422
    assert upload('empty', b'').status_code == 422
    assert upload('large', b'x' * (MAX_BYTES + 1)).status_code == 413
    assert upload('bad.png', b'not png', 'image/png').status_code == 415
    assert upload('photo.heic', b'x', 'image/heic').status_code == 415
    text = upload('notes.txt', b'worker can read this').json()
    photo = upload('photo.png', PNG, 'image/png')
    assert photo.status_code == 200, photo.text
    photo = photo.json()
    assert c.get('/api/attachments/' + text['id']).content == b'worker can read this'
    assert c.get('/api/attachments/' + text['id']).headers['content-type'] == 'application/octet-stream'
    item = app.state.attachments.get(text['id'])
    path = app.state.attachments.root / item['stored_name']
    assert path.stat().st_mode & 0o777 == 0o600
    project = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    other = c.get('/api/history', params={'project_id': project}).json()['conversation']['id']
    assert c.post('/api/chat', json={'project_id': project, 'conversation_id': other,
                                   'attachment_ids': [text['id']], 'content': 'wrong scope'}).status_code == 409
    captured = []
    original = app.state.chat.server.request
    async def record(method, params, timeout=30):
        if method == 'turn/start':
            captured.append(params['input'])
        return await original(method, params, timeout)
    app.state.chat.server.request = record
    payload = {'request_id': 'attachment-request-0001', 'conversation_id': conversation,
               'content': '', 'attachment_ids': [text['id'], photo['id']], 'mode': 'conversation'}
    first = c.post('/api/chat', json=payload)
    assert first.status_code == 200, first.text
    assert c.post('/api/chat', json=payload).json() == first.json()
    for _ in range(100):
        if captured:
            break
        time.sleep(.02)
    assert captured and captured[0][1]['type'] == 'localImage'
    assert captured[0][1]['path'].endswith('.png')
    assert str(path) in captured[0][0]['text']
    history = c.get('/api/history').json()
    owner = next(m for m in history['messages'] if m['role'] == 'user')
    assert owner['content'] == '' and len(owner['attachments']) == 2
    assert len([m for m in history['messages'] if m['role'] == 'user']) == 1
    assert c.get('/api/chat-history/' + conversation).json()['messages'][0]['attachments']
    path.write_bytes(b'changed')
    payload['request_id'] = 'attachment-request-0002'
    assert c.post('/api/chat', json=payload).status_code == 409
    c.cookies.clear()
    assert c.get('/api/attachments/' + text['id']).status_code == 401


def test_verified_attachment_backup(client, tmp_path):
    from ai_manager.attachments import backup_attachments
    c, app, settings = client
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    item = c.post('/api/attachments', params={'name': 'notes', 'conversation_id': conversation},
                  content=b'persisted bytes', headers={**dict(c.headers), 'Content-Type': 'application/octet-stream'}).json()
    snapshot = tmp_path / 'snapshot.sqlite3'
    app.state.db.backup(snapshot)
    target = tmp_path / 'uploads'
    assert backup_attachments(settings.data_dir, snapshot, target) == 1
    stored = app.state.attachments.get(item['id'])['stored_name']
    assert (target / stored).read_bytes() == b'persisted bytes'

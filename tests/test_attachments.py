import base64
import io
import time
import zipfile
from pathlib import Path
from xml.parsers.expat import ExpatError

import pytest

from test_web import client as web_client
from test_web import sign_in

from ai_manager.attachments import MAX_BYTES, MAX_DOCX_XML, attachment_extension, extract_docx

client = web_client
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def docx_bytes(xml=None):
    if xml is None:
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>Hello &amp; </w:t></w:r><w:r><w:t>world 🌍</w:t>'
               '<w:tab/><w:t>column</w:t><w:br/><w:t>next line</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
               '</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', xml)
    return buffer.getvalue()


@pytest.mark.parametrize(('name', 'mime', 'expected'), [
    ('report.PDF', 'application/octet-stream', '.pdf'),
    ('notes.docx', 'application/octet-stream', '.docx'),
    ('photo.PNG', '', '.png'),
    ('photo.JPEG', '', '.jpeg'),
    ('photo', 'image/jpeg', '.jpg'),
    ('photo', 'image/webp', '.webp'),
    ('notes.txt', '', '.txt'),
    ('unknown.weird', 'application/x-owner-unknown', '.weird'),
    ('report', 'application/pdf', '.pdf'),
    ('notes', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
    ('notes', 'text/plain', '.txt'),
    ('recording', 'audio/mpeg', '.mp3'),
    ('report.bad-suffix', 'application/pdf', '.pdf'),
    ('report.' + 'x' * 17, 'application/pdf', '.pdf'),
    ('report.💾', '', '.bin'),
    ('unknown', 'application/x-owner-unknown', '.bin'),
    ('no-extension', '', '.bin'),
    ('unknown.', 'application/octet-stream', '.bin'),
])
def test_safe_attachment_extension(name, mime, expected):
    assert attachment_extension(name, mime) == expected


def test_bounded_docx_extraction(tmp_path, monkeypatch):
    path = tmp_path / 'document.docx'
    path.write_bytes(docx_bytes())
    assert extract_docx(path) == 'Hello & world 🌍\tcolumn\nnext line\nTable cell\n'
    times = iter([0, 3])
    monkeypatch.setattr('ai_manager.attachments.time.monotonic', lambda: next(times))
    with pytest.raises(ValueError, match='time limit'):
        extract_docx(path)


@pytest.mark.parametrize('xml', [
    '<broken>',
    '<!DOCTYPE doc [<!ENTITY e "entity">]><doc>&e;</doc>',
    '<document>' + 'x' * MAX_DOCX_XML + '</document>',
])
def test_docx_rejects_invalid_or_oversized_xml(tmp_path, xml):
    path = tmp_path / 'document.docx'
    path.write_bytes(docx_bytes(xml))
    with pytest.raises((ValueError, ExpatError)):
        extract_docx(path)


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
    assert upload('bad.png', b'not png', 'image/png').status_code == 200
    assert upload('photo.heic', b'x', 'image/heic').status_code == 200
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
               'content': '', 'attachment_ids': [text['id'], photo['id']]}
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


def test_any_format_download_and_honest_manager_routing(client):
    c, app, settings = client
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    fixtures = [
        ('active.svg', 'image/svg+xml', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        ('page.html', 'text/html', b'<script>alert(1)</script>'),
        ('unknown.weird', 'application/x-owner-unknown', b'\x00\xffowner'),
        ('no-extension', '', b'\x00data'),
        ('recording.mp3', 'audio/mpeg', b'ID3audio'),
        ('report.pdf', 'application/pdf', b'%PDF-1.7 fixture'),
        ('photo.heic', 'image/heic', b'opaque image'),
        ('mislabeled.png', 'image/png', b'opaque bytes'),
    ]
    uploaded = []
    for name, mime, data in fixtures:
        response = c.post('/api/attachments', params={'name': name, 'conversation_id': conversation},
                          content=data, headers={**dict(c.headers), 'Content-Type': mime})
        assert response.status_code == 200, response.text
        item = response.json()
        downloaded = c.get('/api/attachments/' + item['id'])
        assert downloaded.content == data
        assert downloaded.headers['content-type'] == 'application/octet-stream'
        assert downloaded.headers['x-content-type-options'] == 'nosniff'
        assert downloaded.headers['content-disposition'].startswith('attachment;')
        stored = app.state.attachments.get(item['id'])
        assert stored['name'] == name and not stored['native_image']
        handling = app.state.attachments.reference(stored)['handling']
        if name == 'report.pdf':
            assert stored['stored_name'].endswith('.pdf')
            assert 'read path with the Read tool, including figures' in handling
        else:
            assert 'not guaranteed' in handling
        uploaded.append(item['id'])
    captured = []
    original = app.state.chat.server.request
    async def record(method, params, timeout=30):
        if method == 'turn/start':
            captured.append(params['input'])
        return await original(method, params, timeout)
    app.state.chat.server.request = record
    response = c.post('/api/chat', json={'conversation_id': conversation, 'content': 'inspect as data',
                      'attachment_ids': uploaded[:4]})
    assert response.status_code == 200, response.text
    for _ in range(100):
        if captured:
            break
        time.sleep(.02)
    assert captured and [x['type'] for x in captured[0]] == ['text']
    assert 'active.svg' in captured[0][0]['text'] and 'interpretation is not guaranteed' in captured[0][0]['text']


def test_pdf_docx_and_legacy_bin_notice_and_backup(client, tmp_path):
    from ai_manager.attachments import backup_attachments

    c, app, settings = client
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    uploads = []
    for name, mime, data in [
        ('report.PDF', 'application/octet-stream', b'%PDF-1.7 fixture'),
        ('document', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docx_bytes()),
        ('broken.docx', 'application/octet-stream', b'invalid zip'),
        ('legacy', 'application/octet-stream', b'old bytes'),
    ]:
        response = c.post('/api/attachments', params={'name': name, 'conversation_id': conversation},
                          content=data, headers={**dict(c.headers), 'Content-Type': mime})
        assert response.status_code == 200, response.text
        item = app.state.attachments.get(response.json()['id'])
        assert (app.state.attachments.root / item['stored_name']).read_bytes() == data
        uploads.append(item)
    # Simulate a record written before suffix preservation, with no newer metadata.
    legacy = uploads[-1]
    legacy.update(name='old.pdf', mime='application/pdf')
    legacy.pop('native_image')
    app.state.db.set_setting('attachment:' + legacy['id'], legacy)
    assert legacy['stored_name'] == legacy['id'] + '.bin'
    assert uploads[0]['stored_name'] == uploads[0]['id'] + '.pdf'
    text_path = app.state.attachments.root / uploads[1]['text_name']
    assert text_path.name == uploads[1]['id'] + '.docx.txt'
    assert text_path.read_text() == 'Hello & world 🌍\tcolumn\nnext line\nTable cell\n'
    assert text_path.stat().st_mode & 0o777 == 0o600
    assert 'text_name' not in uploads[2]
    captured = []
    original = app.state.chat.server.request

    async def record(method, params, timeout=30):
        if method == 'turn/start':
            captured.append(params['input'])
        return await original(method, params, timeout)

    app.state.chat.server.request = record
    response = c.post('/api/chat', json={'conversation_id': conversation, 'content': 'Read attached files',
                      'attachment_ids': [item['id'] for item in uploads]})
    assert response.status_code == 200, response.text
    for _ in range(100):
        if captured:
            break
        time.sleep(.02)
    assert captured and [item['type'] for item in captured[0]] == ['text']
    notice = captured[0][0]['text']
    for item in uploads:
        assert str(app.state.attachments.root / item['stored_name']) in notice
    assert str(text_path) in notice
    assert 'Read PDFs via path (including figures where supported)' in notice
    assert 'DOCX: read text_path' in notice
    assert 'DOCX text extraction failed; original upload retained' in notice
    assert 'pass the original path and any text_path to the worker' in notice
    refs = app.state.db.setting('execution_attachments:' + response.json()['execution_id'])
    assert refs == [app.state.attachments.reference(item) for item in uploads]
    assert refs[1]['text_path'] == str(text_path)
    assert 'text_path' not in refs[2]
    assert 'not guaranteed' in refs[-1]['handling']
    assert c.get('/api/attachments/' + legacy['id']).content == b'old bytes'
    snapshot = tmp_path / 'snapshot.sqlite3'
    app.state.db.backup(snapshot)
    target = tmp_path / 'uploads'
    assert backup_attachments(settings.data_dir, snapshot, target) == 4
    assert (target / text_path.name).read_bytes() == text_path.read_bytes()
    assert (target / legacy['stored_name']).read_bytes() == b'old bytes'
    assert sorted(p.name for p in app.state.attachments.root.iterdir()) == sorted(
        [item['stored_name'] for item in uploads] + [text_path.name])


def test_docx_sidecar_write_failure_does_not_block_upload(client, monkeypatch):
    c, app, settings = client
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    original = Path.open

    def unavailable(path, *args, **kwargs):
        if path.name.endswith('.docx.txt'):
            raise OSError('fixture disk error')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', unavailable)
    data = docx_bytes()
    response = c.post('/api/attachments', params={'name': 'document.docx', 'conversation_id': conversation},
                      content=data, headers={**dict(c.headers), 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200, response.text
    assert 'text extraction failed' in response.json()['handling']
    assert c.get('/api/attachments/' + response.json()['id']).content == data
    assert not list(app.state.attachments.root.glob('*.txt'))


def test_raster_handling_limits_and_parser_fallback(client, monkeypatch):
    import json
    import subprocess

    c, app, settings = client
    sign_in(c, app, settings)
    conversation = c.get('/api/history').json()['conversation']['id']
    for mime, codec in [('image/jpeg', 'mjpeg'), ('image/webp', 'libwebp')]:
        data = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=16x16',
                               '-frames:v', '1', '-threads', '1', '-c:v', codec, '-f', 'image2pipe', '-'],
                              check=True, capture_output=True, timeout=10).stdout
        response = c.post('/api/attachments', params={'name': 'photo', 'conversation_id': conversation},
                          content=data, headers={**dict(c.headers), 'Content-Type': mime})
        assert response.status_code == 200, response.text
        assert app.state.attachments.get(response.json()['id'])['native_image']
    class Probe:
        returncode = 0
        async def communicate(self):
            return json.dumps({'streams': [{'codec_name': 'png', 'width': 5000, 'height': 5000}]}).encode(), b''
    async def oversized(*args, **kwargs):
        return Probe()
    monkeypatch.setattr('ai_manager.attachments.asyncio.create_subprocess_exec', oversized)
    response = c.post('/api/attachments', params={'name': 'large.png', 'conversation_id': conversation},
                      content=PNG, headers={**dict(c.headers), 'Content-Type': 'image/png'})
    assert response.status_code == 422 and '20 megapixels' in response.text
    async def unavailable(*args, **kwargs):
        raise OSError('fixture decoder unavailable')
    monkeypatch.setattr('ai_manager.attachments.asyncio.create_subprocess_exec', unavailable)
    response = c.post('/api/attachments', params={'name': 'valid.png', 'conversation_id': conversation},
                      content=PNG, headers={**dict(c.headers), 'Content-Type': 'image/png'})
    assert response.status_code == 200
    assert not app.state.attachments.get(response.json()['id'])['native_image']

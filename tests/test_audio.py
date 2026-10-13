import io
import json
import shutil
import subprocess
import wave
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from test_web import client as web_client
from test_web import sign_in

from ai_manager.audio import (
    MAX_BYTES,
    MAX_SECONDS,
    MAX_TRANSCRIPT_CHARS,
    PROVIDER_TIMEOUT,
    Audio,
    GrokTranscriber,
    NoRedirect,
    audio_duration,
)
from ai_manager.config import Settings

client = web_client


def wav(seconds=0.2):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(b'\0\0' * int(8000 * seconds))
    return buffer.getvalue()


class Provider:
    async def transcribe(self, data, mime, filename=None):
        self.data, self.mime, self.filename = data, mime, filename
        return 'Recognized speech'


def test_auth_config_bounded_upload_and_no_chat_or_retention(client):
    c, app, settings = client
    assert c.get('/api/audio').status_code == 401
    assert c.post('/api/audio/transcribe', content=wav()).status_code == 401
    sign_in(c, app, settings)
    headers = dict(c.headers)
    settings.xai_api_key = ''
    assert c.get('/api/audio').json()['available'] is False
    assert c.post('/api/audio/transcribe', headers=headers, content=wav()).status_code == 503
    settings.xai_api_key = 'private-test-secret'
    provider = app.state.audio.provider = Provider()
    assert 'private-test-secret' not in c.get('/api/audio').text
    assert 'private-test-secret' not in repr(settings)
    assert c.post('/api/audio/transcribe', content=wav(), headers={'Content-Type': 'audio/wav', 'X-CSRF-Token': ''}).status_code == 403
    h = {**headers, 'Content-Type': 'audio/wav'}
    assert c.post('/api/audio/transcribe', headers={**h, 'Origin': 'https://other.test'}, content=wav()).status_code == 403
    before = {p.name for p in settings.data_dir.iterdir()}
    response = c.post('/api/audio/transcribe', content=wav(), headers=h)
    assert response.status_code == 200, response.text
    assert response.json()['text'] == 'Recognized speech'
    assert provider.data == wav() and provider.mime == 'audio/wav'
    assert {p.name for p in settings.data_dir.iterdir()} == before
    assert not app.state.db.all('SELECT * FROM messages')
    assert c.post('/api/audio/transcribe', headers=h, content=b'').status_code == 422
    assert c.post('/api/audio/transcribe', headers=h, content=b'RIFFbad!WAVEinvalid').status_code == 200
    assert c.post('/api/audio/transcribe', headers=h, content=b'bad').status_code == 200
    assert c.post('/api/audio/transcribe', headers={**h, 'Content-Type': 'text/plain'}, content=wav()).status_code == 200
    assert c.post('/api/audio/transcribe', headers=h, content=b'x' * (MAX_BYTES + 1)).status_code == 413
    assert c.post('/api/audio/transcribe', headers=h, content=wav(MAX_SECONDS + 2)).status_code == 413
    app.state.audio.busy = True
    assert c.post('/api/audio/transcribe', headers=h, content=wav()).status_code == 429
    app.state.audio.busy = False
    assert c.post('/api/audio/transcribe', headers=h, content=wav()).status_code == 200


def test_missing_dependency_provider_and_no_fallback():
    settings = Settings(xai_api_key='test', audio_probe='/missing/probe')
    audio = Audio(settings)
    assert 'ffprobe' in audio.status()['reason']
    settings.audio_provider = 'other'
    assert 'unsupported' in audio.status()['reason']
    settings.audio_provider = 'grok'
    settings.audio_model = 'invalid\r\nmodel'
    assert 'invalid' in audio.status()['reason']


@pytest.mark.asyncio
async def test_real_probe_duration():
    assert await audio_duration(wav(), 'audio/wav', 'ffprobe') == pytest.approx(0.2)


def test_grok_official_contract(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert timeout == PROVIDER_TIMEOUT
            return Response(json.dumps({'text': '  speech  ', 'duration': 1}).encode())

    monkeypatch.setattr('ai_manager.audio.build_opener', lambda handler: Opener())
    provider = GrokTranscriber('server-secret', 'grok-voice-transcribe-2.0')
    assert provider._request(wav(), 'audio/wav') == 'speech'
    request = calls[0]
    assert request.full_url == 'https://api.x.ai/v1/stt'
    assert request.get_header('Authorization') == 'Bearer server-secret'
    assert request.data.index(b'name="model"') < request.data.index(b'name="file"')
    assert b'grok-voice-transcribe-2.0' in request.data
    assert wav() in request.data
    assert NoRedirect().redirect_request(request, None, 302, '', {}, 'https://other.test') is None


@pytest.mark.parametrize('failure', [400, 401, 403, 415, 422, 429, 500, 'network', 'json', 'empty', 'type', 'long'])
def test_provider_failures_sanitized(monkeypatch, failure):
    class Opener:
        def open(self, request, timeout):
            if isinstance(failure, int):
                raise HTTPError(request.full_url, failure, 'private-test-secret', {}, io.BytesIO(b'secret-body'))
            if failure == 'network':
                raise URLError('private-test-secret')
            return io.BytesIO({'json': b'private-test-secret', 'empty': b'{"text":" "}',
                              'type': b'{"text": {}}', 'long': json.dumps({'text': 'x'*(MAX_TRANSCRIPT_CHARS + 1)}).encode()}[failure])
    monkeypatch.setattr('ai_manager.audio.build_opener', lambda handler: Opener())
    with pytest.raises(HTTPException) as caught:
        GrokTranscriber('private-test-secret', 'grok-voice-transcribe-2.0')._request(wav(), 'audio/wav')
    assert caught.value.status_code == 502
    assert 'private-test-secret' not in caught.value.detail
    assert 'secret-body' not in caught.value.detail


@pytest.mark.parametrize('mime,codec,container,extra', [
    ('audio/ogg', 'libopus', 'ogg', []),
    ('audio/mp4', 'aac', 'mp4', ['-movflags', 'frag_keyframe+empty_moov']),
    ('audio/webm', 'libopus', 'webm', []),
])
@pytest.mark.asyncio
async def test_real_supported_browser_containers(mime, codec, container, extra):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is used only to generate test audio')
    encoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-c:a', codec,
                              *extra, '-f', container, 'pipe:1'], input=wav(0.5),
                             capture_output=True, check=True, timeout=10).stdout
    assert 0.4 <= await audio_duration(encoded, mime, 'ffprobe') <= 0.8


def test_failure_releases_upload_slot_for_retry(client):
    c, app, settings = client
    sign_in(c, app, settings)
    settings.xai_api_key = 'test'

    class Failed:
        async def transcribe(self, data, mime, filename=None):
            raise HTTPException(502, 'Provider temporarily unavailable')

    app.state.audio.provider = Failed()
    assert c.post('/api/audio/transcribe', content=wav(), headers={'Content-Type': 'audio/wav'}).status_code == 502
    assert app.state.audio.busy is False
    app.state.audio.provider = Provider()
    assert c.post('/api/audio/transcribe', content=wav(), headers={'Content-Type': 'audio/wav'}).status_code == 200


@pytest.mark.parametrize('seconds,accepted', [(1200, True), (1201, True), (1201.01, False), (1202, False)])
@pytest.mark.asyncio
async def test_twenty_minute_duration_boundary(seconds, accepted):
    if accepted:
        assert await audio_duration(wav(seconds), 'audio/wav', 'ffprobe') == pytest.approx(seconds)
    else:
        with pytest.raises(HTTPException) as caught:
            await audio_duration(wav(seconds), 'audio/wav', 'ffprobe')
        assert caught.value.status_code == 413


@pytest.mark.parametrize('mime,codec,container,extra', [
    ('audio/ogg', 'libopus', 'ogg', ['-vbr', 'off']),
    ('audio/webm', 'libopus', 'webm', ['-vbr', 'off']),
    ('audio/mp4', 'aac', 'mp4', ['-movflags', 'frag_keyframe+empty_moov']),
])
def test_twenty_minute_full_upload_real_probe(client, mime, codec, container, extra):
    # Encode faster than realtime; real 60k Opus packets exercise the metadata cap.
    encoded = subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '1200', '-ac', '1', '-c:a', codec, '-b:a', '64000', *extra,
        '-f', container, 'pipe:1',
    ], capture_output=True, check=True, timeout=60).stdout
    assert 8 * 1024 * 1024 < len(encoded) < MAX_BYTES
    c, app, settings = client
    sign_in(c, app, settings)
    settings.xai_api_key = 'fixture-only'
    provider = app.state.audio.provider = Provider()
    assert c.get('/api/audio').json()['max_seconds'] == 1200
    assert c.get('/api/audio').json()['max_bytes'] == MAX_BYTES
    response = c.post('/api/audio/transcribe', content=encoded, headers={'Content-Type': mime})
    assert response.status_code == 200, response.text
    assert 1200 <= response.json()['duration'] <= 1201
    assert provider.data == encoded and provider.mime == mime
    assert not app.state.db.all('SELECT * FROM messages')


def test_long_transcript_can_be_inserted_and_submitted(monkeypatch):
    from ai_manager.web import MessageBody

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps({'text': 'x' * MAX_TRANSCRIPT_CHARS}).encode())
    monkeypatch.setattr('ai_manager.audio.build_opener', lambda handler: Opener())
    text = GrokTranscriber('fixture-only', 'grok-voice-transcribe-2.0')._request(wav(), 'audio/wav')
    assert len(text) == MAX_TRANSCRIPT_CHARS
    assert MessageBody(content=text).content == text


@pytest.mark.asyncio
async def test_upload_deadline_releases_slot(monkeypatch):
    import asyncio

    monkeypatch.setattr('ai_manager.audio.UPLOAD_TIMEOUT', 0.01)
    audio = Audio(Settings(xai_api_key='fixture-only'))
    provider = audio.provider = Provider()

    class SlowRequest:
        headers = {'content-type': 'audio/wav'}

        async def stream(self):
            yield wav()[:12]
            await asyncio.sleep(0.05)
            yield wav()[12:]

    with pytest.raises(HTTPException) as caught:
        await audio.transcribe(SlowRequest())
    assert caught.value.status_code == 408
    assert not audio.busy and not hasattr(provider, 'data')


@pytest.mark.parametrize('mime', [None, '', 'application/octet-stream', 'text/plain', 'audio/flac', 'audio/wav; codecs=unexpected'])
@pytest.mark.parametrize('data', [b'unfamiliar opaque bytes', wav()])
def test_uploaded_metadata_and_bytes_reach_actual_adapter(client, monkeypatch, mime, data):
    from email.parser import BytesParser
    from urllib.parse import quote

    c, app, settings = client
    sign_in(c, app, settings)
    settings.xai_api_key = 'fixture-only'
    app.state.audio.provider = GrokTranscriber('fixture-only', settings.audio_model)
    captured = []

    class Opener:
        def open(self, request, timeout):
            envelope = ('Content-Type: ' + request.get_header('Content-type') + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + request.data
            file = list(BytesParser().parsebytes(envelope).walk())[-1]
            captured.append((file.get_filename(), str(file['Content-Type']), file.get_payload(decode=True)))
            return io.BytesIO(b'{"text":"provider accepted"}')

    monkeypatch.setattr('ai_manager.audio.build_opener', lambda handler: Opener())
    name = 'original.unknown'
    headers = {'X-Audio-Filename': quote(name)}
    if mime is not None:
        headers['Content-Type'] = mime
    response = c.post('/api/audio/transcribe', content=data, headers=headers)
    assert response.status_code == 200, response.text
    assert captured == [(name, mime or 'application/octet-stream', data)]
    assert response.json()['duration'] == (pytest.approx(0.2) if data == wav() else None)
    assert not app.state.audio.busy


@pytest.mark.parametrize('code', [400, 415, 422])
def test_actual_provider_rejection_reaches_safe_api_error(client, monkeypatch, code):
    c, app, settings = client
    sign_in(c, app, settings)
    settings.xai_api_key = 'fixture-only'
    app.state.audio.provider = GrokTranscriber('fixture-only', settings.audio_model)
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise HTTPError(request.full_url, code, 'secret', {}, io.BytesIO(b'private-provider-body'))

    monkeypatch.setattr('ai_manager.audio.build_opener', lambda handler: Opener())
    response = c.post('/api/audio/transcribe', content=b'unknown bytes', headers={'Content-Type': 'text/plain', 'X-Audio-Filename': 'clip.xyz'})
    assert len(calls) == 1 and b'unknown bytes' in calls[0].data
    assert response.status_code == 502
    assert f'xAI rejected the audio file (HTTP {code})' in response.json()['detail']
    assert 'may not be supported' in response.json()['detail']
    assert 'private-provider-body' not in response.text and 'secret' not in response.text
    assert not app.state.audio.busy


@pytest.mark.asyncio
async def test_unreadable_probe_timeout_still_forwards(monkeypatch):
    async def timeout(*args):
        raise TimeoutError()
    monkeypatch.setattr('ai_manager.audio.audio_duration', timeout)
    audio = Audio(Settings(xai_api_key='fixture-only'))
    provider = audio.provider = Provider()

    class Request:
        headers = {'content-type': 'application/octet-stream', 'x-audio-filename': 'clip.xyz'}
        async def stream(self):
            yield b'opaque data'

    assert await audio.transcribe(Request()) == {'text': 'Recognized speech', 'duration': None}
    assert provider.data == b'opaque data' and provider.filename == 'clip.xyz'


def test_forwarding_metadata_header_safety():
    from ai_manager.audio import forwarding_filename, forwarding_mime
    assert forwarding_filename('../clip.xyz') == 'clip.xyz'
    assert forwarding_filename('clip"\r\nInjected: x.xyz') == 'clip___Injected: x.xyz'
    assert forwarding_filename('звук.flac') == 'звук.flac'
    assert forwarding_mime('audio/unknown; codecs=weird') == 'audio/unknown; codecs=weird'
    assert forwarding_mime('audio/wav\r\nInjected: x') == 'application/octet-stream'

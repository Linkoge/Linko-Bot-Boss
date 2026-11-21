"""Bounded, transient audio transcription. Codex inference is unrelated."""
import asyncio
import json
import math
import re
import shutil
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException

MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 1200
MAX_TRANSCRIPT_CHARS = 64000
UPLOAD_TIMEOUT = 120
PROBE_TIMEOUT = 30
PROVIDER_TIMEOUT = 300
MAX_PROBE_BYTES = 8 * 1024 * 1024
RECORDING_EXTENSIONS = {
    'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a',
    'audio/wav': 'wav', 'audio/x-wav': 'wav',
}


def forwarding_mime(value: str) -> str:
    """Keep supplied MIME/parameters; guard multipart header syntax."""
    value = value.strip()
    if not value or any(ord(c) < 32 or ord(c) > 126 for c in value):
        return 'application/octet-stream'
    return value


def forwarding_filename(value: str) -> str:
    """Retain basename/extension without allowing multipart header injection."""
    value = value.replace('\\', '/').rsplit('/', 1)[-1]
    return ''.join(c if ord(c) >= 32 and ord(c) != 127 and c != '"' else '_' for c in value)[:255] or 'upload'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def audio_duration(data: bytes, mime: str, executable: str) -> float:
    """Probe packets too: browser WebM commonly has no container duration."""
    process = await asyncio.create_subprocess_exec(
        executable, '-v', 'error', '-max_alloc', '16777216', '-protocol_whitelist', 'pipe', '-i', 'pipe:0',
        '-show_entries', 'format=duration:stream=codec_type:packet=pts_time,duration_time', '-of', 'json',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    async def feed():
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def read():
        output = bytearray()
        while chunk := await process.stdout.read(65536):
            output.extend(chunk)
            if len(output) > MAX_PROBE_BYTES:
                raise HTTPException(422, 'Recording metadata exceeds the supported limit.')
        return bytes(output)

    try:
        _, output, _ = await asyncio.wait_for(asyncio.gather(feed(), read(), process.wait()), PROBE_TIMEOUT)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    try:
        result = json.loads(output)
        if process.returncode or not any(s.get('codec_type') == 'audio' for s in result.get('streams', [])):
            raise ValueError()
        durations = [float(result.get('format', {}).get('duration', 0))]
        packets = result.get('packets', [])
        starts = [float(p['pts_time']) for p in packets if 'pts_time' in p]
        ends = [float(p['pts_time']) + float(p.get('duration_time', 0)) for p in packets if 'pts_time' in p]
        if starts:
            durations.append(max(ends) - min(starts))
        duration = max(durations)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, 'Recording could not be read. Retry or record again.') from None
    if duration > MAX_SECONDS + 1:  # Allow a final browser encoder packet at the boundary.
        raise HTTPException(413, 'Recording is too long. Stop sooner and record again.')
    return duration


class GrokTranscriber:
    """Single replaceable adapter; documented xAI REST multipart contract."""
    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    async def transcribe(self, data: bytes, mime: str, filename: str | None = None) -> str:
        work = asyncio.create_task(asyncio.to_thread(self._request, data, mime, filename))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Keep the admission slot until the non-cancellable HTTP thread exits.
            await work
            raise

    def _request(self, data: bytes, mime: str, filename: str | None = None) -> str:
        # Metadata describes the file; never invent an audio extension for uploads.
        mime = forwarding_mime(mime)
        filename = forwarding_filename(filename or 'recording.' + RECORDING_EXTENSIONS.get(mime.split(';')[0], 'audio'))
        boundary = uuid.uuid4().hex
        # The provider requires model/options before the last, file part.
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        request = Request('https://api.x.ai/v1/stt', data=body, headers={
            'Authorization': f'Bearer {self.api_key}', 'Content-Type': f'multipart/form-data; boundary={boundary}',
        })
        try:
            with build_opener(NoRedirect()).open(request, timeout=PROVIDER_TIMEOUT) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError()
                result = json.loads(raw)
                text = result.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSCRIPT_CHARS:
                    raise ValueError()
                return text.strip()
        except HTTPError as error:
            error.close()
            message = 'xAI transcription failed. Recording kept; retry later.'
            if error.code in (401, 403):
                message = 'xAI rejected the server credential or access. Check the server audio configuration.'
            elif error.code in (400, 415, 422):
                message = f'xAI rejected the audio file (HTTP {error.code}). Its format or contents may not be supported. File kept; try another file.'
            elif error.code == 429:
                message = 'xAI audio usage limit reached. Recording kept; retry later.'
            raise HTTPException(502, message) from None
        except (URLError, TimeoutError, OSError):
            raise HTTPException(502, 'xAI audio connection failed. Recording kept; retry later.') from None
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(502, 'No usable transcript returned. Recording kept; retry or record again.') from None


MAX_SPEECH_CHARS = 4000
TTS_TIMEOUT = 60
MAX_SPEECH_BYTES = 24 * 1024 * 1024


def speech_text(markdown: str) -> str:
    """Plain text for speaking: drop code, links' URLs and markdown punctuation; cap the length."""
    text = re.sub(r'```.*?(```|\Z)', ' ', markdown or '', flags=re.S)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+', '', text, flags=re.M)
    text = re.sub(r'[*_~|#>]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > MAX_SPEECH_CHARS:
        cut = text[:MAX_SPEECH_CHARS]
        end = max(cut.rfind('. '), cut.rfind('? '), cut.rfind('! '))
        text = cut[:end + 1] if end > MAX_SPEECH_CHARS // 2 else cut
    return text


class GrokSpeaker:
    """xAI text-to-speech (POST /v1/tts, same bearer key as transcription); returns MP3 bytes."""
    def __init__(self, api_key: str, voice: str):
        self.api_key, self.voice = api_key, voice.strip().lower()  # xAI voice ids are lowercase (eve, ara, rex, ...)

    async def speak(self, text: str) -> bytes:
        return await asyncio.to_thread(self._request, text)

    def _request(self, text: str) -> bytes:
        body = json.dumps({'text': text, 'voice_id': self.voice, 'language': 'en',
                           'output_format': {'codec': 'mp3', 'sample_rate': 24000, 'bit_rate': 128000}}).encode()
        request = Request('https://api.x.ai/v1/tts', data=body, headers={
            'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=TTS_TIMEOUT) as response:
                raw = response.read(MAX_SPEECH_BYTES + 1)
                if not raw or len(raw) > MAX_SPEECH_BYTES:
                    raise ValueError()
                return raw
        except HTTPError as error:
            error.close()
            message = 'xAI speech failed. Retry later.'
            if error.code in (401, 403):
                message = 'xAI rejected the server credential or access. Check the server audio configuration.'
            elif error.code == 429:
                message = 'xAI audio usage limit reached. Retry later.'
            elif error.code == 404:
                message = f'xAI does not know the speech voice "{self.voice}". Check AI_MANAGER_TTS_VOICE.'
            elif error.code in (400, 422):
                message = f'xAI rejected the speech request (HTTP {error.code}).'
            raise HTTPException(502, message) from None
        except (URLError, TimeoutError, OSError):
            raise HTTPException(502, 'xAI speech connection failed. Retry later.') from None
        except ValueError:
            raise HTTPException(502, 'No usable speech returned. Retry later.') from None


class Audio:
    def __init__(self, settings):
        self.settings = settings
        self.busy = False
        self.provider = GrokTranscriber(settings.xai_api_key, settings.audio_model)
        self.speaker = GrokSpeaker(settings.xai_api_key, settings.tts_voice)
        self.speaking = 0

    def status(self):
        error = None
        if self.settings.audio_provider != 'grok':
            error = 'Configured audio provider is unsupported.'
        elif not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', self.settings.audio_model):
            error = 'Configured audio model is invalid.'
        elif not self.settings.xai_api_key:
            error = 'Audio needs XAI_API_KEY in the server environment.'
        elif not shutil.which(self.settings.audio_probe):
            error = 'Audio needs ffprobe installed on the server.'
        return {'available': error is None, 'reason': error, 'provider': self.settings.audio_provider,
                'max_bytes': MAX_BYTES, 'max_seconds': MAX_SECONDS, 'mime_types': list(RECORDING_EXTENSIONS)}

    async def transcribe(self, request):
        status = self.status()
        if not status['available']:
            raise HTTPException(503, status['reason'])
        mime = forwarding_mime(request.headers.get('content-type', ''))
        filename_header = request.headers.get('x-audio-filename')
        filename = forwarding_filename(unquote(filename_header)) if filename_header else None
        if self.busy:
            raise HTTPException(429, 'Another recording is being transcribed. Retry shortly.')
        self.busy = True  # Before any await: one bounded upload/probe/provider request per process.
        try:
            data = bytearray()
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(413, 'Recording exceeds the upload limit. Download it or record a shorter clip.')
                    data.extend(chunk)
            if not data:
                raise HTTPException(422, 'The recording is empty. Record again.')
            try:
                duration = await audio_duration(bytes(data), mime, self.settings.audio_probe)
            except HTTPException as error:
                if error.status_code != 422:
                    raise
                # Local decoder support does not define provider format support.
                duration = None
            except TimeoutError:
                duration = None
            return {'text': await self.provider.transcribe(bytes(data), mime, filename), 'duration': duration}
        except TimeoutError:
            raise HTTPException(408, 'Audio upload timed out. Recording kept; retry.') from None
        finally:
            self.busy = False

    async def speak(self, text: str) -> bytes:
        if not self.settings.xai_api_key:
            raise HTTPException(503, 'Audio needs XAI_API_KEY in the server environment.')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,50}', self.settings.tts_voice):
            raise HTTPException(503, 'Configured speech voice is invalid.')
        text = speech_text(text)
        if not text:
            raise HTTPException(422, 'Nothing to read aloud in this message.')
        if self.speaking >= 2:
            raise HTTPException(429, 'Speech is busy. Retry shortly.')
        self.speaking += 1
        try:
            return await self.speaker.speak(text)
        finally:
            self.speaking -= 1

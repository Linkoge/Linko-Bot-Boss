"""Bounded, transient audio transcription. Codex inference is unrelated."""
import asyncio
import json
import math
import re
import shutil
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException

MAX_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 180
MIME_EXTENSIONS = {
    'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a',
    'audio/wav': 'wav', 'audio/x-wav': 'wav',
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def audio_duration(data: bytes, mime: str, executable: str) -> float:
    """Probe packets too: browser WebM commonly has no container duration."""
    signatures = {
        'webm': data.startswith(b'\x1aE\xdf\xa3'), 'ogg': data.startswith(b'OggS'),
        'm4a': len(data) >= 12 and data[4:8] == b'ftyp',
        'wav': data.startswith(b'RIFF') and data[8:12] == b'WAVE',
    }
    if not signatures[MIME_EXTENSIONS[mime]]:
        raise HTTPException(415, 'Recording format does not match its content type.')
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
            if len(output) > 2 * 1024 * 1024:
                raise HTTPException(422, 'Recording metadata exceeds the supported limit.')
        return bytes(output)

    try:
        _, output, _ = await asyncio.wait_for(asyncio.gather(feed(), read(), process.wait()), 10)
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
        raise HTTPException(413, 'Recording exceeds the three-minute limit.')
    return duration


class GrokTranscriber:
    """Single replaceable adapter; documented xAI REST multipart contract."""
    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    async def transcribe(self, data: bytes, mime: str) -> str:
        work = asyncio.create_task(asyncio.to_thread(self._request, data, mime))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Keep the admission slot until the non-cancellable HTTP thread exits.
            await work
            raise

    def _request(self, data: bytes, mime: str) -> str:
        boundary = uuid.uuid4().hex
        # The provider requires model/options before the last, file part.
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="recording.{MIME_EXTENSIONS[mime]}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        request = Request('https://api.x.ai/v1/stt', data=body, headers={
            'Authorization': f'Bearer {self.api_key}', 'Content-Type': f'multipart/form-data; boundary={boundary}',
        })
        try:
            with build_opener(NoRedirect()).open(request, timeout=60) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError()
                result = json.loads(raw)
                text = result.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 24000:
                    raise ValueError()
                return text.strip()
        except HTTPError as error:
            error.close()
            message = 'xAI transcription failed. Recording kept; retry later.'
            if error.code in (401, 403):
                message = 'xAI rejected the server credential or access. Check the server audio configuration.'
            elif error.code == 429:
                message = 'xAI audio usage limit reached. Recording kept; retry later.'
            raise HTTPException(502, message) from None
        except (URLError, TimeoutError, OSError):
            raise HTTPException(502, 'xAI audio connection failed. Recording kept; retry later.') from None
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(502, 'No usable transcript returned. Recording kept; retry or record again.') from None


class Audio:
    def __init__(self, settings):
        self.settings = settings
        self.busy = False
        self.provider = GrokTranscriber(settings.xai_api_key, settings.audio_model)

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
                'max_bytes': MAX_BYTES, 'max_seconds': MAX_SECONDS, 'mime_types': list(MIME_EXTENSIONS)}

    async def transcribe(self, request):
        status = self.status()
        if not status['available']:
            raise HTTPException(503, status['reason'])
        mime = request.headers.get('content-type', '').split(';')[0].lower()
        if mime not in MIME_EXTENSIONS:
            raise HTTPException(415, 'Use a WebM, Ogg, MP4/M4A or WAV recording.')
        if self.busy:
            raise HTTPException(429, 'Another recording is being transcribed. Retry shortly.')
        self.busy = True  # Before any await: one bounded upload/probe/provider request per process.
        try:
            data = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(413, 'Recording exceeds the 8 MiB upload limit.')
                    data.extend(chunk)
            if not data:
                raise HTTPException(422, 'The recording is empty. Record again.')
            try:
                duration = await audio_duration(bytes(data), mime, self.settings.audio_probe)
            except TimeoutError:
                raise HTTPException(422, 'Recording validation timed out. Retry or record again.') from None
            return {'text': await self.provider.transcribe(bytes(data), mime), 'duration': duration}
        except TimeoutError:
            raise HTTPException(408, 'Audio upload timed out. Recording kept; retry.') from None
        finally:
            self.busy = False

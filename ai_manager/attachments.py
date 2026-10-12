"""Owner-only bounded uploads; paths are generated, never supplied by a client."""
import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import time

from fastapi import HTTPException

from .db import new_id

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 4
MAX_STORAGE = 512 * 1024 * 1024
IMAGES = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}


def backup_attachments(data_dir, database_snapshot, destination):
    """Copy exactly the immutable uploads referenced by an already verified DB snapshot."""
    destination.mkdir(mode=0o700)
    with sqlite3.connect(database_snapshot) as snapshot:
        items = [json.loads(row[0]) for row in snapshot.execute("SELECT value FROM settings WHERE key LIKE 'attachment:%'")]
    for item in items:
        name = item['stored_name']
        if not re.fullmatch(r'[0-9a-f]{32}\.(bin|png|jpg|webp)', name):
            raise ValueError('Invalid stored attachment reference')
        source = data_dir / 'chat-workspace' / 'attachments' / name
        target = destination / name
        shutil.copyfile(source, target)
        target.chmod(0o600)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Attachment backup checksum mismatch')
    (destination / 'manifest.json').write_text(json.dumps(items, ensure_ascii=False))
    (destination / 'manifest.json').chmod(0o600)
    return len(items)


class Attachments:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.root = db.path.parent / 'chat-workspace' / 'attachments'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.busy = False

    def get(self, identifier):
        if not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('Invalid attachment ID')
        item = self.db.setting('attachment:' + identifier)
        if not item or not (self.root / item['stored_name']).is_file():
            raise ValueError('Attachment unavailable. Upload it again.')
        return item

    def resolve(self, identifiers, project, conversation):
        if len(identifiers) > MAX_FILES or len(set(identifiers)) != len(identifiers):
            raise ValueError('Use at most four distinct attachments per message')
        items = [self.get(identifier) for identifier in identifiers]
        for item in items:
            if item['project_id'] != project or item['conversation_id'] != conversation:
                raise ValueError('Attachment belongs to a different chat')
            path = self.root / item['stored_name']
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Attachment changed. Upload it again.')
        return items

    def reference(self, item):
        return {'id': item['id'], 'name': item['name'], 'mime': item['mime'], 'size': item['size'],
                'path': str(self.root / item['stored_name']), 'sha256': item['sha256']}

    async def upload(self, request, name, project, conversation):
        chat = self.db.one('SELECT * FROM conversations WHERE id=? AND project_id IS ? AND context=?',
                           (conversation, project, project or 'global'))
        if not chat:
            raise HTTPException(409, 'Chat changed. Refresh before uploading.')
        if not name or len(name) > 180 or any(ord(c) < 32 for c in name) or '/' in name or '\\' in name:
            raise HTTPException(422, 'Use a filename of at most 180 characters without path separators.')
        mime = request.headers.get('content-type', '').split(';')[0].lower()
        if mime.startswith('image/') and mime not in IMAGES:
            raise HTTPException(415, 'Photos support PNG, JPEG and WebP. Convert HEIC/SVG or other images first.')
        if self.busy:
            raise HTTPException(429, 'Another file is uploading. Retry shortly.')
        self.busy = True
        path = None
        try:
            data = bytearray()
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(413, 'Each attachment must be at most 8 MiB.')
                    data.extend(chunk)
            if not data:
                raise HTTPException(422, 'Empty files are unsupported.')
            if sum(p.stat().st_size for p in self.root.iterdir() if p.is_file()) + len(data) > MAX_STORAGE:
                raise HTTPException(507, 'Attachment storage is full. Ask the operator to review retained uploads.')
            identifier = new_id()
            stored = identifier + '.' + IMAGES.get(mime, 'bin')
            path = self.root / stored
            with path.open('xb') as file:
                path.chmod(0o600)
                file.write(data)
            if mime in IMAGES:
                # Bound parser output/time/allocation and decoded image dimensions.
                signatures = {'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'),
                              'image/jpeg': data.startswith(b'\xff\xd8\xff'),
                              'image/webp': data[:4] == b'RIFF' and data[8:12] == b'WEBP'}
                if not signatures[mime]:
                    raise HTTPException(415, 'Photo content does not match its format.')
                process = await asyncio.create_subprocess_exec(
                    self.settings.audio_probe, '-v', 'error', '-max_alloc', '16777216',
                    '-protocol_whitelist', 'file', '-i', str(path), '-show_entries',
                    'stream=width,height,codec_name', '-of', 'json', stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                try:
                    output, _ = await asyncio.wait_for(process.communicate(), 10)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                streams = json.loads(output).get('streams', []) if not process.returncode else []
                codecs = {'image/png': 'png', 'image/jpeg': 'mjpeg', 'image/webp': 'webp'}
                if not streams or any(s.get('codec_name') != codecs[mime] or
                                      not 0 < s.get('width', 0) * s.get('height', 0) <= 20000000 for s in streams):
                    raise HTTPException(422, 'Photo is unreadable or exceeds 20 megapixels.')
            item = {'id': identifier, 'stored_name': stored, 'name': name, 'mime': mime,
                    'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                    'project_id': project, 'conversation_id': conversation, 'created_at': time.time()}
            self.db.set_setting('attachment:' + identifier, item)
            path = None
            return {k: item[k] for k in ('id', 'name', 'mime', 'size')}
        except (TimeoutError, OSError, ValueError):
            raise HTTPException(422, 'Upload validation failed. File was not accepted; retry or convert it.') from None
        finally:
            if path:
                path.unlink(missing_ok=True)
            self.busy = False

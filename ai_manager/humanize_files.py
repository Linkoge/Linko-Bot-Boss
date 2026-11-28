"""Humanize a file from the Humanizer sheet: level 2 only (the script, no AI), per profile.

Uploads and results live under <data dir>/humanizer/<id>/ (the profile's own data folder, never the checkout). The
uploaded file is only ever read as text or as a zip package by the script; nothing in it is executed. The stored
upload is deleted as soon as the result exists. The latest RESULTS_KEPT results are kept and results older than
KEEP_DAYS days are deleted."""
import asyncio
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException

from . import humanize

MAX_BYTES = 20 * 1024 * 1024
MAX_UNPACKED = 200 * 1024 * 1024  # a .docx is a zip: refuse packages that inflate absurdly
RESULTS_KEPT = 10
KEEP_DAYS = 7
ID_RE = re.compile(r"^[0-9a-f]{32}$")
SUPPORTED = "Only .txt, .md and .docx files can be humanized. Convert other formats to one of these first."


class HumanizedFiles:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / "humanizer"

    def clean_name(self, name):
        name = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        name = "".join(c for c in name if ord(c) >= 32 and c != "\x7f")[:180]
        if not name or name in (".", ".."):
            raise HTTPException(422, "Choose a file to humanize.")
        if Path(name).suffix.lower() not in humanize.tool.SUFFIXES:
            raise HTTPException(422, SUPPORTED)
        return name

    async def run(self, request, name, typos, punct):
        name = self.clean_name(name)
        suffix = Path(name).suffix.lower()
        try:
            humanize.tool.check_rates(typos, punct)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        data = bytearray()
        async with asyncio.timeout(120):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, "The file must be at most 20 MB.")
                data.extend(chunk)
        if not data:
            raise HTTPException(422, "The file is empty.")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        folder = self.root / identifier
        folder.mkdir(mode=0o700)
        try:
            source = folder / f"upload{suffix}"
            source.write_bytes(bytes(data))
            result = await asyncio.to_thread(self._humanize, source, folder / f"result{suffix}", typos, punct)
            source.unlink(missing_ok=True)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        meta = {"id": identifier, "name": f"{Path(name).stem} humanized{suffix}", "typos": result[0],
                "dropped": result[1], "created_at": time.time()}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.prune()
        return meta

    @staticmethod
    def _humanize(source, target, typos, punct):
        if source.suffix == ".docx":
            try:
                with zipfile.ZipFile(source) as package:
                    if sum(item.file_size for item in package.infolist()) > MAX_UNPACKED:
                        raise HTTPException(422, "This document is too large once unpacked.")
            except zipfile.BadZipFile:
                raise HTTPException(422, "This is not a Word document (.docx).") from None
        try:
            _, edits = humanize.humanize_file(source, typos, punct, out=target)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return len(edits["words"]), len(edits["marks"])

    def _metas(self):
        found = []
        for folder in self.root.iterdir() if self.root.is_dir() else ():
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if folder.is_dir() and time.time() - folder.stat().st_mtime > 3600:
                    shutil.rmtree(folder, ignore_errors=True)  # a failed or abandoned run
                continue
            found.append(meta)
        return sorted(found, key=lambda meta: meta["created_at"], reverse=True)

    def prune(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        for index, meta in enumerate(self._metas()):
            if index >= RESULTS_KEPT or meta["created_at"] < cutoff:
                shutil.rmtree(self.root / meta["id"], ignore_errors=True)

    def list(self):
        self.prune()
        return self._metas()

    def get(self, identifier):
        if not ID_RE.match(identifier):
            raise HTTPException(404, "No such result.")
        folder = self.root / identifier
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404, "This result is no longer available.") from None
        return meta, folder / f"result{Path(meta['name']).suffix}"

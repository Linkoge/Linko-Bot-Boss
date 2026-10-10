"""Disposable verification copies allow builds without modifying reviewed workspaces."""

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

from .verification_strategies import content_digest
from .workspace import Workspace

MAX_ADDITIONAL_BYTES = 512 * 1024 * 1024


def directory_size(root):
    total, count = 0, 0
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in files:
            count += 1
            if count > 200_000:
                raise ValueError("Verification workspace file-count budget exceeded")
            total += (Path(directory) / name).lstat().st_size
    return total


def prepare_copy(data_dir, execution_id, canonical):
    root = data_dir / "verification-workspaces" / execution_id
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size = directory_size(canonical.root)
    if shutil.disk_usage(root.parent).free < size + MAX_ADDITIONAL_BYTES + 128 * 1024 * 1024:
        raise ValueError("Insufficient storage for disposable build verification and its output budget")
    shutil.copytree(canonical.root, root, symlinks=True)
    root.chmod(0o700)
    workspace = Workspace(data_dir, execution_id)
    workspace.root, workspace.branch = root, canonical.branch
    workspace.verification_input_snapshot = content_digest(canonical, "HEAD")
    workspace.verification_initial_size = size
    return workspace


async def run_checked(workspace, argv, cwd, baseline):
    expected = getattr(workspace, "verification_input_snapshot", None) or content_digest(workspace, baseline)
    initial_size = getattr(workspace, "verification_initial_size", None) or await asyncio.to_thread(
        directory_size, workspace.root
    )

    async def budget():
        while True:
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(directory_size, workspace.root) > initial_size + MAX_ADDITIONAL_BYTES:
                raise ValueError("Verification build output exceeded 512MB; isolated command terminated")
            if shutil.disk_usage(workspace.root).free < 128 * 1024 * 1024:
                raise ValueError("Verification storage reserve reached; isolated command terminated")

    guard = asyncio.create_task(budget())
    command = asyncio.create_task(workspace.isolated_test(argv, cwd=cwd, writable=True))
    try:
        completed, _ = await asyncio.wait([guard, command], return_when=asyncio.FIRST_COMPLETED)
        if guard in completed:
            await guard
        record = await command
        observed = content_digest(workspace, baseline)
        if any(observed.get(name) != digest for name, digest in expected.items()):
            raise ValueError(
                "Verification command modified project inputs in its disposable copy; acceptance is unverified"
            )
        record.update(
            {
                "verification_workspace": str(workspace.root),
                "environment": "bubblewrap-v3",
                "input_sha256": hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest(),
                "input_files_unchanged": True,
                "output_growth_limit_bytes": MAX_ADDITIONAL_BYTES,
            }
        )
        return record
    finally:
        guard.cancel()
        if not command.done():
            command.cancel()
        await asyncio.gather(guard, command, return_exceptions=True)

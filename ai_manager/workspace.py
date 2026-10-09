"""Workspace capabilities never accept an unrestricted shell command."""

import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

from .registry import git

SENSITIVE = {".git", ".env", ".ssh", ".aws", ".codex", "node_modules", ".venv"}


def safe_file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or any(part in SENSITIVE or part.startswith(".env.") for part in candidate.parts)
    ):
        raise ValueError("Path is outside the permitted project content")
    path = root / candidate
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Symlink escapes the workspace")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("Symlinks are not permitted by file capabilities")
        current = current.parent
    return path


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


class Workspace:
    def __init__(self, data_dir: Path, execution_id: str):
        self.root = data_dir / "workspaces" / execution_id
        self.branch = "ai-manager/" + execution_id[:12]

    def prepare(self, source: Path):
        if self.root.exists():
            return
        if not git(source, "rev-parse", "HEAD"):
            raise ValueError("Worker isolation requires a repository with a committed checkpoint")
        self.root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Standalone clone avoids shared mutable Git admin state and includes committed files only.
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--no-local",
                "--no-hardlinks",
                "--quiet",
                str(source),
                str(self.root),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "core.hooksPath=/dev/null", "checkout", "-b", self.branch],
            check=True,
            capture_output=True,
            timeout=15,
        )
        self.root.chmod(0o700)

    def list_files(self, path="."):
        directory = safe_file(self.root, path)
        if not directory.is_dir():
            raise ValueError("Directory does not exist")
        return [
            p.name + ("/" if p.is_dir() else "")
            for p in sorted(directory.iterdir())
            if p.name not in SENSITIVE and not p.name.startswith(".env.")
        ][:200]

    def read_file(self, path):
        file = safe_file(self.root, path)
        if not file.is_file() or file.stat().st_size > 100000:
            raise ValueError("Read requires a text file smaller than 100KB")
        return {"content": file.read_text(), "sha256": fingerprint(file)}

    def write_file(self, path, content, expected_hash):
        file = safe_file(self.root, path)
        if len(content.encode()) > 100000:
            raise ValueError("File write limit is 100KB")
        if fingerprint(file) != expected_hash:
            raise ValueError("File changed; read its current hash before writing")
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_name(file.name + ".ai-manager-tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError("Temporary path is already occupied")
        with temporary.open("x") as stream:
            stream.write(content)
        temporary.replace(file)
        return {"sha256": fingerprint(file)}

    async def isolated_test(self, argv: list[str], timeout=120):
        if not argv or not all(isinstance(a, str) and len(a) <= 1000 for a in argv):
            raise ValueError("Invalid test command")
        # Tests see system runtimes and this workspace, never the home directory or network.
        args = [
            "bwrap",
            "--die-with-parent",
            "--unshare-all",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--dir",
            "/workspace",
            "--bind",
            str(self.root),
            "/workspace",
            "--ro-bind",
            str(self.root / ".git"),
            "/workspace/.git",
            "--chdir",
            "/workspace",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--",
            *argv,
        ]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True
        )
        try:

            async def bounded_output():
                chunks, size = [], 0
                while chunk := await process.stdout.read(65536):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise ValueError("Test output exceeded 1MB; command terminated")
                    chunks.append(chunk)
                await process.wait()
                return b"".join(chunks)

            output = await asyncio.wait_for(bounded_output(), timeout)
            return {
                "argv": argv,
                "exit_code": process.returncode,
                "output": output.decode(errors="replace")[-20000:],
                "sandbox": "bubblewrap/no-network/no-host-home",
            }
        except (TimeoutError, ValueError, asyncio.CancelledError):
            import signal

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise

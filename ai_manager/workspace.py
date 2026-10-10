"""Workspace capabilities never accept an unrestricted shell command."""

import asyncio
import hashlib
import os
import shutil
import subprocess
import uuid
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
        sizes = git(source, "ls-tree", "-r", "-l", "HEAD").splitlines()
        total_bytes = sum(
            int(parts[3]) for line in sizes if len(parts := line.split(maxsplit=4)) >= 4 and parts[3].isdigit()
        )
        estimated_bytes = total_bytes * 3 + 20 * 1024 * 1024
        if shutil.disk_usage(self.root.parent).free < estimated_bytes + 128 * 1024 * 1024:
            raise ValueError(
                "Insufficient free storage for isolated workspace and 128MB reserve; free or expand storage before retry"
            )
        # Standalone clone avoids shared mutable Git admin state and includes committed files only.
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--no-local",
                "--no-hardlinks",
                "--single-branch",
                "--depth=1",
                "--quiet",
                source.resolve().as_uri(),
                str(self.root),
            ],
            check=True,
            capture_output=True,
            timeout=60,
            env={
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_LFS_SKIP_SMUDGE": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
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

    async def isolated_test(self, argv: list[str], timeout=120, cwd=".", writable=False):
        if not argv or not all(isinstance(a, str) and len(a) <= 1000 for a in argv):
            raise ValueError("Invalid test command")
        directory = safe_file(self.root, cwd)
        if not directory.is_dir():
            raise ValueError("Approved command working directory is unavailable")
        # Tests see system runtimes and this workspace, never the home directory or network.
        unit = "linko-verification-" + uuid.uuid4().hex + ".scope"
        args = [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--unit=" + unit,
            "--property=MemoryMax=2147483648",
            "--property=TasksMax=128",
            "--property=CPUQuota=100%",
            "prlimit",
            "--as=2147483648",
            "--cpu=120",
            "--nofile=256",
            "--fsize=67108864",
            "--",
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
            "--bind" if writable else "--ro-bind",
            str(self.root),
            "/workspace",
            "--ro-bind",
            str(self.root / ".git"),
            "/workspace/.git",
            "--chdir",
            "/workspace" + ("/" + cwd if cwd != "." else ""),
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
            "--setenv",
            "PYTEST_ADDOPTS",
            "-o cache_dir=/tmp/pytest-cache",
            "--setenv",
            "npm_config_cache",
            "/workspace/.npm-cache",
            "--setenv",
            "GOPROXY",
            "off",
            "--setenv",
            "GOCACHE",
            "/tmp/go-cache",
            "--setenv",
            "CARGO_HOME",
            "/workspace/.cargo-cache",
            "--",
            *argv,
        ]
        runtime = self.root / ".linko-runtime"
        if runtime.is_symlink():
            raise ValueError("Verification runtime cannot be a symlink")
        if runtime.is_dir():
            position = args.index("--chdir")
            args[position:position] = ["--ro-bind", str(runtime), "/workspace/.linko-runtime"]
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
                "cwd": cwd,
                "environment": "bubblewrap-v2",
                "resources": {
                    "per_process_address_space": 2147483648,
                    "cpu_seconds": 120,
                    "cgroup_tasks_max": 128,
                    "cgroup_memory_max": 2147483648,
                    "cpu_quota": "100%",
                    "wall_seconds": timeout,
                },
            }
        finally:
            import signal

            # Scope cleanup is required even when a test exits after spawning detached children.
            cleanup = await asyncio.create_subprocess_exec(
                "systemctl",
                "--user",
                "kill",
                "--signal=SIGKILL",
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(cleanup.wait(), 5)
            except TimeoutError:
                cleanup.kill()
                await cleanup.wait()
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
            try:
                await asyncio.wait_for(process.communicate(), 5)
            except TimeoutError:
                process._transport.close()

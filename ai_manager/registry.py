import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .db import Database, new_id

MEMORY_NAMES = ("PROJECT.md", "STATE.md", "TASKS.md", "DECISIONS.md", "HANDOFF.md")


def validate_display_name(value):
    if value is None:
        return None
    if len(value.strip()) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Display name must be at most 80 characters without control characters")
    return value.strip() or None


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def safe_remote(value: str) -> str:
    if "://" in value:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    return value


class Registry:
    def __init__(self, db: Database, roots: tuple[Path, ...]):
        self.db = db
        self.roots = tuple(p.resolve() for p in roots)

    def allowed_path(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise ValueError("Project must be inside a configured project root")
        if not resolved.is_dir():
            raise ValueError("Project directory does not exist")
        return resolved

    def add(self, path: str | Path, name: str | None = None) -> dict:
        name = validate_display_name(name)
        path = self.allowed_path(path)
        existing = self.db.one("SELECT * FROM projects WHERE path=?", (str(path),))
        if existing:
            return existing
        if self.db.setting("freeze_projects_until", 0) > time.time():
            raise ValueError("New project registrations are paused by the owner")
        project_id, now = new_id(), time.time()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id,name,path,repository,agent_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    project_id,
                    path.name,
                    str(path),
                    safe_remote(git(path, "remote", "get-url", "origin")),
                    f"project-{project_id[:12]}",
                    now,
                    now,
                ),
            )
            for filename in MEMORY_NAMES:
                source = path / filename
                # Existing files are read only; symlinks cannot bring private files into memory.
                content = (
                    source.read_text(errors="replace")[:24000]
                    if source.is_file() and not source.is_symlink()
                    else f"# {filename[:-3].title()} — {name or path.name}\n\nNo verified information recorded yet.\n"
                )
                conn.execute(
                    "INSERT INTO memory(project_id,name,content,updated_at) VALUES(?,?,?,?)",
                    (project_id, filename, content, now),
                )
            self.db.event(conn, "project.registered", {"path": str(path)}, project_id)
        if name:
            self.update(project_id, 0, {"display_name": name})
        self.export_memory(project_id)
        return self.get(project_id)

    def discover(self) -> list[dict]:
        found = []
        # Bounded traversal skips dependencies, build artifacts and worktrees under hidden dirs.
        skip = {"node_modules", "build", "dist", "vendor", "__pycache__"}

        def visit(path, depth):
            if depth > 3 or not path.is_dir() or path.is_symlink():
                return
            if (path / ".git").exists():
                if self.db.setting("freeze_projects_until", 0) <= time.time() or self.db.one(
                    "SELECT id FROM projects WHERE path=?", (str(path.resolve()),)
                ):
                    found.append(self.add(path))
                return
            for child in sorted(path.iterdir()):
                if not child.name.startswith(".") and child.name not in skip:
                    visit(child, depth + 1)

        for root in self.roots:
            if root.is_dir():
                visit(root, 0)
        return [p for p in found if p["status"] != "removed"]

    def list(self):
        return self.db.all(
            "SELECT * FROM projects WHERE status != 'removed' ORDER BY last_used_at DESC,COALESCE(display_name,name) COLLATE NOCASE,id"
        )

    def get(self, project_id):
        project = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("Unknown project")
        return project

    def update(self, project_id: str, revision: int, changes: dict):
        allowed = {"display_name", "description", "objective", "success_criteria", "instructions", "priority", "status"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Unsupported project update")
        if "status" in changes and changes["status"] not in {"active", "paused", "archived", "removed"}:
            raise ValueError("Invalid project status")
        if "display_name" in changes:
            changes = {**changes, "display_name": validate_display_name(changes["display_name"])}
        with self.db.transaction() as conn:
            assignments = ",".join(f"{key}=?" for key in changes)
            cursor = conn.execute(
                f"UPDATE projects SET {assignments},revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (*changes.values(), time.time(), project_id, revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("Project changed; refresh before editing")
            self.db.event(conn, "project.updated", changes, project_id)
        from .memory import sync_project

        sync_project(self.db, project_id)
        return self.get(project_id)

    def memory(self, project_id):
        self.get(project_id)
        return self.db.all("SELECT * FROM memory WHERE project_id=? ORDER BY name", (project_id,))

    def update_memory(self, project_id, name, content, revision):
        if name not in MEMORY_NAMES or len(content) > 100000:
            raise ValueError("Invalid memory document")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE memory SET content=?,revision=revision+1,updated_at=? WHERE project_id=? AND name=? AND revision=?",
                (content, time.time(), project_id, name, revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("Memory changed; refresh before editing")
            self.db.event(conn, "memory.updated", {"name": name, "revision": revision + 1}, project_id)
        self.export_memory(project_id)

    def export_memory(self, project_id):
        # Lock while projecting the authoritative DB to human-readable files.
        directory = self.db.path.parent / "memory" / project_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db.transaction() as conn:
            for row in conn.execute("SELECT * FROM memory WHERE project_id=?", (project_id,)):
                temporary = directory / (row["name"] + ".tmp")
                temporary.write_text(row["content"])
                temporary.chmod(0o600)
                temporary.replace(directory / row["name"])

    def reconcile(self, project_id):
        project = self.get(project_id)
        path = self.allowed_path(project["path"])
        result = {
            "head": git(path, "rev-parse", "HEAD"),
            "branch": git(path, "branch", "--show-current"),
            "changes": git(path, "status", "--porcelain"),
            "commits": git(path, "log", "-8", "--format=%h %s").splitlines(),
            "docs": [name for name in MEMORY_NAMES if (path / name).is_file()],
            "executions": self.db.all(
                "SELECT id,pid,process_start,status,thread_id,turn_id FROM executions WHERE project_id=? AND finished_at IS NULL",
                (project_id,),
            ),
        }
        # Observation only: a disconnected transport does not prove a process has exited.
        for execution in result["executions"]:
            execution["process_observed"] = bool(execution["pid"] and Path(f"/proc/{execution['pid']}").exists())
        with self.db.transaction() as conn:
            self.db.event(conn, "project.reconciled", result, project_id)
        return result

    def checkpoint(self, project_id, summary, evidence, commit_ref="", task_id=None):
        if not evidence or not summary:
            raise ValueError("Verified checkpoints require evidence and summary")
        checkpoint_id = new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)",
                (checkpoint_id, project_id, task_id, commit_ref, summary, json.dumps(evidence), time.time()),
            )
            self.db.event(
                conn,
                "checkpoint.recorded",
                {"id": checkpoint_id, "summary": summary, "evidence": evidence},
                project_id,
                task_id,
            )
        return checkpoint_id

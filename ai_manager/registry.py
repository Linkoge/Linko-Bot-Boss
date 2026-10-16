import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .codex import process_start
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
    def __init__(self, db: Database, roots: tuple[Path, ...], fixed: Path | None = None):
        self.db = db
        self.roots = tuple(p.resolve() for p in roots)
        self.fixed = fixed.resolve() if fixed else None  # restricted instance: the one and only project

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
        if self.fixed and path != self.fixed:
            raise ValueError("This instance has one fixed project")
        existing = self.db.one("SELECT * FROM projects WHERE path=?", (str(path),))
        if existing:
            return self.get(existing["id"])
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
            self.db.event(conn, "project.registered", {"path": str(path)}, project_id)
        if name:
            self.update(project_id, 0, {"display_name": name})
        return self.get(project_id)

    def discover(self) -> list[dict]:
        if self.fixed:
            for other in self.db.all("SELECT id FROM projects WHERE path!=? AND status!='removed'", (str(self.fixed),)):
                self.update(other["id"], self.get(other["id"])["revision"], {"status": "removed"}, force=True)
            return [self.add(self.fixed)]
        found = []
        # Bounded traversal skips dependencies, build artifacts and worktrees under hidden dirs.
        skip = {"node_modules", "build", "dist", "vendor", "__pycache__"}

        def visit(path, depth):
            if depth > 3 or not path.is_dir() or path.is_symlink():
                return
            if (path / ".git").exists():
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
        projects = self.db.all(
            "SELECT p.*,COALESCE(s.value,'true') AS visibility FROM projects p "
            "LEFT JOIN settings s ON s.key='project_visible:' || p.id "
            "WHERE p.status != 'removed' ORDER BY p.last_used_at DESC,COALESCE(p.display_name,p.name) COLLATE NOCASE,p.id"
        )
        for project in projects:
            project["visible"] = json.loads(project.pop("visibility"))
            project["github_backup"] = self.db.setting("github_backup:" + project["id"], {})
        return projects

    def get(self, project_id):
        project = self.db.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("Unknown project")
        project["visible"] = self.db.setting("project_visible:" + project_id, True)
        project["github_backup"] = self.db.setting("github_backup:" + project_id, {})
        return project

    def set_visibility(self, project_id: str, visible: bool):
        if not isinstance(visible, bool):
            raise ValueError("Visibility must be a boolean")
        with self.db.transaction() as conn:
            project = conn.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project or project["status"] == "removed":
                raise ValueError("Unknown or removed project")
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("project_visible:" + project_id, json.dumps(visible)),
            )
            self.db.event(conn, "project.visibility_changed", {"visible": visible}, project_id)
        return self.get(project_id)

    def update(self, project_id: str, revision: int, changes: dict, force=False):
        allowed = {"display_name", "description", "objective", "success_criteria", "instructions", "priority", "status"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("Unsupported project update")
        if "status" in changes and changes["status"] not in {"active", "paused", "archived", "removed"}:
            raise ValueError("Invalid project status")
        if self.fixed and not force and changes.get("status") in {"archived", "removed"}:
            raise ValueError("This instance has one fixed project; it cannot be removed")
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
        return self.get(project_id)

    def memory(self, project_id):
        self.get(project_id)
        return self.db.all("SELECT * FROM memory WHERE project_id=? ORDER BY name", (project_id,))

    def notes(self, project_id):
        notes = {}
        for row in self.memory(project_id):
            content = row["content"]
            before, marker, rest = content.partition("<!-- AI_MANAGER_GENERATED_START -->")
            if marker and "<!-- AI_MANAGER_GENERATED_END -->" in rest:
                content = before + rest.split("<!-- AI_MANAGER_GENERATED_END -->", 1)[1]
            if content.strip():
                notes[row["name"]] = content[:2000]
        return notes

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
            execution["process_observed"] = bool(
                execution["pid"]
                and execution["process_start"]
                and process_start(execution["pid"]) == execution["process_start"]
            )
        with self.db.transaction() as conn:
            self.db.event(conn, "project.reconciled", result, project_id)
        return result

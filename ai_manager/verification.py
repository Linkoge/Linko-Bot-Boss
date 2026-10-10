"""Static discovery and owner-bound reusable command approvals; no repository code runs here."""

import hashlib
import json
import re
import shlex
import shutil
import time
import tomllib
from pathlib import Path

from .workspace import safe_file

ENVIRONMENT = {
    "id": "bubblewrap-v3",
    "verification_filesystem": "disposable-build-copy/input-hashes-checked",
    "output_growth_limit_bytes": 536870912,
    "network": False,
    "host_home": False,
    "workspace": "/workspace",
    "timeout_seconds": 120,
    "memory_bytes": 2147483648,
    "cpu_seconds": 120,
    "process_limit": 128,
    "cgroup": "systemd-user-transient-scope",
    "output_bytes": 1048576,
    "filesystem": "project-only",
}
CONFIG_NAMES = {
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Makefile",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "uv.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "conftest.py",
}


def configuration(root):
    paths = [root / name for name in sorted(CONFIG_NAMES)]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir() and not workflows.is_symlink():
        paths += sorted(workflows.glob("*.y*ml"))[:40]
    hashes = {}
    for path in paths:
        safe_file(root, str(path.relative_to(root)))
        if path.is_file():
            if path.stat().st_size > 2_000_000:
                raise ValueError("Verification configuration exceeds inspection limit")
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def valid_command(argv, cwd="."):
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 30
        or any(not isinstance(arg, str) or not arg or len(arg) > 1000 or "\x00" in arg for arg in argv)
    ):
        raise ValueError("Invalid verification command")
    if not isinstance(cwd, str) or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
        raise ValueError("Command working directory must be project-relative")


def identity(argv, cwd, config):
    return hashlib.sha256(json.dumps([argv, cwd, ENVIRONMENT, config], sort_keys=True).encode()).hexdigest()


class VerificationCommands:
    def __init__(self, db, registry):
        self.db, self.registry = db, registry

    def root(self, project_id):
        return self.registry.allowed_path(self.registry.get(project_id)["path"])

    def execution_root(self, project_id, execution_id):
        execution = self.db.one(
            "SELECT worktree FROM executions WHERE id=? AND project_id=?", (execution_id, project_id)
        )
        if not execution or not execution["worktree"]:
            raise ValueError("No assigned isolated workspace")
        root = Path(execution["worktree"])
        if (
            root.is_symlink()
            or not root.resolve().is_relative_to((self.db.path.parent / "workspaces").resolve())
            or not (root / ".git").is_dir()
        ):
            raise ValueError("Command discovery workspace outside isolation")
        return root

    def discover(self, project_id, execution_id=None):
        root, found = self.execution_root(project_id, execution_id) if execution_id else self.root(project_id), {}
        existing = self.db.setting(f"verification_commands:{project_id}", [])
        if existing and not self.db.one("SELECT id FROM verification_commands WHERE project_id=?", (project_id,)):
            self.register(project_id, existing)
        config = configuration(root)

        def add(argv, source, explanation, cwd=".", purpose="verification"):
            valid_command(argv, cwd)
            directory = safe_file(root, cwd)
            if not directory.is_dir():
                return
            key = identity(argv, cwd, config)
            found[key] = {
                "id": key,
                "argv": argv,
                "cwd": cwd,
                "environment": ENVIRONMENT,
                "config": config,
                "source": source,
                "explanation": explanation,
                "requirements": self.requirements(argv),
                "purpose": purpose,
                "execution_id": execution_id,
                "working_directory": "/workspace" + ("/" + cwd if cwd != "." else ""),
            }

        def content(name):
            p = safe_file(root, name)
            return p.read_text(errors="replace") if p.is_file() and p.stat().st_size < 500_000 else ""

        py = content("pyproject.toml")
        if py:
            try:
                data = tomllib.loads(py)
                if "pytest" in data.get("tool", {}) or any(
                    "pytest" in str(v) for v in data.get("dependency-groups", {}).values()
                ):
                    add(["python3", "-m", "pytest", "-q"], "pyproject.toml", "Run configured Python pytest suite")
                if "ruff" in data.get("tool", {}):
                    add(["python3", "-m", "ruff", "check", "."], "pyproject.toml", "Check Python static analysis")
            except tomllib.TOMLDecodeError:
                pass
        if content("pytest.ini") or "pytest" in content("tox.ini"):
            add(["python3", "-m", "pytest", "-q"], "pytest.ini/tox.ini", "Run configured pytest suite")
        # unittest is proposed only if actual test code uses it.
        tests = sorted(root.glob("test*.py")) + (
            sorted((root / "tests").glob("test*.py")) if (root / "tests").is_dir() else []
        )
        for path in tests[:30]:
            if "unittest" in content(str(path.relative_to(root))):
                add(
                    ["python3", "-m", "unittest", "discover", "-v"],
                    str(path.relative_to(root)),
                    "Discover and run unittest tests",
                )
                break
        package = content("package.json")
        if package:
            try:
                scripts = json.loads(package).get("scripts", {})
                for name in ("test", "lint", "typecheck", "build"):
                    if name in scripts:
                        add(
                            ["npm", "test"] if name == "test" else ["npm", "run", name],
                            "package.json",
                            f"Run npm {name} script: {str(scripts[name])[:250]}",
                        )
                if content("package-lock.json"):
                    add(
                        ["npm", "ci", "--offline", "--ignore-scripts"],
                        "package-lock.json",
                        "Install locked dependencies from project-local offline npm cache; no network or lifecycle scripts",
                        purpose="dependency",
                    )
            except (json.JSONDecodeError, AttributeError):
                pass
        if content("Cargo.toml"):
            add(["cargo", "test", "--offline"], "Cargo.toml", "Run Rust tests using offline dependencies")
        if content("go.mod"):
            add(["go", "test", "./..."], "go.mod", "Run Go package tests; modules must be available offline")
        make = content("Makefile")
        for target in ("test", "check", "lint"):
            if re.search(rf"^{target}\s*:", make, re.M):
                add(
                    ["make", target],
                    "Makefile",
                    f"Run Makefile {target} target (repository code executes in isolation)",
                )
        docs = ["README.md", "PROJECT.md", "HANDOFF.md", "docs/verification.md"] + list(config)
        for name in dict.fromkeys(docs):
            for line in content(name).splitlines():
                line = re.sub(r"^\s*(?:-\s*run:\s*|run:\s*|\$\s*)", "", line).strip().strip("`")
                if not re.match(r"^(python3?|uv run|npm|pnpm|yarn|cargo|go test|make|pytest|ruff)\s", line):
                    continue
                if any(c in line for c in ("&&", ";", "|", ">", "<", "$", "`")):
                    continue
                try:
                    argv = shlex.split(line)
                    if not any(
                        x in argv for x in ("test", "pytest", "unittest", "check", "lint", "typecheck", "build")
                    ):
                        continue
                    add(
                        argv,
                        name,
                        "Documented verification command; runtime and dependencies must exist in the sandbox",
                    )
                except ValueError:
                    continue
        for command in self.db.setting(f"verification_commands:{project_id}", []):
            add(command, "existing owner registration", "Previously owner-registered verification command")
        for result in self.db.all(
            "SELECT evidence FROM verification_results v JOIN tasks t ON t.id=v.task_id WHERE t.project_id=? AND v.passed=1 ORDER BY v.created_at DESC LIMIT 10",
            (project_id,),
        ):
            for record in json.loads(result["evidence"]).get("tests", []):
                if record.get("argv"):
                    add(
                        record["argv"], "successful execution history", "Previously successful independent verification"
                    )
        with self.db.transaction() as conn:
            for item in found.values():
                conn.execute(
                    "INSERT OR IGNORE INTO verification_commands(id,project_id,definition,status,created_at) VALUES(?,?,?,'discovered',?)",
                    (item["id"] + ":" + project_id, project_id, json.dumps(item), time.time()),
                )
        return self.snapshot(project_id)

    @staticmethod
    def requirements(argv):
        runtime = argv[0]
        deps = "Repository scripts may execute arbitrary code. Dependencies must be provisioned offline in the isolated workspace."
        if runtime == "uv" or runtime.startswith("/home/"):
            deps += " Host home runtimes are unavailable; select system runtime or provision a project environment."
        if runtime.startswith("python") and "-m" in argv:
            deps += " Required module: " + (
                argv[argv.index("-m") + 1] if argv.index("-m") + 1 < len(argv) else "unspecified"
            )
        return {"runtime": runtime, "dependencies": deps}

    def snapshot(self, project_id):
        config = configuration(self.root(project_id))
        rows = self.db.all(
            "SELECT * FROM verification_commands WHERE project_id=? ORDER BY created_at,id", (project_id,)
        )
        commands = []
        for row in rows:
            item = json.loads(row["definition"])
            observed = config
            if item.get("execution_id"):
                try:
                    observed = configuration(self.execution_root(project_id, item["execution_id"]))
                except ValueError:
                    observed = None
            commands.append(
                {
                    **item,
                    "id": row["id"],
                    "status": "stale" if row["status"] == "approved" and item["config"] != observed else row["status"],
                    "decided_at": row["decided_at"],
                }
            )
        return {
            "commands": self.db.setting(f"verification_commands:{project_id}", []),
            "candidates": commands,
            "missing": not any(c["status"] == "approved" and c["purpose"] == "verification" for c in commands),
            "environment": ENVIRONMENT,
            "failures": self.db.all(
                "SELECT v.* FROM verification_results v JOIN tasks t ON t.id=v.task_id WHERE t.project_id=? AND v.passed=0 ORDER BY created_at DESC LIMIT 10",
                (project_id,),
            ),
        }

    def decide(self, project_id, decisions):
        config = configuration(self.root(project_id))
        with self.db.transaction() as conn:
            for choice in decisions:
                row = conn.execute(
                    "SELECT * FROM verification_commands WHERE id=? AND project_id=?", (choice["id"], project_id)
                ).fetchone()
                if not row or choice["status"] not in {"approved", "rejected", "revoked"}:
                    raise ValueError("Invalid scoped command decision")
                item = json.loads(row["definition"])
                observed = (
                    configuration(self.execution_root(project_id, item["execution_id"]))
                    if item.get("execution_id")
                    else config
                )
                if choice["status"] == "approved" and item["config"] != observed:
                    raise ValueError("Configuration changed; rediscover and review commands")
                conn.execute(
                    "UPDATE verification_commands SET status=?,decided_at=? WHERE id=?",
                    (choice["status"], time.time(), row["id"]),
                )
                self.db.event(
                    conn,
                    "verification.command_decided",
                    {
                        "id": row["id"],
                        "status": choice["status"],
                        "definition": item,
                        "authorized_by": "authenticated_owner",
                    },
                    project_id,
                )
            approved = [
                json.loads(r[0])["argv"]
                for r in conn.execute(
                    "SELECT definition FROM verification_commands WHERE project_id=? AND status='approved'",
                    (project_id,),
                )
                if json.loads(r[0])["purpose"] == "verification"
            ]
            conn.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"verification_commands:{project_id}", json.dumps(approved)),
            )
        return {
            "result": "accepted",
            "message": "Scoped command decisions saved; unchanged approvals persist until revoked",
            **self.snapshot(project_id),
        }

    def register(self, project_id, commands):
        """Compatibility owner registration: replace command set, retain revocation history."""
        root, config = self.root(project_id), configuration(self.root(project_id))
        for argv in commands:
            valid_command(argv)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE verification_commands SET status='revoked',decided_at=? WHERE project_id=? AND status='approved' AND json_extract(definition,'$.purpose')='verification'",
                (time.time(), project_id),
            )
            for argv in commands:
                item = {
                    "argv": argv,
                    "cwd": ".",
                    "environment": ENVIRONMENT,
                    "config": config,
                    "purpose": "verification",
                    "source": "owner registration",
                    "explanation": "Explicit owner-provided command",
                    "requirements": self.requirements(argv),
                }
                cid = identity(argv, ".", config) + ":" + project_id
                conn.execute(
                    "INSERT INTO verification_commands VALUES(?,?,?,'approved',?,?) ON CONFLICT(id) DO UPDATE SET status='approved',decided_at=excluded.decided_at",
                    (cid, project_id, json.dumps(item), time.time(), time.time()),
                )
            conn.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"verification_commands:{project_id}", json.dumps(commands)),
            )
            self.db.event(
                conn,
                "verification.configured",
                {"commands": commands, "config": config, "environment": ENVIRONMENT, "configured_by": "owner"},
                project_id,
            )
        return {
            "updated": True,
            "result": "accepted",
            "message": "Trusted command configuration saved; only confirmed missing-configuration verification can recover automatically",
            "workspace": str(root),
        }

    def approved(self, project_id, root=None, purpose="verification"):
        rows = self.db.all(
            "SELECT * FROM verification_commands WHERE project_id=? AND status='approved'", (project_id,)
        )
        tracked = self.db.one("SELECT id FROM verification_commands WHERE project_id=?", (project_id,))
        if not tracked:
            # Preserve old owner-approved commands. No migration of decisions or policy.
            return (
                [
                    {"argv": argv, "cwd": ".", "environment": ENVIRONMENT, "legacy": True}
                    for argv in self.db.setting(f"verification_commands:{project_id}", [])
                ]
                if purpose == "verification"
                else []
            )
        config = configuration(root or self.root(project_id))
        approved, stale = [], False
        for row in rows:
            item = json.loads(row["definition"])
            if item["purpose"] != purpose:
                continue
            if item["config"] != config or item["environment"] != ENVIRONMENT:
                stale = True
                continue
            approved.append({**item, "id": row["id"]})
        if stale and not approved:
            raise ValueError("Verification configuration changed materially; renewed owner approval required")
        return approved

    def assert_current(self, project_id, records, root, purpose="verification"):
        current = self.approved(project_id, root, purpose)
        for record in records:
            if not any(
                c.get("id") == record.get("id") and c["argv"] == record["argv"] and c["cwd"] == record["cwd"]
                for c in current
            ):
                raise ValueError("Command approval was revoked or replaced before verification completed")

    def add_definition(self, project_id, argv, cwd=".", purpose="verification", execution_id=None):
        if purpose not in {"verification", "dependency"}:
            raise ValueError("Invalid command purpose")
        valid_command(argv, cwd)
        root = self.execution_root(project_id, execution_id) if execution_id else self.root(project_id)
        if not safe_file(root, cwd).is_dir():
            raise ValueError("Command working directory does not exist")
        config = configuration(root)
        definition = {
            "argv": argv,
            "cwd": cwd,
            "environment": ENVIRONMENT,
            "config": config,
            "purpose": purpose,
            "execution_id": execution_id,
            "working_directory": "/workspace" + ("/" + cwd if cwd != "." else ""),
            "source": "owner proposal",
            "explanation": "Owner-defined isolated command",
            "requirements": self.requirements(argv),
        }
        cid = identity(argv, cwd, config) + ":" + purpose + ":" + project_id
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO verification_commands VALUES(?,?,?,'discovered',?,NULL)",
                (cid, project_id, json.dumps(definition), time.time()),
            )
            self.db.event(conn, "verification.command_proposed", {"id": cid, "definition": definition}, project_id)
        return {"id": cid, **definition, "status": "discovered"}


def recovery_identity(db, project_id, root):
    """Change-triggered retry signal; never a substitute for completed-worker reconciliation."""
    approved = db.all(
        "SELECT id,decided_at FROM verification_commands WHERE project_id=? AND status='approved' ORDER BY id",
        (project_id,),
    )
    raw_commands = db.setting(f"verification_commands:{project_id}", [])
    runtimes = {argv[0]: shutil.which(argv[0], path="/usr/bin:/bin") for argv in raw_commands if argv}
    artifacts = {}
    for name in (".venv", "node_modules", ".npm-cache", "vendor"):
        path = root / name
        if path.exists() and not path.is_symlink():
            stat = path.stat()
            artifacts[name] = [stat.st_mtime_ns, stat.st_size]
    return hashlib.sha256(
        json.dumps(
            [approved, raw_commands, runtimes, artifacts, db.setting("policy_epoch", 0)], sort_keys=True
        ).encode()
    ).hexdigest()

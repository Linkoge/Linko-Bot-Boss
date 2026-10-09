import asyncio
import hashlib
import json
import os
import subprocess
import time

from .policies import Policies
from .registry import git
from .workspace import fingerprint, safe_file

MUTATING = {"write": "edit_files", "delete": "delete_files", "commit": "create_commits", "test": "run_tests"}


class Capabilities:
    def __init__(self, db, settings, workspace, project_id, task_id, execution_id):
        self.db, self.settings, self.workspace = db, settings, workspace
        self.project_id, self.task_id, self.execution_id = project_id, task_id, execution_id
        self.policies = Policies(db)
        self.lock = asyncio.Lock()

    async def perform(self, args, call_id):
        try:
            return await self._perform(args, call_id)
        except Exception as error:
            with self.db.transaction() as conn:
                self.db.event(
                    conn,
                    "capability.failed",
                    {"operation": args.get("operation"), "error": str(error)[:2000]},
                    self.project_id,
                    self.task_id,
                    self.execution_id,
                )
            raise

    async def _perform(self, args, call_id):
        async with self.lock:
            operation = args["operation"]
            if operation not in {"list", "read", "write", "delete", "test", "commit"}:
                raise ValueError("Unknown capability")
            action = MUTATING.get(operation, "read_files")
            task = self.db.one("SELECT status,lease_owner FROM tasks WHERE id=?", (self.task_id,))
            if (
                not task
                or task["lease_owner"] != self.execution_id
                or task["status"] not in {"running", "awaiting_approval"}
            ):
                raise ValueError("Worker no longer owns an active task")
            if operation in {"read", "write", "delete"}:
                safe_file(self.workspace.root, args["path"])
            if operation in {"write", "delete"}:
                if fingerprint(safe_file(self.workspace.root, args["path"])) != args.get("expected_hash"):
                    raise ValueError("File changed; refresh its hash before requesting approval")
            if operation == "test":
                args = {**args, "commands": self.db.setting(f"verification_commands:{self.project_id}", [])}
            if operation == "commit":
                args = {**args, "workspace_state": self.state_digest()}
            consequences = f"{operation} in isolated workspace {self.workspace.root.name}: " + args.get(
                "path", args.get("message", "registered verification commands")
            )
            key = await self.policies.authorize(
                self.project_id, self.task_id, self.execution_id, action, args, call_id, consequences
            )
            current = self.db.one("SELECT status,lease_owner FROM tasks WHERE id=?", (self.task_id,))
            if not current or current["status"] != "running" or current["lease_owner"] != self.execution_id:
                raise ValueError("Task ownership changed before execution")
            if operation == "commit" and args["workspace_state"] != self.state_digest():
                raise ValueError("Workspace changed since the exact commit approval")
            with self.db.transaction() as conn:
                previous = conn.execute("SELECT * FROM operations WHERE operation_key=?", (key,)).fetchone()
                if previous:
                    if previous["state"] == "completed":
                        return json.loads(previous["result"])
                    raise ValueError("Previous operation outcome is uncertain; reconcile before retry")
                if self.policies.effective(self.project_id, action)["mode"] == "DENY":
                    raise ValueError("Policy now denies this operation")
                conn.execute(
                    "INSERT INTO operations(operation_key,execution_id,action,state,created_at) VALUES(?,?,?,'started',?)",
                    (key, self.execution_id, action, time.time()),
                )
            if operation == "list":
                result = self.workspace.list_files(args.get("path", "."))
            elif operation == "read":
                result = self.workspace.read_file(args["path"])
            elif operation == "write":
                result = self.workspace.write_file(args["path"], args["content"], args["expected_hash"])
            elif operation == "delete":
                path = safe_file(self.workspace.root, args["path"])
                if not path.is_file() or fingerprint(path) != args["expected_hash"]:
                    raise ValueError("Delete requires an existing file with the approved hash")
                path.unlink()
                result = {"deleted": args["path"]}
            elif operation == "test":
                commands = args["commands"]
                if not commands:
                    raise ValueError("No owner-configured verification commands")
                result = [await self.workspace.isolated_test(command) for command in commands]
            elif operation == "commit":
                result = await asyncio.to_thread(self.commit, args["message"])
            else:
                raise ValueError("Unknown controlled capability")
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE operations SET state='completed',result=? WHERE operation_key=?", (json.dumps(result), key)
                )
                if operation == "commit":
                    conn.execute("UPDATE executions SET commit_ref=? WHERE id=?", (result["commit"], self.execution_id))
                self.db.event(
                    conn,
                    "capability.executed",
                    {"action": action, "operation": operation, "path": args.get("path"), "key": key},
                    self.project_id,
                    self.task_id,
                    self.execution_id,
                )
            return result

    def commit(self, message):
        if not message or len(message) > 2000:
            raise ValueError("Commit needs a message of 1–2000 characters")
        root = self.workspace.root
        for path in git(root, "ls-files", "--modified", "--others", "--exclude-standard").splitlines():
            safe_file(root, path)
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        base = [
            "git",
            "-C",
            str(root),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=AI Manager",
            "-c",
            "user.email=ai-manager@localhost",
        ]
        subprocess.run([*base, "add", "--all"], env=env, check=True, capture_output=True, timeout=15)
        subprocess.run(
            [*base, "commit", "--no-gpg-sign", "-m", message], env=env, check=True, capture_output=True, timeout=30
        )
        return {"commit": git(root, "rev-parse", "HEAD"), "branch": self.workspace.branch, "source_unchanged": True}

    def state_digest(self):
        root = self.workspace.root
        names = set(git(root, "diff", "HEAD", "--name-only", "-z").split("\0")) | set(
            git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        )
        files = {name: fingerprint(safe_file(root, name)) for name in names if name}
        return hashlib.sha256(
            json.dumps({"head": git(root, "rev-parse", "HEAD"), "files": files}, sort_keys=True).encode()
        ).hexdigest()

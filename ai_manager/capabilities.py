import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .policies import Policies
from .registry import git
from .verification import VerificationCommands, valid_command
from .workspace import fingerprint, safe_file, validate_git_admin

MUTATING = {
    "command": "execute_commands",
    "write": "edit_files",
    "delete": "delete_files",
    "commit": "create_commits",
    "test": "run_tests",
    "install": "install_dependencies",
}


def catastrophic_command(argv):
    """Catch explicit catastrophic requests; OS isolation contains arbitrary program effects.

    This is a confirmation trigger, not a shell security boundary. Host repositories,
    databases, sockets and infrastructure credentials are never mounted.
    """
    executable = Path(argv[0]).name
    if executable in {"git", "grep", "rg", "cat", "echo", "printf"}:
        return False  # Inspection and commit messages mentioning destruction are ordinary work.
    text = " ".join(argv).casefold()
    if executable in {"sh", "bash", "dash", "zsh"} and "-c" in argv:
        text = argv[argv.index("-c") + 1].casefold()
    if re.search(r"\bdrop\s+(database|schema)\b", text) and (
        executable in {"psql", "mysql", "mariadb", "sqlcmd"}
        or re.search(r"\b(psql|mysql|mariadb|sqlcmd)\b|\.execute\s*\(", text)
    ):
        return True
    if re.search(
        r"\b(terraform|tofu|pulumi)\b.*\bdestroy\b|\baws\b.*\b(terminate-instances|delete-db-instance)\b", text
    ):
        return True
    if re.search(r"\bmkfs(?:\.\w+)?\b|\bwipefs\b", text):
        return True
    if re.search(r"\brm\b", text) and re.search(
        r"(?:^|\s)(?:/|/workspace/?|/workspace/\*|\.|\./\*|\*)(?:\s|$|[;])", text
    ):
        return True
    return False


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
            if operation not in {"list", "read", "write", "delete", "test", "commit", "install", "command"}:
                raise ValueError("Unknown capability")
            action = MUTATING.get(operation, "read_files")
            validate_git_admin(self.workspace.root)
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
                    raise ValueError("File changed; refresh its hash before writing")
            if operation == "write" and args.get("expected_hash") == "missing":
                # Keep stable operation categories in the audit ledger.
                action = "create_files"
            if operation in {"command", "test", "install"}:
                from .registry import Registry

                selector = VerificationCommands(self.db, Registry(self.db, self.settings.project_roots))
                if args.get("argv"):
                    valid_command(args["argv"], args.get("cwd", "."))
                    safe_file(self.workspace.root, args.get("cwd", "."))
                    commands = [{"argv": args["argv"], "cwd": args.get("cwd", ".")}]
                elif operation == "command":
                    raise ValueError("Command requires argv")
                else:
                    commands = selector.selected(
                        self.project_id, self.workspace.root, "dependency" if operation == "install" else "verification"
                    )
                    if operation == "install" and args.get("command_id"):
                        commands = [c for c in commands if c["id"] == args["command_id"]]
                args = {**args, "commands": commands}
                if args.get("catastrophic") or any(catastrophic_command(c["argv"]) for c in commands):
                    action = "destructive_operations"
            if operation == "commit":
                args = {**args, "workspace_state": self.state_digest()}
            consequences = f"{operation} in isolated workspace {self.workspace.root.name}: " + args.get(
                "path", args.get("message", "automatically selected commands")
            )
            authorization_epoch = self.db.setting("policy_epoch", 0)
            key = await self.policies.authorize(
                self.project_id, self.task_id, self.execution_id, action, args, call_id, consequences
            )
            if self.db.setting("policy_epoch", 0) != authorization_epoch:
                raise ValueError("Policy changed before execution; request a fresh authorization")
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
            if operation in {"write", "delete"}:
                original = safe_file(self.workspace.root, args["path"])
                if original.is_file():
                    backup = self.settings.data_dir / "recovery-files" / key
                    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copy2(original, backup)
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
            elif operation in {"command", "test", "install"}:
                commands = args["commands"]
                if not commands:
                    raise ValueError("No command selected; supply argv")
                from .verification_runtime import VerificationRuntime
                from .verification_workspace import prepare_copy, run_checked

                await VerificationRuntime(self.db, self.settings).provision(
                    self.project_id, self.workspace, self.task_id, self.execution_id
                )
                # Retain a pre-command copy for automatic recovery, including uncommitted inputs.
                checkpoint = await asyncio.to_thread(prepare_copy, self.settings.data_dir, key, self.workspace)
                if operation == "test":
                    baseline = git(self.workspace.root, "rev-parse", "HEAD")
                    result = [await run_checked(checkpoint, c["argv"], c["cwd"], baseline) for c in commands]
                    if args.get("argv"):
                        definition = selector.add_definition(
                            self.project_id, args["argv"], args.get("cwd", "."), execution_id=self.execution_id
                        )
                        with self.db.transaction() as conn:
                            row = conn.execute(
                                "SELECT definition FROM verification_commands WHERE id=?", (definition["id"],)
                            ).fetchone()
                            item = json.loads(row["definition"])
                            item["source"] = "worker verification"
                            conn.execute(
                                "UPDATE verification_commands SET definition=? WHERE id=?",
                                (json.dumps(item), definition["id"]),
                            )
                else:
                    result = [
                        await self.workspace.isolated_test(
                            c["argv"], cwd=c["cwd"], writable=True, network=operation == "install", git_writable=True
                        )
                        for c in commands
                    ]
                try:
                    validate_git_admin(self.workspace.root)
                except (ValueError, OSError):
                    # Git commands completed inside isolation; restore damaged metadata automatically
                    # before any host controller Git reads. Retain project edits and the full checkpoint.
                    admin = self.workspace.root / ".git"
                    if admin.is_symlink() or admin.is_file():
                        admin.unlink()
                    elif admin.exists():
                        shutil.rmtree(admin)
                    shutil.copytree(checkpoint.root / ".git", admin, symlinks=False)
                    for record in result:
                        record["exit_code"] = 1
                        record["output"] += "\nUnsafe Git metadata restored automatically from recovery checkpoint."
                        record["git_metadata_recovered"] = True
                for record in result:
                    record["recovery_checkpoint"] = str(checkpoint.root)
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

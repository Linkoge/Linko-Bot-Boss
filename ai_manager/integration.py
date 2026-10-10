"""Verified local integration only. No remote credentials, push, hooks or uncertain replay."""

import asyncio
import configparser
import json
import subprocess
import time
from pathlib import Path

from .capabilities import Capabilities
from .db import new_id
from .policies import Policies
from .registry import git
from .verification_strategies import content_digest
from .workspace import Workspace


class Integrator:
    def __init__(self, db, registry, settings):
        self.db, self.registry, self.settings = db, registry, settings

    @staticmethod
    def validate_source(source, workspace, baseline):
        admin = source / ".git"
        if not admin.is_dir() or admin.is_symlink():
            raise ValueError(
                "Integration needs project-local Git administration; external worktree admin is unavailable"
            )
        config = admin / "config"
        if config.is_symlink() or config.stat().st_size > 100000:
            raise ValueError("Source Git configuration is outside the controlled integration profile")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(config.read_text())
        except configparser.Error as error:
            raise ValueError("Source Git configuration requires explicit integration review") from error
        for section in parser.sections():
            name = section.casefold()
            if name.startswith(("include", "filter ")) or name == "filter":
                raise ValueError(
                    "Source Git includes/filters require a separately reviewed isolated integration profile"
                )
            if name == "core" and any(key in parser[section] for key in ("worktree", "attributesfile")):
                raise ValueError("Source Git configuration references content outside the controlled workspace")
        for name in ("attributes", "exclude"):
            if (admin / "info" / name).is_symlink():
                raise ValueError("Source Git administration contains an escaping reference")
        # Git checkout can overwrite ignored files; protect every incoming untracked path.
        names = filter(None, git(workspace.root, "diff", baseline, "--name-only", "-z").split("\0"))
        for name in names:
            path = source / name
            if (path.exists() or path.is_symlink()) and not git(source, "ls-files", "--error-unmatch", "--", name):
                raise ValueError("Verified merge would overwrite existing untracked/ignored data: " + name)

    async def integrate(self, task, verified_evidence=None):
        policy = self.db.setting(f"integration_policy:{task['project_id']}", {})
        configured_policy = dict(policy)
        if not policy:
            policy = {
                "auto_commit": Policies(self.db).profile(task["project_id"]) in {"high_autonomy", "maximum_autonomy"}
                and Policies(self.db).effective(task["project_id"], "create_commits")["mode"] == "ALLOW",
                "auto_merge": False,
            }
        requirement = json.loads(task.get("verification_spec") or "{}").get("integration", "none")
        if requirement == "merge" and not policy.get("auto_merge"):
            raise ValueError("Task requires merge; owner must authorize an explicit project target branch")
        if not policy.get("auto_commit") and not policy.get("auto_merge") and requirement == "none":
            return {"status": "skipped"}
        worker = self.db.one(
            "SELECT * FROM executions WHERE task_id=? AND worktree IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (task["id"],),
        )
        result = self.db.one(
            "SELECT * FROM verification_results WHERE task_id=? AND passed=1 ORDER BY created_at DESC LIMIT 1",
            (task["id"],),
        )
        provisional = (
            task["status"] == "verifying" and verified_evidence and verified_evidence.get("outcome") == "verified"
        )
        if not worker or not (provisional or task["status"] == "completed" and result):
            raise ValueError("Integration requires independent test/file evidence from the verification controller")
        # Integration receipts are added to the controller's evidence afterwards.
        # Keep the pre-integration proof immutable and avoid a recursive receipt.
        evidence = json.loads(json.dumps(verified_evidence)) if provisional else json.loads(result["evidence"])
        workspace = Workspace(self.settings.data_dir, worker["id"])
        workspace.root, workspace.branch = Path(worker["worktree"]), worker["branch"]
        if workspace.root.is_symlink() or not workspace.root.resolve().is_relative_to(
            (self.settings.data_dir / "workspaces").resolve()
        ):
            raise ValueError("Integration workspace outside boundary")
        if (
            not evidence.get("content_snapshot")
            or content_digest(workspace, worker["base_commit"]) != evidence["content_snapshot"]
        ):
            raise ValueError("Verified content changed; integration requires renewed verification")
        execution_id, operation_id = new_id(), new_id()
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT status,evidence FROM integration_results WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
            if previous and previous["status"] != "reconciled_retry":
                if previous["status"] != "completed":
                    raise ValueError(
                        "Previous integration outcome requires explicit owner reconciliation; it will not be replayed"
                    )
                return {"status": "completed", **json.loads(previous["evidence"])}
            conn.execute(
                "INSERT INTO integration_results VALUES(?,?,?,'started',?,?)",
                (operation_id, task["id"], execution_id, json.dumps({"verified_code": evidence}), time.time()),
            )
            conn.execute(
                "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES(?,?,?,'integration','running',?)",
                (execution_id, task["id"], task["project_id"], time.time()),
            )
        details = {"source_unchanged": True, "verified_code": evidence}
        try:
            authorization_epoch = self.db.setting("policy_epoch", 0)
            dirty = git(workspace.root, "status", "--porcelain")
            if dirty:
                await Policies(self.db).authorize(
                    task["project_id"],
                    None,
                    execution_id,
                    "create_commits",
                    {"snapshot": evidence["content_snapshot"], "workspace": str(workspace.root)},
                    "verified-commit",
                    "Commit independently verified isolated content",
                )
                if content_digest(workspace, worker["base_commit"]) != evidence["content_snapshot"]:
                    raise ValueError("Content changed while waiting for commit approval")
                if (
                    self.db.setting("policy_epoch", 0) != authorization_epoch
                    or self.db.setting(f"integration_policy:{task['project_id']}", {}) != configured_policy
                ):
                    raise ValueError("Integration authorization changed before commit")
                cap = Capabilities(self.db, self.settings, workspace, task["project_id"], task["id"], execution_id)
                details.update(await asyncio.to_thread(cap.commit, "Verified task: " + task["description"][:200]))
            else:
                details["commit"] = git(workspace.root, "rev-parse", "HEAD")
            if requirement in {"commit", "merge"} and details["commit"] == worker["base_commit"]:
                raise ValueError("Required integration has no new verified commit")
            if policy.get("auto_merge"):
                source = self.registry.allowed_path(self.registry.get(task["project_id"])["path"])
                self.validate_source(source, workspace, worker["base_commit"])
                expected = {
                    "source": str(source),
                    "head": worker["base_commit"],
                    "commit": details["commit"],
                    "branch": policy.get("target_branch"),
                }
                await Policies(self.db).authorize(
                    task["project_id"],
                    None,
                    execution_id,
                    "merge_branches",
                    expected,
                    "verified-merge",
                    "Fast-forward verified changes into the explicitly authorized project branch; no push or deployment",
                )
                if (
                    self.db.setting("policy_epoch", 0) != authorization_epoch
                    or self.db.setting(f"integration_policy:{task['project_id']}", {}) != configured_policy
                ):
                    raise ValueError("Integration authorization changed before merge")
                # The source checkout is never reset/stashed or force-updated.
                if not expected["branch"] or git(source, "branch", "--show-current") != expected["branch"]:
                    raise ValueError("Source branch differs from owner integration policy")
                if git(source, "status", "--porcelain") or git(source, "rev-parse", "HEAD") != worker["base_commit"]:
                    raise ValueError("Source changed or has local work; verified merge blocked without modification")
                if content_digest(workspace, worker["base_commit"]) != evidence["content_snapshot"]:
                    raise ValueError("Verified workspace changed before integration")
                env = {
                    "PATH": "/usr/bin:/bin",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_SYSTEM": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                }
                base = [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "submodule.recurse=false",
                    "-c",
                    "fetch.recurseSubmodules=false",
                ]
                await asyncio.to_thread(
                    subprocess.run,
                    [*base, "fetch", "--no-tags", "--no-recurse-submodules", str(workspace.root), details["commit"]],
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                # Recheck after fetch; external source changes cannot be overwritten by ff-only merge.
                if git(source, "status", "--porcelain") or git(source, "rev-parse", "HEAD") != worker["base_commit"]:
                    raise ValueError("Source changed during integration")
                await asyncio.to_thread(
                    subprocess.run,
                    [*base, "merge", "--ff-only", "--no-edit", details["commit"]],
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                details.update(
                    {
                        "source_unchanged": False,
                        "merged_head": git(source, "rev-parse", "HEAD"),
                        "target_branch": expected["branch"],
                    }
                )
            status = "completed"
        except asyncio.CancelledError:
            details["error"] = "Integration interrupted; outcome uncertain, reconcile before explicit retry"
            status = "blocked"
            raise
        except Exception as error:
            details["error"], status = str(error), "blocked"
        finally:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE integration_results SET status=?,evidence=? WHERE id=?",
                    (status, json.dumps(details), operation_id),
                )
                conn.execute(
                    "UPDATE executions SET status=?,output=?,error=?,finished_at=? WHERE id=?",
                    (status, json.dumps(details), details.get("error", ""), time.time(), execution_id),
                )
                if details.get("commit"):
                    conn.execute("UPDATE executions SET commit_ref=? WHERE id=?", (details["commit"], worker["id"]))
                self.db.event(conn, "integration." + status, details, task["project_id"], task["id"], execution_id)
            from .tasks import Tasks

            Tasks(self.db).sync(task["project_id"])
        return {"status": status, **details}

    def reconcile(self, task, revision, note):
        if task["status"] != "blocked" or task["revision"] != revision or not note.strip():
            raise ValueError(
                "Blocked integration requires current task revision and explicit owner reconciliation evidence"
            )
        previous = self.db.one(
            "SELECT * FROM integration_results WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (task["id"],)
        )
        if not previous or previous["status"] not in {"started", "blocked"}:
            raise ValueError("No uncertain or blocked integration to reconcile")
        if self.db.one("SELECT id FROM executions WHERE task_id=? AND finished_at IS NULL", (task["id"],)):
            raise ValueError("Execution is still active; do not reconcile or replay it")
        worker = self.db.one(
            "SELECT * FROM executions WHERE task_id=? AND worktree IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (task["id"],),
        )
        details = json.loads(previous["evidence"])
        evidence = details.get("verified_code")
        if not worker or not evidence or not evidence.get("content_snapshot"):
            raise ValueError("Persisted independent content evidence is missing")
        workspace = Workspace(self.settings.data_dir, worker["id"])
        workspace.root = Path(worker["worktree"])
        if workspace.root.is_symlink() or not workspace.root.resolve().is_relative_to(
            (self.settings.data_dir / "workspaces").resolve()
        ):
            raise ValueError("Workspace outside isolation")
        if content_digest(workspace, worker["base_commit"]) != evidence["content_snapshot"]:
            raise ValueError("Workspace changed; investigate before retry")
        source = self.registry.allowed_path(self.registry.get(task["project_id"])["path"])
        self.validate_source(source, workspace, worker["base_commit"])
        if (
            (source / ".git/index.lock").exists()
            or (workspace.root / ".git/index.lock").exists()
            or git(source, "status", "--porcelain")
        ):
            raise ValueError("Source has local work or a Git operation lock; owner must reconcile it first")
        head, commit = git(source, "rev-parse", "HEAD"), git(workspace.root, "rev-parse", "HEAD")
        if head not in {worker["base_commit"], commit}:
            raise ValueError("Source has diverged; integration cannot safely be replayed")
        policy = self.db.setting(f"integration_policy:{task['project_id']}", {})
        already_merged = bool(
            policy.get("auto_merge")
            and head == commit
            and commit != worker["base_commit"]
            and git(source, "branch", "--show-current") == policy.get("target_branch")
        )
        status = "completed" if already_merged else "reconciled_retry"
        if already_merged:
            details.update({"commit": commit, "merged_head": head, "source_unchanged": False})
        with self.db.transaction() as conn:
            current = conn.execute("SELECT status,revision FROM tasks WHERE id=?", (task["id"],)).fetchone()
            recorded = conn.execute("SELECT status FROM integration_results WHERE id=?", (previous["id"],)).fetchone()
            if (
                current["status"] != "blocked"
                or current["revision"] != revision
                or recorded["status"] != previous["status"]
            ):
                raise ValueError("Integration state changed; refresh before reconciliation")
            conn.execute(
                "UPDATE integration_results SET status=?,evidence=? WHERE id=?",
                (status, json.dumps({**details, "owner_reconciliation": note}), previous["id"]),
            )
            self.db.event(
                conn,
                "integration.owner_reconciled",
                {
                    "id": previous["id"],
                    "previous_status": previous["status"],
                    "status": status,
                    "evidence": note,
                    "source_head": head,
                    "workspace_head": commit,
                },
                task["project_id"],
                task["id"],
            )
        return {
            "result": "accepted",
            "message": "Integration outcome reconciled; independent verification must pass before completion",
            "status": status,
        }

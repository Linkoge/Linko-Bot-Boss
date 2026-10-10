"""Verified local integration only. No remote credentials, push, hooks or uncertain replay."""

import asyncio
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

    async def integrate(self, task):
        policy = self.db.setting(f"integration_policy:{task['project_id']}", {})
        if not policy.get("auto_commit") and not policy.get("auto_merge"):
            return
        worker = self.db.one(
            "SELECT * FROM executions WHERE task_id=? AND worktree IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (task["id"],),
        )
        result = self.db.one(
            "SELECT * FROM verification_results WHERE task_id=? AND passed=1 ORDER BY created_at DESC LIMIT 1",
            (task["id"],),
        )
        if task["status"] != "completed" or not worker or not result:
            raise ValueError("Integration requires independently verified completion")
        evidence = json.loads(result["evidence"])
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
            previous = conn.execute("SELECT status FROM integration_results WHERE task_id=?", (task["id"],)).fetchone()
            if previous:
                return  # Including uncertain started/blocked operations; explicit reconciliation is required.
            conn.execute(
                "INSERT INTO integration_results VALUES(?,?,?,'started',?,?)",
                (operation_id, task["id"], execution_id, "{}", time.time()),
            )
            conn.execute(
                "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES(?,?,?,'integration','running',?)",
                (execution_id, task["id"], task["project_id"], time.time()),
            )
        details = {"source_unchanged": True}
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
                    or self.db.setting(f"integration_policy:{task['project_id']}", {}) != policy
                ):
                    raise ValueError("Integration authorization changed before commit")
                cap = Capabilities(self.db, self.settings, workspace, task["project_id"], task["id"], execution_id)
                details.update(await asyncio.to_thread(cap.commit, "Verified task: " + task["description"][:200]))
            else:
                details["commit"] = git(workspace.root, "rev-parse", "HEAD")
            if policy.get("auto_merge"):
                source = self.registry.allowed_path(self.registry.get(task["project_id"])["path"])
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
                    or self.db.setting(f"integration_policy:{task['project_id']}", {}) != policy
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
                base = ["git", "-C", str(source), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
                await asyncio.to_thread(
                    subprocess.run,
                    [*base, "fetch", "--no-tags", str(workspace.root), details["commit"]],
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

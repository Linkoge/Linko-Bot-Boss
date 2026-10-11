import asyncio
import hashlib
import json
import time

from .db import new_id

DEFAULTS = {
    "execute_commands": "ALLOW",
    "read_files": "ALLOW",
    "edit_files": "ASK",
    "run_tests": "ALLOW",
    "install_dependencies": "ASK",
    "create_commits": "ASK",
    "merge_branches": "ASK",
    "push_remote": "ASK",
    "deploy": "ASK",
    "delete_files": "ASK",
    "modify_infrastructure": "DENY",
    "change_network": "DENY",
    "access_credentials": "DENY",
    "start_services": "ASK",
    "external_communications": "ASK",
    "paid_services": "DENY",
    "create_files": "ASK",
    "launch_workers": "ALLOW",
    "resume_sessions": "ALLOW",
    "create_branches": "ALLOW",
    "access_other_projects": "DENY",
    "external_api_calls": "ASK",
    "destructive_operations": "ASK",
}
SUPPORTED = {
    "read_files",
    "edit_files",
    "create_files",
    "run_tests",
    "create_commits",
    "delete_files",
    "launch_workers",
    "resume_sessions",
    "create_branches",
    "install_dependencies",
    "merge_branches",
    "execute_commands",
    "destructive_operations",
}
# Stable identifiers preserve existing stored policies. New IDs describe formerly implicit gates.
ROUTINE = {
    "read_files",
    "edit_files",
    "create_files",
    "delete_files",
    "run_tests",
    "install_dependencies",
    "launch_workers",
    "resume_sessions",
    "create_branches",
    "create_commits",
    "merge_branches",
    "execute_commands",
}
PROFILES = {
    "legacy": DEFAULTS,
    "conservative": {**DEFAULTS, **{a: "ASK" for a in ROUTINE - {"read_files", "run_tests"}}},
    "high_autonomy": {a: "ALLOW" if a in ROUTINE else "ASK" for a in DEFAULTS},
    "maximum_autonomy": {
        **{a: "ALLOW" if a in ROUTINE else "ASK" for a in DEFAULTS},
        "external_api_calls": "ALLOW",
        "access_other_projects": "ALLOW",
        "start_services": "ALLOW",
    },
}
BOUNDARY = (
    "Routine development is autonomous globally, including existing and new projects. "
    "Commands run in recoverable isolated workspaces without host data or credentials. "
    "Catastrophic actions require exact owner confirmation; host destruction is unavailable."
)


def operation_key(execution_id, call_id, action, arguments):
    return hashlib.sha256(json.dumps([execution_id, call_id, action, arguments], sort_keys=True).encode()).hexdigest()


class Policies:
    def __init__(self, db):
        self.db = db

    def profile(self, project_id=None):
        local = self.db.setting(f"autonomy_profile:{project_id}") if project_id else None
        return local or self.db.setting("autonomy_profile:global", "legacy")

    def effective(self, project_id, action, profile=None):
        if action not in DEFAULTS:
            raise ValueError("Unknown action category")
        local = (
            self.db.one("SELECT mode FROM policies WHERE scope=? AND action=?", (project_id, action))
            if project_id
            else None
        )
        global_policy = self.db.one("SELECT mode FROM policies WHERE scope='global' AND action=?", (action,))
        selected = profile or self.profile(project_id)
        source = "project" if local else "global" if global_policy else "profile:" + selected
        mode = local["mode"] if local else global_policy["mode"] if global_policy else PROFILES[selected][action]
        # One global execution policy supersedes legacy profiles/overrides without deleting them.
        if action in ROUTINE:
            mode, source = "ALLOW", "global:autonomous"
        elif action == "destructive_operations":
            mode, source = "ASK", "global:catastrophic-boundary"
        available = action in SUPPORTED
        return {
            "action": action,
            "mode": mode,
            "source": source,
            "available": available,
            "profile": selected,
            "epoch": self.db.setting("policy_epoch", 0),
            "explanation": f"{mode} from {source}. "
            + (BOUNDARY if available else "No controlled capability; policy cannot enable execution."),
        }

    def proposal(self, profile, project_id=None):
        if profile not in PROFILES and not (profile == "inherit" and project_id):
            raise ValueError("Unknown autonomy profile")
        changes = []
        scopes = (
            [project_id]
            if project_id
            else [None] + [p["id"] for p in self.db.all("SELECT id FROM projects WHERE status!='removed'")]
        )
        for scope in scopes:
            # Explicit project profiles survive a global switch.
            target = self.db.setting(f"autonomy_profile:{scope}") if not project_id and scope else None
            for action in DEFAULTS:
                old, new = (
                    self.effective(scope, action),
                    self.effective(
                        scope,
                        action,
                        target
                        or (self.db.setting("autonomy_profile:global", "legacy") if profile == "inherit" else profile),
                    ),
                )
                if old["mode"] != new["mode"]:
                    changes.append(
                        {
                            "scope": scope or "global",
                            "action": action,
                            "old": old["mode"],
                            "mode": new["mode"],
                            "available": new["available"],
                        }
                    )
        result = {
            "profile": profile,
            "previous": self.profile(project_id),
            "previous_override": self.db.setting("autonomy_profile:" + (project_id or "global")),
            "scope": project_id or "global",
            "epoch": self.db.setting("policy_epoch", 0),
            "changes": changes,
            "boundaries": BOUNDARY,
            "scheduler": "Preserve enabled/scope/budget; profile activation does not start previously paused scheduling.",
            "duration": "Until owner changes or revokes it; explicit overrides remain in effect.",
        }
        result["proposal_hash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result

    def activate(self, profile, project_id, proposal_hash, authorized_by="authenticated_owner"):
        # Called only through authenticated owner routes, never a worker tool.
        with self.db.transaction() as conn:
            proposal = self.proposal(profile, project_id)
            if proposal["proposal_hash"] != proposal_hash:
                raise ValueError("Policy proposal changed; review a fresh consolidated proposal")
            if profile == "inherit":
                conn.execute("DELETE FROM settings WHERE key=?", ("autonomy_profile:" + project_id,))
            else:
                conn.execute(
                    "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("autonomy_profile:" + (project_id or "global"), json.dumps(profile)),
                )
            conn.execute(
                "INSERT INTO settings VALUES('policy_epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(proposal["epoch"] + 1),),
            )
            conn.execute("UPDATE approvals SET status='invalidated' WHERE status IN ('pending','approved')")
            self.db.event(conn, "policy.profile_activated", {**proposal, "authorized_by": authorized_by}, project_id)
        return {
            "result": "accepted",
            "profile": profile,
            "message": "Owner-approved autonomy profile activated",
            "proposal": proposal,
        }

    def history(self):
        return self.db.all("SELECT * FROM events WHERE kind LIKE 'policy.%' ORDER BY seq DESC LIMIT 100")

    def list(self, project_id=None):
        return [self.effective(project_id, action) for action in DEFAULTS]

    def set(self, project_id, action, mode, risk_accepted=False):
        if action not in DEFAULTS or mode not in {"ALLOW", "ASK", "DENY", None}:
            raise ValueError("Invalid action policy")
        if mode == "ALLOW" and action not in SUPPORTED and not risk_accepted:
            raise ValueError("Explicit risk acceptance is required; this action still has no executable capability")
        scope = project_id or "global"
        with self.db.transaction() as conn:
            old = conn.execute("SELECT mode FROM policies WHERE scope=? AND action=?", (scope, action)).fetchone()
            if mode:
                conn.execute(
                    "INSERT INTO policies VALUES(?,?,?) ON CONFLICT(scope,action) DO UPDATE SET mode=excluded.mode",
                    (scope, action, mode),
                )
            else:
                conn.execute("DELETE FROM policies WHERE scope=? AND action=?", (scope, action))
            row = conn.execute("SELECT value FROM settings WHERE key='policy_epoch'").fetchone()
            epoch = (json.loads(row["value"]) if row else 0) + 1
            conn.execute(
                "INSERT INTO settings VALUES('policy_epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(epoch),),
            )
            conn.execute("UPDATE approvals SET status='invalidated' WHERE status IN ('pending','approved')")
            self.db.event(
                conn,
                "policy.changed",
                {
                    "action": action,
                    "old": old["mode"] if old else None,
                    "mode": mode,
                    "scope": scope,
                    "epoch": epoch,
                    "risk_accepted": risk_accepted,
                    "authorized_by": "authenticated_owner",
                },
                project_id,
            )
        return self.effective(project_id, action)

    async def authorize(
        self, project_id, task_id, execution_id, action, arguments, call_id, consequences, resume_status="running"
    ):
        policy = self.effective(project_id, action)
        if not policy["available"]:
            raise ValueError("This action has no controlled execution capability; UI policy cannot enable it")
        if policy["mode"] == "DENY":
            with self.db.transaction() as conn:
                self.db.event(conn, "policy.denied", {"action": action}, project_id, task_id, execution_id)
            raise ValueError("Effective policy denies " + action)
        key = operation_key(execution_id, call_id, action, arguments)
        if policy["mode"] == "ALLOW":
            return key
        now, approval_id = time.time(), new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    key,
                    project_id,
                    task_id,
                    execution_id,
                    action,
                    json.dumps(arguments),
                    "Effective policy requires owner approval",
                    consequences,
                    policy["epoch"],
                    "pending",
                    now,
                    now + 900,
                    None,
                ),
            )
            row = conn.execute("SELECT * FROM approvals WHERE operation_key=?", (key,)).fetchone()
            if row["status"] not in {"pending", "approved"}:
                raise ValueError("This approval was already consumed, rejected or invalidated")
            approval_id = row["id"]
            conn.execute(
                "UPDATE tasks SET status='awaiting_approval',revision=revision+1 WHERE id=? AND lease_owner=?",
                (task_id, execution_id),
            )
            conn.execute("UPDATE executions SET status='awaiting_approval' WHERE id=?", (execution_id,))
            conn.execute(
                "INSERT OR IGNORE INTO notifications VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    new_id(),
                    "approval:" + approval_id,
                    "approval",
                    "Approval needed: " + action,
                    consequences,
                    project_id,
                    task_id,
                    None,
                    now,
                ),
            )
            self.db.event(
                conn,
                "approval.requested",
                {
                    "id": approval_id,
                    "action": action,
                    "affected_path": arguments.get("path"),
                    "consequences": consequences,
                },
                project_id,
                task_id,
                execution_id,
            )
        try:
            while True:
                row = self.db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
                if row["status"] == "approved":
                    with self.db.transaction() as conn:
                        if (
                            self.effective(project_id, action)["mode"] == "DENY"
                            or self.db.setting("policy_epoch", 0) != row["policy_epoch"]
                        ):
                            raise ValueError("Policy changed; approval is stale")
                        cursor = conn.execute(
                            "UPDATE approvals SET status='consumed' WHERE id=? AND status='approved' AND expires_at>?",
                            (approval_id, time.time()),
                        )
                        if cursor.rowcount != 1:
                            raise ValueError("Approval expired or already consumed")
                        self.db.event(conn, "approval.consumed", {"id": approval_id}, project_id, task_id, execution_id)
                    return key
                if row["status"] != "pending":
                    raise ValueError("Operation " + row["status"] + " by owner or policy change")
                if row["expires_at"] < time.time():
                    with self.db.transaction() as conn:
                        conn.execute(
                            "UPDATE approvals SET status='expired' WHERE id=? AND status='pending'", (approval_id,)
                        )
                    raise ValueError("Approval expired; operation did not execute")
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE approvals SET status='cancelled' WHERE id=? AND status IN ('pending','approved')",
                    (approval_id,),
                )
            raise
        finally:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET status=?,revision=revision+1 WHERE id=? AND status='awaiting_approval' AND lease_owner=?",
                    (resume_status, task_id, execution_id),
                )
                conn.execute(
                    "UPDATE executions SET status=? WHERE id=? AND status='awaiting_approval' AND finished_at IS NULL",
                    (resume_status, execution_id),
                )

    def decide(self, approval_id, approve):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row or row["status"] != "pending" or row["expires_at"] <= time.time():
                raise ValueError("Approval is not pending or expired")
            epoch = conn.execute("SELECT value FROM settings WHERE key='policy_epoch'").fetchone()
            if (json.loads(epoch["value"]) if epoch else 0) != row["policy_epoch"]:
                raise ValueError("Approval policy is stale")
            conn.execute(
                "UPDATE approvals SET status=?,decided_at=? WHERE id=?",
                ("approved" if approve else "rejected", time.time(), approval_id),
            )
            self.db.event(
                conn,
                "approval.decided",
                {"id": approval_id, "approved": approve},
                row["project_id"],
                row["task_id"],
                row["execution_id"],
            )

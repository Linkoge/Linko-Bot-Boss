"""Owner administration with explicit persisted scope and durable, idempotent receipts.

This module is never exposed to project worker tools. Local CLI authority is the
existing private Unix-owner administration channel; browser authority uses auth/CSRF.
"""

import hashlib
import json
import os
import time

from .policies import Policies
from .tasks import Tasks
from .verification import VerificationCommands

ACTIONS = {
    "snapshot",
    "discover_verification",
    "propose_command",
    "decide_commands",
    "activate_profile",
    "reconcile_task",
    "retry_verification",
}


class Administration:
    def __init__(self, db, registry, settings, verifier=None):
        self.db, self.registry, self.settings, self.verifier = db, registry, settings, verifier

    def grants(self):
        return [
            json.loads(r["value"])
            for r in self.db.all("SELECT value FROM settings WHERE key LIKE 'owner_admin_grant:%'")
        ]

    def grant(self, grant_id, project_id, actions, authorization, actor):
        if not grant_id or not authorization.strip() or not actions or set(actions) - ACTIONS:
            raise ValueError("Explicit authorization and supported action scope are required")
        if project_id:
            self.registry.get(project_id)
        if not project_id and set(actions) - {"snapshot", "activate_profile"}:
            raise ValueError("Routine administration grants must name a project")
        record = {
            "id": grant_id,
            "project_id": project_id,
            "actions": sorted(set(actions)),
            "authorization": authorization,
            "actor": actor,
            "active": True,
            "profile": "high_autonomy",
            "created_at": time.time(),
            "duration": "until revoked",
        }
        key = "owner_admin_grant:" + grant_id
        with self.db.transaction() as conn:
            old = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if old:
                raise ValueError("Grant already exists; revoke it before proposing a new scope")
            conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(record)))
            self.db.event(conn, "owner_admin.granted", record, project_id)
        return record

    def revoke(self, grant_id, actor):
        key = "owner_admin_grant:" + grant_id
        with self.db.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not row:
                raise ValueError("Unknown administration grant")
            record = json.loads(row["value"])
            record["active"] = False
            conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(record), key))
            self.db.event(conn, "owner_admin.revoked", {"id": grant_id, "actor": actor}, record["project_id"])
        return record

    def execute(self, request, actor, execution_id=None, selected_project=None):
        if not isinstance(request, dict) or set(request) - {"id", "grant_id", "action", "project_id", "arguments"}:
            raise ValueError("Invalid administration request")
        action, project_id = request.get("action"), request.get("project_id")
        if action not in ACTIONS or not request.get("id"):
            raise ValueError("Supported action and stable request ID are required")
        if selected_project and project_id != selected_project:
            raise ValueError("Administration cannot escape selected project context")
        if execution_id:
            execution = self.db.one("SELECT agent_id,finished_at FROM executions WHERE id=?", (execution_id,))
            if not execution or execution["agent_id"] != "manager" or execution["finished_at"]:
                raise ValueError("Administration requires an active owner manager conversation; workers are excluded")
        elif actor == "local_owner":
            if self.db.path.stat().st_uid != os.geteuid():
                raise ValueError("Local administration requires the private database Unix owner")
        elif actor != "authenticated_owner":
            raise ValueError("Owner administration channel is required")
        key = "owner_admin_receipt:" + request["id"]
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.db.transaction() as conn:
            grant = conn.execute(
                "SELECT value FROM settings WHERE key=?", ("owner_admin_grant:" + request.get("grant_id", ""),)
            ).fetchone()
            grant = json.loads(grant["value"]) if grant else None
            if not grant or not grant["active"] or grant["project_id"] != project_id or action not in grant["actions"]:
                self.db.event(
                    conn,
                    "owner_admin.rejected",
                    {"request": request, "actor": actor, "reason": "Missing/revoked/out-of-scope grant"},
                    project_id,
                    execution_id=execution_id,
                )
                # Commit the rejection before returning a durable negative receipt.
                return {"result": "rejected", "reason": "Missing/revoked/out-of-scope owner administration grant"}
            prior = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if prior:
                prior = json.loads(prior["value"])
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("Administration request ID reused for different arguments")
                if prior["status"] == "started":
                    raise ValueError("Previous administration outcome uncertain; inspect receipts, do not replay")
                return prior
            receipt = {
                "id": request["id"],
                "fingerprint": fingerprint,
                "status": "started",
                "request": request,
                "actor": actor,
                "execution_id": execution_id,
                "created_at": time.time(),
            }
            conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(receipt)))
            self.db.event(conn, "owner_admin.started", receipt, project_id, execution_id=execution_id)
        try:
            result = self.apply(action, project_id, request.get("arguments", {}), actor)
            receipt.update(status="completed", result=result)
        except Exception as error:
            receipt.update(status="failed", error=str(error))
        receipt["finished_at"] = time.time()
        with self.db.transaction() as conn:
            conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(receipt), key))
            self.db.event(conn, "owner_admin." + receipt["status"], receipt, project_id, execution_id=execution_id)
        return receipt

    def apply(self, action, project_id, args, actor):
        commands = VerificationCommands(self.db, self.registry)
        if action == "snapshot":
            return {
                "profile": Policies(self.db).profile(project_id),
                "effective": Policies(self.db).list(project_id),
                "verification": commands.snapshot(project_id) if project_id else None,
            }
        if action == "activate_profile":
            if args.get("profile") != "high_autonomy":
                raise ValueError("Current grant authorizes High Autonomy only")
            return Policies(self.db).activate("high_autonomy", project_id, args["proposal_hash"], actor)
        if action == "discover_verification":
            return commands.discover(project_id, args.get("execution_id"))
        if action == "propose_command":
            return commands.add_definition(project_id, **args)
        if action == "decide_commands":
            return commands.decide(project_id, args["decisions"])
        task = Tasks(self.db).get(args["task_id"])
        if task["project_id"] != project_id:
            raise ValueError("Task is outside granted project")
        if action == "retry_verification":
            if not self.verifier:
                from .orchestration import Verifier

                self.verifier = Verifier(self.db, self.registry, self.settings)
            return self.verifier.retry(task["id"], args["revision"])
        if action == "reconcile_task":
            note = args.get("evidence", "").strip()
            if not note or not args.get("disposition") == "obsolete":
                raise ValueError(
                    "Obsolete task reconciliation requires explicit evidence; completion needs verification"
                )
            if task["status"] in {"completed", "cancelled"}:
                return {"task_id": task["id"], "status": task["status"], "history_preserved": True}
            if self.db.one("SELECT id FROM executions WHERE task_id=? AND finished_at IS NULL", (task["id"],)):
                raise ValueError("Task execution is active; reconcile its process first")
            result = Tasks(self.db).transition(task["id"], "cancelled", args["revision"], blocker=task["blocker"])
            with self.db.transaction() as conn:
                self.db.event(
                    conn,
                    "task.owner_reconciled",
                    {
                        "disposition": "obsolete",
                        "evidence": note,
                        "previous_status": task["status"],
                        "previous_blocker": task["blocker"],
                        "actor": actor,
                    },
                    project_id,
                    task["id"],
                )
            return {"task_id": task["id"], "status": result["status"], "evidence": note, "history_preserved": True}
        raise ValueError("Unsupported administration action")

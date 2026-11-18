"""Owner-controlled auto mode: lets a worker-result notice turn start NEW normal tasks, within hardcoded limits.

One global switch, stored once in the `auto_mode` setting (what the UI shows is what is saved and what the gate reads).
Off by default. Only an owner-typed turn may switch it (`set_auto_mode`); followups.py rejects it in notice turns.
While on, a notice turn may `delegate` with followup_trigger 'auto' and a reason. Limits (constants, not settings):
at most DAILY_LIMIT auto tasks per rolling 24 hours across all projects, never two for the same normalized cause, and
after FAILURE_STREAK auto tasks in a row end failed the mode pauses itself and tells the owner once. Every auto task
is a `task.auto_started` event; the morning summary lists them for the owner. See README.md, Automatic follow-ups.
"""

import hashlib
import json
import time

from .followups import cause_key, refuse

TRIGGER = "auto"
DAILY_LIMIT = 10
WINDOW = 24 * 3600
FAILURE_STREAK = 3
KIND = "task.auto_started"
SETTING = "auto_mode"
REPORTED = "auto_reported:"


def one_line(text, limit=160):
    return " ".join((text or "").split())[:limit]


class AutoMode:
    def __init__(self, db, registry=None):
        self.db, self.registry = db, registry

    def stored(self):
        return self.db.setting(SETTING) or {"enabled": False}

    def state(self):
        saved = self.stored()
        used = self.db.one("SELECT count(*) AS n FROM events WHERE kind=? AND created_at>?",
                           (KIND, time.time() - WINDOW))["n"]
        return {"enabled": bool(saved.get("enabled")), "paused": saved.get("paused", ""),
                "used_24h": used, "daily_limit": DAILY_LIMIT}

    def set(self, enabled):
        """Owner-typed turns only (the caller enforces it). Turning on starts a fresh failure streak."""
        if not isinstance(enabled, bool):
            raise ValueError("set_auto_mode needs enabled: true or false")
        seq = self.db.one("SELECT coalesce(max(seq),0) AS n FROM events")["n"]
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (SETTING, json.dumps({"enabled": enabled, "since": seq, "changed_at": time.time()})))
            self.db.event(conn, "auto_mode.changed", {"enabled": enabled})
        return self.state()

    def authorize(self, action, args, selected_project, source):
        """Plan for an auto task from a notice turn, or a refusal. The cap and cause are re-checked in reserve()."""
        if not self.stored().get("enabled"):
            raise refuse("auto mode is off; only the owner can turn it on")
        if not source:
            raise refuse("there is no worker result in this turn")
        if action != "delegate" or args.get("task_id"):
            raise refuse("auto mode only starts new tasks with delegate")
        if args.get("todos"):
            raise refuse("starting To-Dos needs an owner-typed message")
        reason = one_line(args.get("reason"), 600)
        if not reason:
            raise refuse("an auto task needs a reason")
        project_id = args.get("project_id") or selected_project
        project = self.registry.get(project_id) if project_id else None
        if not project or project["status"] != "active":
            raise refuse(f"the project is {project['status'] if project else 'not selected'}")
        if project.get("github_tier") == "offline":
            raise refuse("the project is offline")
        plan = {"auto": True, "trigger": TRIGGER, "cause": cause_key(TRIGGER, reason), "reason": reason,
                "action": action, "parent": source["task_id"], "root": None}
        self.check_limits(None, plan)
        return plan

    def check_limits(self, conn, plan):
        def one(sql, params):
            return conn.execute(sql, params).fetchone() if conn else self.db.one(sql, params)
        used = one("SELECT count(*) AS n FROM events WHERE kind=? AND created_at>?", (KIND, time.time() - WINDOW))
        if used["n"] >= DAILY_LIMIT:
            raise refuse(f"the limit of {DAILY_LIMIT} auto tasks per 24 hours is reached; tell the owner what is open")
        if one("SELECT 1 FROM events WHERE kind=? AND json_extract(history_text(payload),'$.cause')=?",
               (KIND, plan["cause"])):
            raise refuse("an auto task for the same cause already exists; tell the owner instead")

    def reserve(self, conn, plan, task_id, params, objective):
        """Inside the task's own transaction: re-check, record the event and tell the originating chat."""
        if not json.loads((conn.execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
                           or {"value": "{}"})["value"]).get("enabled"):
            raise refuse("auto mode is off; only the owner can turn it on")
        self.check_limits(conn, plan)
        task = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.db.event(conn, KIND, {"cause": plan["cause"], "reason": plan["reason"], "trigger": TRIGGER,
                                   "source_task": plan["parent"], "objective": one_line(objective)},
                      task["project_id"], task_id, params.get("manager_execution_id"))
        conversation = params.get("conversation_id")
        if conversation and conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation,)).fetchone():
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                         "VALUES(?,?,'assistant',?,'completed',?)",
                         ("auto-" + task_id, conversation, f"Auto task started: {one_line(plan['reason'], 120)}",
                          time.time()))
        return 1

    def tasks(self, since=None):
        rows = self.db.all(
            """SELECT e.seq,e.created_at,e.payload,t.id AS task_id,t.project_id,t.state,t.blocker,t.updated_at,
            t.origin_conversation FROM events e JOIN tasks t ON t.id=e.task_id WHERE e.kind=?
            AND e.created_at>? ORDER BY e.seq""", (KIND, since if since is not None else 0))
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            outcome = row["state"] if row["state"] != "failed" else "failed: " + one_line(row["blocker"], 120)
            out.append({"seq": row["seq"], "task_id": row["task_id"], "project_id": row["project_id"],
                        "started_at": row["created_at"], "reason": payload.get("reason", ""), "outcome": outcome,
                        "state": row["state"], "updated_at": row["updated_at"],
                        "conversation": row["origin_conversation"]})
        return out

    @staticmethod
    def brief(task):
        return {k: task[k] for k in ("task_id", "project_id", "reason", "outcome")}

    def summary(self):
        """manager_action auto_summary: every auto task of the last 24 hours plus any not yet reported."""
        recent = time.time() - WINDOW
        unreported = {t["seq"] for t in self.unreported(None)}
        return {**self.state(), "tasks": [self.brief(t) for t in self.tasks()
                                          if t["started_at"] > recent or t["seq"] in unreported]}

    def unreported(self, execution_id):
        reported = {int(r["key"][len(REPORTED):]): json.loads(r["value"]) for r in self.db.all(
            "SELECT key,value FROM settings WHERE key LIKE ?", (REPORTED + "%",))}
        return [t for t in self.tasks() if t["seq"] not in reported or (execution_id and reported[t["seq"]] == execution_id)]

    def morning(self, execution_id):
        """For the first owner-typed turn after auto activity: the tasks to list, marking finished ones reported.
        A rebuilt prompt for the same turn gets the same list; running tasks stay for the next summary."""
        tasks = self.unreported(execution_id)
        if not tasks:
            return None
        with self.db.transaction() as conn:
            for task in tasks:
                if task["state"] in {"done", "failed"}:
                    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                                 (REPORTED + str(task["seq"]), json.dumps(execution_id)))
        return {"instruction": "Auto mode started these tasks since the owner last heard about them. Begin your reply "
                               "with one line per task: its reason and its outcome.",
                "tasks": [self.brief(t) for t in tasks]}

    def check_pause(self):
        """Pause after FAILURE_STREAK auto tasks in a row (since the mode was last turned on) ended failed;
        tells the owner once. Returns True when it paused now."""
        saved = self.stored()
        if not saved.get("enabled"):
            return False
        rows = self.db.all(
            """SELECT t.state,t.origin_conversation FROM events e JOIN tasks t ON t.id=e.task_id
            WHERE e.kind=? AND e.seq>? AND t.state IN ('done','failed') ORDER BY t.updated_at DESC,e.seq DESC LIMIT ?""",
            (KIND, saved.get("since", 0), FAILURE_STREAK))
        if len(rows) < FAILURE_STREAK or any(r["state"] != "failed" for r in rows):
            return False
        text = (f"Auto mode paused itself: the last {FAILURE_STREAK} tasks it started all failed. I will not start "
                "new tasks on my own until you say \"auto mode on\" again.")
        with self.db.transaction() as conn:
            current = conn.execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
            if not current or json.loads(current["value"]) != saved:
                return False  # changed meanwhile
            conn.execute("UPDATE settings SET value=? WHERE key=?",
                         (json.dumps({**saved, "enabled": False, "paused": "failure streak", "changed_at": time.time()}),
                          SETTING))
            self.db.event(conn, "auto_mode.paused", {"reason": "failure streak", "streak": FAILURE_STREAK})
            conversation = rows[0]["origin_conversation"]
            if not conversation or not conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation,)).fetchone():
                row = conn.execute("SELECT id FROM conversations WHERE context='global'").fetchone()
                conversation = row and row["id"]
            if conversation:
                key = hashlib.sha256(f"{saved.get('since', 0)}:{saved.get('changed_at')}".encode()).hexdigest()[:24]
                conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                             "VALUES(?,?,'assistant',?,'completed',?)",
                             ("auto-paused-" + key, conversation, text, time.time()))
        return True

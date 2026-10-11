"""Durable objectives and exclusive project claims. Legacy columns are history only."""

import json
import time

from .db import new_id
from .recovery import gate_available


class Tasks:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def record(row):
        return {**row, "legacy_status": row["status"], "status": row["lifecycle"]}

    def get(self, task_id):
        row = self.db.one(
            "SELECT t.*,e.retry_at,e.recovery_reason FROM tasks t LEFT JOIN executions e ON e.id=t.lease_owner WHERE t.id=?",
            (task_id,),
        )
        if not row:
            raise ValueError("Unknown assignment")
        return self.record(row)

    @staticmethod
    def context(task):
        return {k: task[k] for k in ("id", "description", "status", "blocker")}

    def list(self, project_id=None):
        rows = self.db.all(
            "SELECT t.*,e.retry_at,e.recovery_reason FROM tasks t LEFT JOIN executions e ON e.id=t.lease_owner"
            + (" WHERE t.project_id=?" if project_id else "")
            + " ORDER BY t.created_at DESC",
            (project_id,) if project_id else (),
        )
        archived = self.db.setting("archived_tasks", {})
        return [self.record(row) for row in rows if row["id"] not in archived]

    def archive_before(self, cutoff, preserve=()):
        """Clear old inactive objectives without deleting history or changing dependencies.

        A fixed creation cutoff protects this request and later descendants. Leases
        and unfinished executions are checked under the same write lock as archival.
        """
        with self.db.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='archived_tasks'").fetchone()
            archived = json.loads(row[0]) if row else {}
            candidates = conn.execute("""SELECT * FROM tasks t WHERE created_at<?
                AND lifecycle IN ('waiting','queued','failed','cancelled','completed') AND lease_owner IS NULL
                AND NOT EXISTS(SELECT 1 FROM executions e WHERE e.task_id=t.id AND e.finished_at IS NULL)
                ORDER BY created_at,id""", (cutoff,)).fetchall()
            cleared = []
            for task in candidates:
                if task["id"] in preserve or task["id"] in archived:
                    continue
                archived[task["id"]] = {"at": time.time(), "previous_lifecycle": task["lifecycle"], "cutoff": cutoff}
                # Never leave hidden legacy work eligible for a future dispatch.
                if task["lifecycle"] in {"waiting", "queued", "failed"}:
                    conn.execute("""UPDATE tasks SET lifecycle='cancelled',revision=revision+1,
                        updated_at=? WHERE id=?""", (time.time(), task["id"]))
                self.db.event(conn, "task.archived", archived[task["id"]], task["project_id"], task["id"])
                cleared.append(task["id"])
            conn.execute("""INSERT INTO settings VALUES('archived_tasks',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (json.dumps(archived),))
            return cleared

    def add(self, project_id, description, origin_conversation=None, request_key=None):
        if not description.strip() or len(description) > 24000:
            raise ValueError("Objective must be 1–24000 characters")
        now, task_id = time.time(), new_id()
        with self.db.transaction() as conn:
            if request_key:
                previous = conn.execute(
                    """SELECT id,project_id FROM tasks WHERE request_key=? OR id=(
                        SELECT task_id FROM events WHERE kind='task.reused' AND json_extract(payload,'$.request_key')=?
                        ORDER BY seq DESC LIMIT 1)""",
                    (request_key, request_key),
                ).fetchone()
                if previous:
                    if previous["project_id"] != project_id:
                        raise ValueError("Delegation belongs to another project")
                    return self.get(previous["id"])
            previous = conn.execute(
                """SELECT id FROM tasks WHERE project_id=? AND description=? AND origin_conversation IS ?
                AND lifecycle IN ('queued','running','waiting') AND legacy=0 ORDER BY created_at DESC LIMIT 1""",
                (project_id, description.strip(), origin_conversation),
            ).fetchone()
            if previous:
                if request_key:
                    self.db.event(conn, "task.reused", {"request_key": request_key}, project_id, previous["id"])
                return self.get(previous["id"])
            conn.execute(
                """INSERT INTO tasks(id,project_id,description,acceptance,created_at,updated_at,
                         lifecycle,origin_conversation,request_key,legacy)
                         VALUES(?,?,?,'',?,?,'queued',?,?,0)""",
                (task_id, project_id, description.strip(), now, now, origin_conversation, request_key),
            )
            self.db.event(conn, "task.created", {"objective": description}, project_id, task_id)
        return self.get(task_id)

    def control(self, task_id, action, revision):
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["revision"] != revision:
                raise ValueError("Assignment changed; refresh before editing")
            if task["lifecycle"] == "running" or task["lease_owner"]:
                raise ValueError("Stop the active worker before changing this assignment")
            if task_id in self.db.setting("archived_tasks", {}):
                raise ValueError("Assignment was archived; its history is preserved")
            if task["lifecycle"] == "cancelled":
                raise ValueError("Assignment was cancelled")
            status = {"continue": "queued", "pause": "waiting", "cancel": "cancelled"}.get(action)
            if not status:
                raise ValueError("Unknown assignment action")
            conn.execute(
                "UPDATE tasks SET lifecycle=?,blocker=?,revision=revision+1,updated_at=? WHERE id=?",
                (status, "Paused by user" if action == "pause" else "", time.time(), task_id),
            )
            self.db.event(conn, "task." + action, {}, task["project_id"], task_id)
        return self.get(task_id)

    def claim(self, max_workers):
        with self.db.transaction() as conn:
            if not gate_available(conn):
                return None
            if (
                conn.execute("""SELECT count(*) FROM tasks t LEFT JOIN executions e ON e.id=t.lease_owner
                WHERE t.lease_owner IS NOT NULL AND (e.id IS NULL OR e.status!='recovering')""").fetchone()[0]
                >= max_workers
            ):
                return None
            row = conn.execute("""SELECT t.*,p.path FROM tasks t JOIN projects p ON p.id=t.project_id
                WHERE t.lifecycle='queued' AND p.status='active'
                AND NOT EXISTS(SELECT 1 FROM settings s,json_each(s.value) a
                    WHERE s.key='archived_tasks' AND a.key=t.id)
                AND NOT EXISTS(SELECT 1 FROM tasks busy WHERE busy.project_id=t.project_id AND busy.lease_owner IS NOT NULL)
                ORDER BY p.priority DESC,t.created_at LIMIT 1""").fetchone()
            if not row:
                return None
            execution_id = new_id()
            if not gate_available(conn, execution_id, True):
                return None
            conn.execute(
                "UPDATE tasks SET lifecycle='running',lease_owner=?,revision=revision+1,updated_at=? WHERE id=?",
                (execution_id, time.time(), row["id"]),
            )
            conn.execute(
                """INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,worktree,launch_key)
                            VALUES(?,?,?,'execution','queued',?,?,'not-started')""",
                (
                    execution_id,
                    row["id"],
                    row["project_id"],
                    time.time(),
                    row["path"],
                ),
            )
            self.db.event(conn, "worker.queued", {}, row["project_id"], row["id"], execution_id)
        return self.get(row["id"])

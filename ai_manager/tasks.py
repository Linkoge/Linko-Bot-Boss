import json
import time

from .db import Database, new_id

TRANSITIONS = {
    "pending": {"running", "paused", "blocked", "awaiting_approval", "cancelled"},
    "running": {"verifying", "paused", "blocked", "failed", "awaiting_approval"},
    "verifying": {"completed", "failed", "blocked", "paused"},
    "paused": {"pending", "cancelled"},
    "blocked": {"pending", "cancelled"},
    "awaiting_approval": {"pending", "blocked", "cancelled"},
    "failed": {"pending", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class Tasks:
    def __init__(self, db: Database):
        self.db = db

    def add(self, project_id, description, acceptance, priority=50, dependencies=(), verification="", permissions=()):
        if not description.strip() or not acceptance.strip():
            raise ValueError("Tasks need description and acceptance criteria")
        task_id, now = new_id(), time.time()
        with self.db.transaction() as conn:
            for dep in dependencies:
                row = conn.execute("SELECT project_id FROM tasks WHERE id=?", (dep,)).fetchone()
                if not row or row["project_id"] != project_id:
                    raise ValueError("Dependency must exist in the same project")
            conn.execute(
                "INSERT INTO tasks(id,project_id,description,priority,acceptance,verification,permissions,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    project_id,
                    description,
                    priority,
                    acceptance,
                    verification,
                    json.dumps(permissions),
                    now,
                    now,
                ),
            )
            conn.executemany("INSERT INTO dependencies VALUES(?,?)", [(task_id, d) for d in dependencies])
            self.db.event(conn, "task.created", {"description": description}, project_id, task_id)
        return self.get(task_id)

    def get(self, task_id):
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise ValueError("Unknown task")
        return row

    def list(self, project_id=None):
        return self.db.all(
            "SELECT * FROM tasks"
            + (" WHERE project_id=?" if project_id else "")
            + " ORDER BY priority DESC,created_at",
            (project_id,) if project_id else (),
        )

    def transition(self, task_id, status, revision, evidence=None, blocker=""):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["revision"] != revision:
                raise ValueError("Task changed; refresh before editing")
            if status not in TRANSITIONS[row["status"]]:
                raise ValueError(f"Cannot transition {row['status']} to {status}")
            if status == "completed" and not evidence:
                raise ValueError("Completion requires independent verification evidence")
            conn.execute(
                "UPDATE tasks SET status=?,blocker=?,revision=revision+1,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE id=?",
                (status, blocker, time.time(), task_id),
            )
            self.db.event(
                conn,
                "task.transition",
                {"from": row["status"], "to": status, "evidence": evidence, "blocker": blocker},
                row["project_id"],
                task_id,
            )
        return self.get(task_id)

    def claim(self, owner, max_workers=2, lease_seconds=90):
        with self.db.transaction() as conn:
            if (
                conn.execute(
                    "SELECT count(*) FROM tasks WHERE status IN ('running','verifying','awaiting_approval')"
                ).fetchone()[0]
                >= max_workers
            ):
                return None
            row = conn.execute("""SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id
                WHERE t.status='pending' AND p.status='active'
                AND NOT EXISTS(SELECT 1 FROM dependencies d JOIN tasks prerequisite ON prerequisite.id=d.depends_on
                    WHERE d.task_id=t.id AND prerequisite.status!='completed')
                AND NOT EXISTS(SELECT 1 FROM tasks active WHERE active.project_id=t.project_id AND active.status IN ('running','verifying','awaiting_approval'))
                ORDER BY p.priority DESC,t.priority DESC,t.created_at LIMIT 1""").fetchone()
            if not row:
                return None
            now = time.time()
            conn.execute(
                "UPDATE tasks SET status='running',lease_owner=?,lease_until=?,revision=revision+1,updated_at=? WHERE id=?",
                (owner, now + lease_seconds, now, row["id"]),
            )
            self.db.event(conn, "task.claimed", {"owner": owner}, row["project_id"], row["id"])
            return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, task_id, owner, lease_seconds=90):
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET lease_until=? WHERE id=? AND lease_owner=? AND status IN ('running','verifying','awaiting_approval')",
                (time.time() + lease_seconds, task_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("Task lease lost")

    def recover_expired(self):
        # Uncertain outcomes block instead of replaying operations.
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('running','verifying','awaiting_approval') AND lease_until < ?",
                (time.time(),),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE tasks SET status='blocked',blocker='Lease expired; reconcile process and workspace before retry',revision=revision+1 WHERE id=?",
                    (row["id"],),
                )
                self.db.event(conn, "task.recovery_required", {}, row["project_id"], row["id"])
            return len(rows)

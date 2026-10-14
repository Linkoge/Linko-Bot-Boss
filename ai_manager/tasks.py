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
        selected = self.db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected' ORDER BY seq DESC LIMIT 1", (task_id,))
        return {**self.record(row), "worker_route": json.loads(selected['payload']) if selected else None,
                "job_followup": self.db.setting("assignment_workflow:" + task_id),
                "execution_policy": self.db.setting("assignment_policy:" + task_id, {})}

    @staticmethod
    def context(task):
        return {k: task[k] for k in ("id", "description", "acceptance", "status", "blocker", "lease_owner", "revision")} | {"worker_route": task.get("worker_route"), "job_followup": task.get("job_followup"), "execution_policy": task.get("execution_policy"),
                "workspace": task.get("workspace"), "workspace_branch": task.get("workspace_branch")}

    def list(self, project_id=None):
        rows = self.db.all(
            "SELECT t.*,e.retry_at,e.recovery_reason FROM tasks t LEFT JOIN executions e ON e.id=t.lease_owner"
            + (" WHERE t.project_id=?" if project_id else "")
            + " ORDER BY CASE t.lifecycle WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'waiting' THEN 2 ELSE 3 END,t.created_at DESC,t.id",
            (project_id,) if project_id else (),
        )
        archived = self.db.setting("archived_tasks", {})
        return [{**self.record(row), "execution_policy": self.db.setting("assignment_policy:" + row["id"], {}),
                 "job_followup": self.db.setting("assignment_workflow:" + row["id"])}
                for row in rows if row["id"] not in archived]

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

    def add(self, project_id, description, origin_conversation=None, request_key=None, acceptance="", selected=None, model_route=None, todos=None, execution_policy=None):
        if not description.strip() or len(description) > 24000:
            raise ValueError("Objective must be 1–24000 characters")
        if not isinstance(acceptance, str) or len(acceptance) > 12000:
            raise ValueError("Acceptance criteria must be at most 12000 characters")
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
                    if todos:
                        from .todos import Todos
                        Todos(self.db).consume(conn, todos, project_id, previous["id"])
                    return self.get(previous["id"])
            previous = conn.execute(
                """SELECT id FROM tasks WHERE project_id=? AND description=? AND origin_conversation IS ?
                AND lifecycle IN ('queued','running','waiting','paused','pause_requested','cancel_requested') AND legacy=0 ORDER BY created_at DESC LIMIT 1""",
                (project_id, description.strip(), origin_conversation),
            ).fetchone()
            if previous:
                if request_key:
                    self.db.event(conn, "task.reused", {"request_key": request_key}, project_id, previous["id"])
                if todos:
                    from .todos import Todos
                    Todos(self.db).consume(conn, todos, project_id, previous["id"])
                return self.get(previous["id"])
            conn.execute(
                """INSERT INTO tasks(id,project_id,description,acceptance,created_at,updated_at,
                         lifecycle,origin_conversation,request_key,legacy)
                         VALUES(?,?,?,?,?,?,'queued',?,?,0)""",
                (task_id, project_id, description.strip(), acceptance, now, now, origin_conversation, request_key),
            )
            if execution_policy:
                from .parallel import save
                save(conn, "assignment_policy:" + task_id, execution_policy)
            self.db.event(conn, "task.created", {"objective": description}, project_id, task_id)
            from .assignment_workflow import update
            update(self.db, conn, task_id, "actionable", "Authorized assignment; worker outcome requires manager review")
            if model_route:
                self.db.event(conn, "task.model_selected", model_route, project_id, task_id)
            if selected:
                self.db.event(conn, "task.context", selected, project_id, task_id)
            if todos:
                from .todos import Todos
                Todos(self.db).consume(conn, todos, project_id, task_id)
        return self.get(task_id)

    def control(self, task_id, action, revision):
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["revision"] != revision:
                raise ValueError("Assignment changed; refresh before editing")
            if task_id in self.db.setting("archived_tasks", {}):
                raise ValueError("Assignment was archived; its history is preserved")
            if task["lifecycle"] == "cancelled":
                raise ValueError("Assignment was cancelled")
            from .assignment_workflow import get
            workflow = get(conn, task_id)
            if task["lifecycle"] == "completed" and action in {"pause", "cancel"} and (workflow or {}).get('state') not in {'actionable', 'review'}:
                raise ValueError("Completed assignment has no unfinished work to stop")
            if action not in {"continue", "pause", "cancel"}:
                raise ValueError("Unknown assignment action")
            if action in {'pause', 'cancel'}:
                from .deployment_intent import revoke
                revoke(self.db, conn, 'Assignment '+action+' requested', task_id=task_id)
            if task["lease_owner"]:
                if action == "continue":
                    raise ValueError("Wait for confirmed worker cleanup before resuming")
                execution = conn.execute("SELECT * FROM executions WHERE id=?", (task["lease_owner"],)).fetchone()
                if not execution or execution["finished_at"] is not None:
                    raise ValueError("Worker cleanup is still pending")
                if execution["stop_requested"] == 3:
                    raise ValueError("Cancellation is already requested")
                conn.execute("UPDATE questions SET status='expired' WHERE execution_id=? AND status='pending'", (execution["id"],))
                status = action + "_requested"
                conn.execute("UPDATE executions SET stop_requested=?,retry_at=NULL WHERE id=?",
                             (2 if action == "pause" else 3, execution["id"]))
                blocker = "Pause requested; waiting for checkpoint and process cleanup" if action == "pause" else "Cancel requested; waiting for process termination"
            else:
                status = {"continue": "queued", "pause": "paused", "cancel": "cancelled"}[action]
                blocker = "Paused by user" if action == "pause" else ""
            from .parallel import load, release_integration, reserve_integration
            p = load(conn, 'assignment_policy:' + task_id, {})
            if action == 'continue' and p.get('integration'):
                reserve_integration(self.db, conn, task, [v['task_id'] for v in p['sources']])
            conn.execute(
                "UPDATE tasks SET lifecycle=?,blocker=?,revision=revision+1,updated_at=? WHERE id=?",
                (status, blocker, time.time(), task_id),
            )
            from .assignment_workflow import update
            update(self.db, conn, task_id, 'actionable' if action == 'continue' else 'owner_wait',
                   'Explicit assignment ' + action, reset_retry=action == 'continue')
            release_integration(conn, task_id)
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
            from .parallel import admission, prepare_workspace
            rows = conn.execute("""SELECT t.*,p.path FROM tasks t JOIN projects p ON p.id=t.project_id
                WHERE t.lifecycle='queued' AND p.status='active'
                AND NOT EXISTS(SELECT 1 FROM settings s,json_each(s.value) a
                    WHERE s.key='archived_tasks' AND a.key=t.id)
                ORDER BY p.priority DESC,t.created_at,t.id""").fetchall()
            row = None
            for candidate in rows:
                reason = admission(conn, candidate)
                if not reason:
                    try:
                        candidate = prepare_workspace(self.db, conn, candidate)
                    except (ValueError, OSError) as error:
                        reason = str(error)[:2000]
                if reason:
                    if candidate['blocker'] != reason:
                        conn.execute("UPDATE tasks SET blocker=? WHERE id=?", (reason, candidate['id']))
                        self.db.event(conn, "worker.queue_reason", {"reason": reason}, candidate['project_id'], candidate['id'])
                    continue
                row = candidate
                break
            if row is None:
                return None
            execution_id = new_id()
            if not gate_available(conn, execution_id, True):
                return None
            conn.execute(
                "UPDATE tasks SET lifecycle='running',blocker='',lease_owner=?,revision=revision+1,updated_at=? WHERE id=?",
                (execution_id, time.time(), row["id"]),
            )
            conn.execute(
                """INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,worktree,branch,launch_key)
                            VALUES(?,?,?,'execution','queued',?,?,?,'not-started')""",
                (
                    execution_id,
                    row["id"],
                    row["project_id"],
                    time.time(),
                    row["workspace"] or row["path"],
                    row["workspace_branch"],
                ),
            )
            self.db.event(conn, "worker.queued", {}, row["project_id"], row["id"], execution_id)
        return self.get(row["id"])

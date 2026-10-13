"""Deferred project ideas. These records never enter the execution queue."""

import json
import time

from .db import new_id

DEFERRED_ASSIGNMENT = "2b15765038784009b8f6ad8e0e7008ea"
DEFERRED_TITLE = "Keep project memory concise; archive detailed history"
DEFERRED_NOTE = """Recorded at the owner's request on 2026-10-12. Deferred / not scheduled. This is a backlog note only, not an executable assignment; do not automatically implement, investigate, dispatch follow-up development or deploy. Resume only when the owner explicitly schedules it.

Future objective: keep continually growing Markdown memory/context concise for manager reading, covering core purpose, functions, current decisions and current state. Summarize or archive historical change logs and detailed evidence separately, with retrievable references that preserve audit history. Preserve existing authoritative documents and unrelated records.

When scheduled, evaluate architectural options and retention/summarization rules before choosing an approach. Also consider the owner's suggestion of a future to-do list capability, first checking what the existing backlog/task facilities already support; no new list, architecture or capability is authorized now."""


def migrate_deferred(conn):
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (DEFERRED_ASSIGNMENT,)).fetchone()
    if task and not conn.execute("SELECT 1 FROM todos WHERE source_task_id=?", (DEFERRED_ASSIGNMENT,)).fetchone():
        entry_id = "deferred-" + DEFERRED_ASSIGNMENT
        conn.execute(
            """INSERT INTO todos(id,project_id,title,details,source_task_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                entry_id,
                task["project_id"],
                DEFERRED_TITLE,
                DEFERRED_NOTE + "\n\nOriginal recording assignment (preserved):\n" + task["description"],
                task["id"],
                task["created_at"],
                task["updated_at"],
            ),
        )
        # Original assignment, history and evidence remain byte-for-byte intact.
        conn.execute(
            """INSERT INTO events(kind,project_id,task_id,payload,created_at)
            VALUES('todo.migrated',?,?,?,?)""",
            (
                task["project_id"],
                task["id"],
                '{"todo_id":"' + entry_id + '","source":"TASKS.md deferred backlog"}',
                time.time(),
            ),
        )


class Todos:
    def __init__(self, db):
        self.db = db

    def list(self, project_id=None):
        return self.db.all(
            "SELECT * FROM todos WHERE archived_at IS NULL"
            + (" AND project_id=?" if project_id else "")
            + " ORDER BY created_at,id",
            (project_id,) if project_id else (),
        )

    @staticmethod
    def validate(title, details):
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
            raise ValueError("To-Do title must be 1–200 characters on one line")
        if not isinstance(details, str) or len(details) > 24000:
            raise ValueError("To-Do details must be at most 24000 characters")
        return title.strip(), details

    def save(self, project_id, title, details="", entry_id=None, revision=None):
        title, details = self.validate(title, details)
        now = time.time()
        with self.db.transaction() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=? AND status!='removed'", (project_id,)).fetchone():
                raise ValueError("Select an available project for this To-Do")
            previous = None
            updating = bool(entry_id)
            if entry_id:
                previous = conn.execute(
                    "SELECT * FROM todos WHERE id=? AND project_id=?", (entry_id, project_id)
                ).fetchone()
                changed = conn.execute(
                    """UPDATE todos SET title=?,details=?,revision=revision+1,updated_at=?
                    WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL""",
                    (title, details, now, entry_id, project_id, revision),
                )
                if changed.rowcount != 1:
                    raise ValueError("To-Do changed or belongs to another project; refresh before editing")
            else:
                entry_id = new_id()
                conn.execute(
                    "INSERT INTO todos(id,project_id,title,details,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (entry_id, project_id, title, details, now, now),
                )
            self.db.event(
                conn,
                "todo.updated" if updating else "todo.created",
                {
                    "todo_id": entry_id,
                    "title": title,
                    "details": details,
                    "previous": dict(previous) if previous else None,
                },
                project_id,
            )
        return self.db.one("SELECT * FROM todos WHERE id=?", (entry_id,))

    def archive(self, selected, project_id=None):
        if not selected or len(selected) > 200 or len({e["id"] for e in selected}) != len(selected):
            raise ValueError("Select 1–200 distinct To-Do entries")
        with self.db.transaction() as conn:
            for entry in selected:
                row = conn.execute(
                    "SELECT * FROM todos WHERE id=? AND revision=? AND archived_at IS NULL",
                    (entry["id"], entry["revision"]),
                ).fetchone()
                if not row or (project_id and row["project_id"] != project_id):
                    raise ValueError("Selection changed or belongs to another project; refresh and select again")
                conn.execute(
                    "UPDATE todos SET archived_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (time.time(), time.time(), row["id"]),
                )
                self.db.event(conn, "todo.archived", {"todo_id": row["id"]}, row["project_id"])
        return {"archived": len(selected)}

    def consume(self, conn, selected, project_id, task_id, reason="Explicit owner scheduling"):
        """Archive exact source revisions only inside the accepting task transaction."""
        if not selected or len(selected) > 200 or len({e["id"] for e in selected}) != len(selected):
            raise ValueError("Select 1–200 distinct To-Do entries")
        task = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["project_id"] != project_id:
            raise ValueError("To-Do handoff needs an accepted assignment in the same project")
        for entry in selected:
            key = "todo_handoff:" + entry["id"]
            prior = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if prior:
                handoff = json.loads(prior["value"])
                if handoff["task_id"] == task_id and handoff["source"]["revision"] == entry["revision"]:
                    continue
                raise ValueError("To-Do already started in another assignment; continue that assignment")
            row = conn.execute("SELECT * FROM todos WHERE id=? AND revision=? AND archived_at IS NULL",
                               (entry["id"], entry["revision"])).fetchone()
            if not row or row["project_id"] != project_id:
                raise ValueError("To-Do changed or belongs to another project; refresh before starting")
            record = {"task_id": task_id, "source": dict(row), "reason": reason, "at": time.time()}
            conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(record)))
            conn.execute("UPDATE todos SET archived_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                         (record["at"], record["at"], row["id"]))
            self.db.event(conn, "todo.consumed", record, project_id, task_id)

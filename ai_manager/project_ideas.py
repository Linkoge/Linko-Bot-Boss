"""Global notes for future projects. Saving a note never schedules work."""

import time

from .db import new_id


class ProjectIdeas:
    def __init__(self, db):
        self.db = db

    def list(self):
        return self.db.all("SELECT * FROM project_ideas ORDER BY created_at,id")

    def save(self, title, details, entry_id=None):
        if not isinstance(details, str) or not details.strip() or len(details) > 24000:
            raise ValueError("Project idea text must be 1–24000 characters")
        if not isinstance(title, str) or len(title) > 200:
            raise ValueError("Project idea title must be at most 200 characters on one line")
        title = title.strip()
        if any(ord(c) < 32 for c in title):
            raise ValueError("Project idea title must be at most 200 characters on one line")
        if not title:
            title = " ".join(next(line for line in details.splitlines() if line.strip()).split())[:80].rstrip()
        now = time.time()
        with self.db.transaction() as conn:
            if entry_id:
                if not conn.execute("SELECT 1 FROM project_ideas WHERE id=?", (entry_id,)).fetchone():
                    raise ValueError("Project idea not found; refresh the list")
                conn.execute("UPDATE project_ideas SET title=?,details=?,updated_at=? WHERE id=?",
                             (title, details, now, entry_id))
            else:
                entry_id = new_id()
                conn.execute("INSERT INTO project_ideas VALUES(?,?,?,?,?)", (entry_id, title, details, now, now))
        return self.db.one("SELECT * FROM project_ideas WHERE id=?", (entry_id,))

    def delete(self, entry_id):
        with self.db.transaction() as conn:
            if not conn.execute("DELETE FROM project_ideas WHERE id=?", (entry_id,)).rowcount:
                raise ValueError("Project idea not found; refresh the list")
        return {"deleted": entry_id}

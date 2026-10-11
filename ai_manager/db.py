import json
import shutil
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, repository TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', objective TEXT NOT NULL DEFAULT '', success_criteria TEXT NOT NULL DEFAULT '',
 instructions TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 50,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','archived','removed')),
 agent_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), description TEXT NOT NULL,
 acceptance TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 blocker TEXT NOT NULL DEFAULT '', lease_owner TEXT, revision INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS executions(
 id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), project_id TEXT REFERENCES projects(id),
 agent_id TEXT NOT NULL, thread_id TEXT, turn_id TEXT, pid INTEGER, process_start TEXT,
 worktree TEXT, branch TEXT, commit_ref TEXT, model TEXT, effort TEXT,
 status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 started_at REAL NOT NULL, finished_at REAL, latency_ms REAL, usage TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS memory(
 project_id TEXT REFERENCES projects(id), name TEXT NOT NULL, content TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, PRIMARY KEY(project_id,name));
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, project_id TEXT, task_id TEXT,
 execution_id TEXT, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(
 id TEXT PRIMARY KEY, context TEXT NOT NULL UNIQUE, project_id TEXT REFERENCES projects(id),
 thread_id TEXT, summary TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL,
 content TEXT NOT NULL, status TEXT NOT NULL, execution_id TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_execution ON events(execution_id, seq);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,created_at);
CREATE TABLE IF NOT EXISTS chat_requests(
 owner_id INTEGER NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
 receipt TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(owner_id,request_id));
CREATE TABLE IF NOT EXISTS todos(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL,
 details TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 source_task_id TEXT UNIQUE REFERENCES tasks(id), created_at REAL NOT NULL, updated_at REAL NOT NULL,
 archived_at REAL);
CREATE INDEX IF NOT EXISTS todos_project ON todos(project_id,archived_at);
CREATE TABLE IF NOT EXISTS chat_archives(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 title TEXT NOT NULL, archived_at REAL NOT NULL);
"""


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
            for name, definition in (("display_name", "TEXT"), ("last_used_at", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
            if not conn.execute("SELECT 1 FROM schema_version WHERE version=4").fetchone():
                conn.execute("""UPDATE projects SET last_used_at=COALESCE(
                    (SELECT MAX(created_at) FROM events WHERE events.project_id=projects.id
                     AND kind IN ('chat.queued','task.created','task.claimed','worker.queued','plan.applied','manager.action')),0)""")
                conn.execute("INSERT INTO schema_version VALUES(4)")
            # Global context has no project row. Seed its durable recency once from
            # accepted global submissions without altering project history.
            if not conn.execute("SELECT 1 FROM settings WHERE key='global_last_used_at'").fetchone():
                conn.execute("""INSERT INTO settings(key,value)
                    SELECT 'global_last_used_at', CAST(COALESCE(MAX(created_at),0) AS TEXT)
                    FROM events WHERE project_id IS NULL AND kind='chat.queued'""")
            task_columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
            # Old supervision records remain historical. Only lifecycle drives new execution.
            for name, definition in (
                ("lifecycle", "TEXT NOT NULL DEFAULT 'waiting'"),
                ("origin_conversation", "TEXT"),
                ("request_key", "TEXT"),
                ("native_thread", "TEXT"),
                ("legacy", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in task_columns:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            conn.executescript("""
                CREATE UNIQUE INDEX IF NOT EXISTS task_request ON tasks(request_key) WHERE request_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS questions(
                    id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES executions(id),
                    request TEXT NOT NULL, kind TEXT NOT NULL, answer TEXT,
                    status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL);
            """)
            execution_columns = {r[1] for r in conn.execute("PRAGMA table_info(executions)")}
            for name, definition in (
                ("delivery", "TEXT"),
                ("delivery_execution", "TEXT"),
                ("conversation_id", "TEXT"),
                ("input", "TEXT NOT NULL DEFAULT ''"),
                ("retry_at", "REAL"),
                ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
                ("recovery_reason", "TEXT NOT NULL DEFAULT ''"),
                ("mode", "TEXT NOT NULL DEFAULT 'auto'"),
                ("launch_input", "TEXT"),
                ("launch_key", "TEXT"),
                ("stop_requested", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in execution_columns:
                    conn.execute(f"ALTER TABLE executions ADD COLUMN {name} {definition}")
            if not conn.execute("SELECT 1 FROM schema_version WHERE version=7").fetchone():
                conn.execute(
                    "UPDATE tasks SET lifecycle=CASE WHEN status IN ('completed','cancelled','failed') THEN status ELSE 'waiting' END"
                )
                conn.execute("INSERT INTO schema_version VALUES(7)")
            if not conn.execute("SELECT 1 FROM schema_version WHERE version=8").fetchone():
                conn.execute(
                    "UPDATE executions SET delivery='pending' WHERE delivery='waiting' AND finished_at IS NOT NULL"
                )
                conn.execute("INSERT INTO schema_version VALUES(8)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS project_worker ON tasks(project_id) WHERE lifecycle='running'"
            )
            if not conn.execute("SELECT 1 FROM schema_version WHERE version=9").fetchone():
                conn.execute("""UPDATE executions SET
                    mode=COALESCE((SELECT json_extract(payload,'$.prompt.mode') FROM events
                        WHERE execution_id=executions.id AND kind='model.context' ORDER BY seq DESC LIMIT 1),'auto'),
                    launch_input=(SELECT json_extract(payload,'$.prompt') FROM events
                        WHERE execution_id=executions.id AND kind='model.context' ORDER BY seq DESC LIMIT 1)
                    WHERE finished_at IS NULL""")
                conn.execute("INSERT INTO schema_version VALUES(9)")
            conn.execute("INSERT OR IGNORE INTO schema_version VALUES(10)")
            if not conn.execute("SELECT 1 FROM schema_version WHERE version=11").fetchone():
                from .todos import migrate_deferred
                migrate_deferred(conn)
                conn.execute("INSERT INTO schema_version VALUES(11)")
            conn.commit()
        self.path.chmod(0o600)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, sql: str, params=()) -> list[dict]:
        with closing(self.connect()) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def event(conn, kind: str, payload: dict, project_id=None, task_id=None, execution_id=None):
        if project_id and kind in {
            "chat.queued",
            "task.created",
            "worker.queued",
        }:
            conn.execute("UPDATE projects SET last_used_at=MAX(last_used_at,?) WHERE id=?", (time.time(), project_id))
        elif project_id is None and kind == "chat.queued":
            conn.execute(
                """INSERT INTO settings(key,value) VALUES('global_last_used_at',?)
                ON CONFLICT(key) DO UPDATE SET value=CAST(MAX(CAST(value AS REAL),CAST(excluded.value AS REAL)) AS TEXT)""",
                (json.dumps(time.time()),),
            )
        conn.execute(
            "INSERT INTO events(kind,project_id,task_id,execution_id,payload,created_at) VALUES(?,?,?,?,?,?)",
            (kind, project_id, task_id, execution_id, json.dumps(payload), time.time()),
        )

    def setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.event(conn, "settings.changed", {"key": key})

    def backup(self, destination: Path):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError("Backup must use a new destination")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(destination.parent).free < self.path.stat().st_size * 2 + 128 * 1024 * 1024:
            raise ValueError("Insufficient storage for verified backup and 128MB reserve")
        destination.touch(mode=0o600, exist_ok=False)
        try:
            with closing(self.connect()) as source, closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                if target.execute("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError("Backup foreign key check failed")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def restore(source: Path, destination: Path):
        if destination.exists() or Path(str(destination) + "-wal").exists():
            raise ValueError("Restore only to a fresh path while service is stopped")
        with closing(sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)) as original:
            if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Invalid backup")
            if original.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Backup references missing records")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.touch(mode=0o600, exist_ok=False)
            with closing(sqlite3.connect(destination)) as target:
                original.backup(target)

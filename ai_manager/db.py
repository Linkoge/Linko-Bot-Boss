import json
import shutil
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, repository TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', objective TEXT NOT NULL DEFAULT '', success_criteria TEXT NOT NULL DEFAULT '',
 instructions TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 50,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','archived','removed')),
 agent_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), description TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 50, acceptance TEXT NOT NULL, verification TEXT NOT NULL DEFAULT '',
 permissions TEXT NOT NULL DEFAULT '[]', agent_id TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','paused','blocked','awaiting_approval','verifying','failed','completed','cancelled')),
 blocker TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until REAL, revision INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS dependencies(task_id TEXT REFERENCES tasks(id), depends_on TEXT REFERENCES tasks(id),
 PRIMARY KEY(task_id,depends_on), CHECK(task_id != depends_on));
CREATE TABLE IF NOT EXISTS executions(
 id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), project_id TEXT REFERENCES projects(id),
 agent_id TEXT NOT NULL, thread_id TEXT, turn_id TEXT, pid INTEGER, process_start TEXT,
 worktree TEXT, branch TEXT, commit_ref TEXT, model TEXT, effort TEXT,
 status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 started_at REAL NOT NULL, finished_at REAL, latency_ms REAL, usage TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS checkpoints(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT REFERENCES tasks(id),
 commit_ref TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL);
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
CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(status, priority, created_at);
CREATE INDEX IF NOT EXISTS events_execution ON events(execution_id, seq);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,created_at);
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id), goal TEXT NOT NULL, status TEXT NOT NULL,
 proposal TEXT NOT NULL DEFAULT '', execution_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS verification_results(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 execution_id TEXT NOT NULL, passed INTEGER NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL);
INSERT OR IGNORE INTO schema_version VALUES(2);
CREATE TABLE IF NOT EXISTS policies(scope TEXT NOT NULL,action TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('ALLOW','ASK','DENY')),PRIMARY KEY(scope,action));
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL,
 project_id TEXT NOT NULL, task_id TEXT, execution_id TEXT NOT NULL, action TEXT NOT NULL,
 arguments TEXT NOT NULL, reason TEXT NOT NULL, consequences TEXT NOT NULL, policy_epoch INTEGER NOT NULL,
 status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, decided_at REAL);
CREATE TABLE IF NOT EXISTS operations(operation_key TEXT PRIMARY KEY,execution_id TEXT NOT NULL,
 action TEXT NOT NULL,state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,dedupe_key TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,project_id TEXT,task_id TEXT,
 read_at REAL,created_at REAL NOT NULL);
INSERT OR IGNORE INTO schema_version VALUES(3);
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
            if "base_commit" not in {r[1] for r in conn.execute("PRAGMA table_info(executions)")}:
                conn.execute("ALTER TABLE executions ADD COLUMN base_commit TEXT")
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

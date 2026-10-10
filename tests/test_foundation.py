import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks


@pytest.fixture
def foundation(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    repo = root / "sample"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "PROJECT.md").write_text("# Existing owner documentation\n")
    db = Database(tmp_path / "data" / "manager.sqlite3")
    registry = Registry(db, (root,))
    return db, registry, repo


def test_discovery_memory_and_removal_preserve_sources(foundation):
    db, registry, repo = foundation
    project = registry.discover()[0]
    assert registry.discover()[0]["id"] == project["id"]
    original = (repo / "PROJECT.md").read_text()
    memory = next(m for m in registry.memory(project["id"]) if m["name"] == "PROJECT.md")
    registry.update_memory(project["id"], "PROJECT.md", "# Updated context", memory["revision"])
    assert (repo / "PROJECT.md").read_text() == original
    assert (db.path.parent / "memory" / project["id"] / "PROJECT.md").read_text() == "# Updated context"
    registry.update(project["id"], 0, {"status": "removed"})
    assert registry.discover() == []
    assert repo.exists()


def test_revision_conflicts_and_scope(foundation, tmp_path):
    db, registry, repo = foundation
    project = registry.add(repo)
    registry.update(project["id"], 0, {"objective": "Ship"})
    with pytest.raises(ValueError, match="changed"):
        registry.update(project["id"], 0, {"objective": "Overwrite"})
    with pytest.raises(ValueError, match="root"):
        registry.add(tmp_path)
    assert registry.reconcile(project["id"])["docs"] == ["PROJECT.md"]
    with pytest.raises(ValueError, match="evidence"):
        registry.checkpoint(project["id"], "Done", [])


def test_claim_is_atomic_dependencies_and_verification(foundation):
    db, registry, repo = foundation
    project = registry.add(repo)
    tasks = Tasks(db)
    first = tasks.add(project["id"], "Implement", "Tests pass")
    second = tasks.add(project["id"], "Review", "Independent evidence", dependencies=[first["id"]], priority=99)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(tasks.claim, [f"worker-{i}" for i in range(6)]))
    assert sum(c is not None for c in claims) == 1
    running = next(c for c in claims if c)
    assert running["id"] == first["id"]
    with pytest.raises(ValueError):
        tasks.transition(first["id"], "completed", running["revision"], evidence=["Agent said done"])
    verifying = tasks.transition(first["id"], "verifying", running["revision"])
    with pytest.raises(ValueError, match="evidence"):
        tasks.transition(first["id"], "completed", verifying["revision"])
    tasks.transition(first["id"], "completed", verifying["revision"], evidence={"test": "independent suite passed"})
    assert tasks.claim("reviewer")["id"] == second["id"]


def test_restart_and_backup_restore(foundation, tmp_path):
    db, registry, repo = foundation
    project = registry.add(repo)
    tasks = Tasks(db)
    task = tasks.add(project["id"], "Uncertain operation", "Review outcome")
    tasks.claim("worker", lease_seconds=-1)
    reopened = Database(db.path)
    assert Tasks(reopened).recover_expired() == 1
    assert Tasks(reopened).get(task["id"])["status"] == "blocked"
    backup = tmp_path / "backup.sqlite3"
    reopened.backup(backup)
    target = tmp_path / "restored" / "manager.sqlite3"
    Database.restore(backup, target)
    assert Database(target).one("SELECT id FROM projects")["id"] == project["id"]
    with pytest.raises(ValueError):
        Database.restore(backup, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.one("PRAGMA journal_mode")["journal_mode"] == "wal"


def test_display_names_identity_duplicates_and_restart(foundation):
    db, registry, repo = foundation
    project = registry.add(repo)
    from ai_manager.chat import Chat
    from ai_manager.config import Settings

    chat = Chat(db, registry, Settings(data_dir=db.path.parent, project_roots=registry.roots))
    conversation = chat.conversation(project["id"])
    tasks = Tasks(db)
    task = tasks.add(project["id"], "Keep identity", "Identity unchanged")
    before = registry.get(project["id"])
    renamed = registry.update(project["id"], before["revision"], {"display_name": " Bot Manager "})
    assert renamed["display_name"] == "Bot Manager"
    for key in ("id", "name", "path", "repository", "agent_id", "last_used_at"):
        assert renamed[key] == before[key]
    assert chat.conversation(project["id"])["id"] == conversation["id"]
    assert tasks.get(task["id"])["project_id"] == project["id"]
    assert (db.path.parent / "memory" / project["id"] / "STATE.md").exists()
    other = repo.parent / "other"
    other.mkdir()
    duplicate = registry.add(other, "bot manager")
    assert duplicate["display_name"] == "bot manager"
    restarted = Registry(Database(db.path), registry.roots)
    assert restarted.get(project["id"])["display_name"] == "Bot Manager"
    assert restarted.get(project["id"])["last_used_at"] == before["last_used_at"]
    assert restarted.discover()[0]["id"] in {project["id"], duplicate["id"]}
    for value in ("x" * 81, "bad\x00name", "bad\nname"):
        with pytest.raises(ValueError, match="Display name"):
            restarted.update(project["id"], renamed["revision"], {"display_name": value})
    fallback = restarted.update(project["id"], renamed["revision"], {"display_name": ""})
    assert fallback["display_name"] is None and fallback["name"] == repo.name


def test_recency_only_meaningful_interactions_and_restart(foundation):
    db, registry, repo = foundation
    first = registry.add(repo)
    other = repo.parent / "another"
    other.mkdir()
    second = registry.add(other)
    assert first["last_used_at"] == second["last_used_at"] == 0
    Tasks(db).add(first["id"], "Assigned task", "Check")
    timestamp = registry.get(first["id"])["last_used_at"]
    registry.get(second["id"])
    registry.memory(second["id"])
    registry.reconcile(second["id"])
    registry.update(second["id"], 0, {"display_name": "AAA"})
    assert registry.list()[0]["id"] == first["id"]
    assert registry.get(second["id"])["last_used_at"] == 0
    for kind in ("chat.queued", "goal.submitted", "manager.action", "task.claimed", "worker.queued"):
        with db.transaction() as conn:
            db.event(conn, kind, {}, second["id"])
        assert registry.list()[0]["id"] == second["id"]
    with db.transaction() as conn:
        db.event(conn, "chat.queued", {}, None)
    assert len(registry.list()) == 2
    restarted = Registry(Database(db.path), registry.roots)
    assert restarted.list()[0]["id"] == second["id"]
    assert restarted.get(first["id"])["last_used_at"] == timestamp
    backup = db.path.parent / "backup.sqlite3"
    db.backup(backup)
    Database.restore(backup, db.path.parent / "restored.sqlite3")
    restored = Registry(Database(db.path.parent / "restored.sqlite3"), registry.roots)
    assert restored.list() == restarted.list()


def test_navigation_migration_preserves_legacy_project_and_history(tmp_path):
    from ai_manager.db import SCHEMA

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('stable','legacy',?,'agent',1,1)",
            (str(tmp_path / "legacy"),),
        )
        conn.execute("INSERT INTO events(kind,project_id,payload,created_at) VALUES('chat.queued','stable','{}',123)")
        conn.execute(
            "INSERT INTO events(kind,project_id,payload,created_at) VALUES('project.reconciled','stable','{}',999)"
        )
        conn.execute(
            "INSERT INTO conversations(id,context,project_id,thread_id,created_at) VALUES('conversation','stable','stable','thread',1)"
        )
    db = Database(path)
    assert db.one("SELECT MAX(version) AS version FROM schema_version")["version"] == 5
    project = db.one("SELECT * FROM projects")
    assert project["id"] == "stable" and project["name"] == "legacy" and project["last_used_at"] == 123
    assert project["display_name"] is None
    assert db.one("SELECT thread_id FROM conversations")["thread_id"] == "thread"
    assert Database(path).one("SELECT last_used_at FROM projects")["last_used_at"] == 123

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

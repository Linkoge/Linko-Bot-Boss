from ai_manager.db import Database
from ai_manager.registry import Registry


def test_global_continuous_defaults_persist_for_future_projects(tmp_path):
    path = tmp_path / "data" / "manager.sqlite3"
    db = Database(path)
    assert db.setting("scheduler_enabled") is True
    assert db.setting("scheduler_scope") is None
    assert db.setting("scheduler_batch") == {"limit": None, "task_ids": []}
    root = tmp_path / "projects"
    root.mkdir()
    registry = Registry(db, (root,))
    for name in ("first", "added-later"):
        repo = root / name
        repo.mkdir()
        assert registry.add(repo)["status"] == "active"
        db = Database(path)
        assert db.setting("scheduler_enabled") is True
        assert db.setting("scheduler_scope") is None
    assert db.setting("max_workers") is None  # Existing Settings default remains authoritative.


def test_reopening_preserves_explicit_scheduler_configuration(tmp_path):
    path = tmp_path / "manager.sqlite3"
    db = Database(path)
    values = {
        "scheduler_enabled": False,
        "scheduler_scope": "owner-selected-project",
        "scheduler_batch": {"limit": 3, "task_ids": ["retained-task"]},
        "max_workers": 4,
    }
    for key, value in values.items():
        db.set_setting(key, value)
    reopened = Database(path)
    for key, value in values.items():
        assert reopened.setting(key) == value

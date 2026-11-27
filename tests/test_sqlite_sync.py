"""SQLite durability: every commit is fsynced; only disposable test data opts out (tests/conftest.py)."""


def test_sqlite_fsync_is_full_unless_disposable_test_data_opts_out(tmp_path, monkeypatch):
    from ai_manager.db import SYNCHRONOUS_ENV, Database, synchronous

    db = Database(tmp_path / "manager.sqlite3")
    monkeypatch.delenv(SYNCHRONOUS_ENV, raising=False)
    assert synchronous() == "FULL" and db.connect().execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    monkeypatch.setenv(SYNCHRONOUS_ENV, "normal")  # anything but OFF keeps every commit durable
    assert synchronous() == "FULL"
    monkeypatch.setenv(SYNCHRONOUS_ENV, "off")
    assert db.connect().execute("PRAGMA synchronous").fetchone()[0] == 0  # OFF

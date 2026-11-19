import importlib.util
import os
import stat
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("secrets_backup", ROOT / "scripts/secrets_backup.py")
secrets_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secrets_backup)

PASSPHRASE = "correct horse battery staple"
# Key-shaped values are built at runtime so no literal matches the secret scanners (AGENTS.md rule 10).
FAKE_TOKEN = "ghp_" + "Q" * 36
FAKE_ENV = "AI_MANAGER_SESSION=" + "Z" * 40 + "\n"


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A disposable home with two secret files, a scratch TMPDIR and a backup target directory."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    onboarding = home / ".config/linko-onboarding.env"
    manager = home / ".config/linko-bot-manager.env"
    onboarding.write_text(f"TOKEN={FAKE_TOKEN}\n")
    manager.write_text(FAKE_ENV)
    os.chmod(onboarding, 0o644)
    os.chmod(manager, 0o640)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv(secrets_backup.PASSPHRASE_ENV, PASSPHRASE)
    monkeypatch.delenv("AI_MANAGER_ONBOARDING_CONFIG", raising=False)
    return {"home": home, "onboarding": onboarding, "manager": manager, "scratch": scratch,
            "target": tmp_path / "repo-backup"}


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def leftovers(scratch):
    return [p.name for p in scratch.iterdir()]


def test_round_trip_keeps_content_and_mode_600(layout):
    target, count = secrets_backup.backup(layout["target"])
    assert count == 2
    assert target == layout["target"] / "secrets-backup.age"
    assert mode(target) == 0o600

    layout["onboarding"].unlink()
    layout["manager"].unlink()
    secrets_backup.restore(target)

    assert layout["onboarding"].read_text() == f"TOKEN={FAKE_TOKEN}\n"
    assert layout["manager"].read_text() == FAKE_ENV
    assert mode(layout["onboarding"]) == 0o600
    assert mode(layout["manager"]) == 0o600


def test_restore_on_fresh_home_maps_paths_under_new_home(layout, monkeypatch, tmp_path):
    target, _ = secrets_backup.backup(layout["target"])
    fresh = tmp_path / "fresh-vm-home"
    fresh.mkdir()
    monkeypatch.setenv("HOME", str(fresh))

    secrets_backup.restore(target)

    assert (fresh / ".config/linko-onboarding.env").read_text() == f"TOKEN={FAKE_TOKEN}\n"
    assert mode(fresh / ".config/linko-onboarding.env") == 0o600


def test_encrypted_output_contains_no_plaintext(layout):
    target, _ = secrets_backup.backup(layout["target"])
    blob = target.read_bytes()
    assert FAKE_TOKEN.encode() not in blob
    assert b"AI_MANAGER_SESSION" not in blob
    assert b"TOKEN=" not in blob
    assert blob.startswith(secrets_backup.MAGIC)
    assert sorted(p.name for p in layout["target"].iterdir()) == ["secrets-backup.age"]


def test_wrong_passphrase_fails_cleanly_and_touches_nothing(layout, monkeypatch, capsys):
    target, _ = secrets_backup.backup(layout["target"])
    layout["onboarding"].write_text("LOCAL=keep\n")
    monkeypatch.setenv(secrets_backup.PASSPHRASE_ENV, "not the passphrase at all")

    assert secrets_backup.main(["restore", str(target)]) == 1

    err = capsys.readouterr().err
    assert "wrong passphrase" in err
    assert "not the passphrase at all" not in err and PASSPHRASE not in err
    assert layout["onboarding"].read_text() == "LOCAL=keep\n"
    assert not (layout["onboarding"].with_name("linko-onboarding.env.bak")).exists()
    assert layout["manager"].read_text() == FAKE_ENV
    assert leftovers(layout["scratch"]) == []


def test_existing_file_gets_bak_before_overwrite(layout):
    target, _ = secrets_backup.backup(layout["target"])
    layout["onboarding"].write_text("OLD=value\n")

    done = secrets_backup.restore(target)

    bak = layout["onboarding"].with_name("linko-onboarding.env.bak")
    assert bak.read_text() == "OLD=value\n"
    assert mode(bak) == 0o600
    assert layout["onboarding"].read_text() == f"TOKEN={FAKE_TOKEN}\n"
    assert mode(layout["onboarding"]) == 0o600
    assert (str(layout["onboarding"]), str(bak)) in done


def test_second_restore_never_replaces_an_earlier_bak(layout):
    target, _ = secrets_backup.backup(layout["target"])
    layout["onboarding"].write_text("FIRST=one\n")
    secrets_backup.restore(target)
    layout["onboarding"].write_text("SECOND=two\n")

    secrets_backup.restore(target)

    assert layout["onboarding"].with_name("linko-onboarding.env.bak").read_text() == "FIRST=one\n"
    assert layout["onboarding"].with_name("linko-onboarding.env.bak.1").read_text() == "SECOND=two\n"


def test_plaintext_temp_directory_is_removed_after_backup_and_restore(layout):
    target, _ = secrets_backup.backup(layout["target"])
    assert leftovers(layout["scratch"]) == []

    secrets_backup.restore(target)
    assert leftovers(layout["scratch"]) == []


def test_plaintext_temp_directory_is_removed_when_restore_fails(layout, monkeypatch):
    target, _ = secrets_backup.backup(layout["target"])
    monkeypatch.setenv(secrets_backup.PASSPHRASE_ENV, "wrong wrong wrong")

    with pytest.raises(secrets_backup.BackupError):
        secrets_backup.restore(target)
    assert leftovers(layout["scratch"]) == []


def test_plaintext_temp_directory_is_removed_when_backup_fails(layout):
    layout["manager"].unlink()
    with pytest.raises(secrets_backup.BackupError):
        secrets_backup.backup(layout["target"], extra=[str(layout["home"] / "missing.env")])
    assert leftovers(layout["scratch"]) == []
    assert not (layout["target"] / "secrets-backup.age").exists()


def test_config_file_and_flags_extend_the_default_list(layout, tmp_path):
    extra = layout["home"] / ".config/extra-service.env"
    extra.write_text("EXTRA=1\n")
    config = tmp_path / "list.txt"
    config.write_text(f"# instance secrets\n{extra}\n\n")
    flagged = layout["home"] / "flagged.key"
    flagged.write_text("FLAG=1\n")

    target, count = secrets_backup.backup(layout["target"], extra=[str(flagged)], config=str(config))

    assert count == 4
    layout["onboarding"].unlink()
    layout["manager"].unlink()
    extra.unlink()
    flagged.unlink()
    secrets_backup.restore(target)
    assert extra.read_text() == "EXTRA=1\n"
    assert flagged.read_text() == "FLAG=1\n"


def test_missing_explicit_file_is_an_error_and_writes_nothing(layout):
    with pytest.raises(secrets_backup.BackupError, match="not found"):
        secrets_backup.backup(layout["target"], extra=[str(layout["home"] / "absent.env")])
    assert not layout["target"].exists() or not any(layout["target"].iterdir())


def test_missing_default_files_are_skipped(layout, capsys):
    layout["manager"].unlink()
    target, count = secrets_backup.backup(layout["target"])
    assert count == 1
    assert "skipped" in capsys.readouterr().err
    assert target.is_file()


def test_dry_run_changes_nothing(layout):
    target, _ = secrets_backup.backup(layout["target"])
    layout["onboarding"].write_text("KEEP=1\n")

    result = secrets_backup.restore(target, dry_run=True)

    assert layout["onboarding"].read_text() == "KEEP=1\n"
    assert not layout["onboarding"].with_name("linko-onboarding.env.bak").exists()
    assert (str(layout["onboarding"]), str(layout["onboarding"].with_name("linko-onboarding.env.bak"))) in result
    assert len(result) == 2


def test_missing_passphrase_is_refused_without_a_terminal(layout, monkeypatch, capsys):
    monkeypatch.delenv(secrets_backup.PASSPHRASE_ENV)
    monkeypatch.setattr(secrets_backup.sys, "stdin", type("Pipe", (), {"isatty": lambda self: False})())

    assert secrets_backup.main(["backup", str(layout["target"])]) == 1
    assert "no passphrase" in capsys.readouterr().err
    assert not (layout["target"] / "secrets-backup.age").exists()


def test_short_passphrase_is_refused_on_backup(layout, monkeypatch, capsys):
    monkeypatch.setenv(secrets_backup.PASSPHRASE_ENV, "short")

    assert secrets_backup.main(["backup", str(layout["target"])]) == 1
    assert "at least" in capsys.readouterr().err

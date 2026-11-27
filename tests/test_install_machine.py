import os
import pytest
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs/INSTALL.md").read_text()
README = (ROOT / "README.md").read_text()
HOST = "box.example.ts.net"


def stubs(directory):
    directory.mkdir()
    body = {
        "tailscale": f"""#!/bin/sh
[ "$1" = status ] && echo '{{"BackendState":"Running","Self":{{"DNSName":"{HOST}."}}}}'
exit 0
""",
    }
    for name in ("git", "python3", "uv", "claude", "tailscale"):
        path = directory / name
        path.write_text(body.get(name, "#!/bin/sh\nexit 0\n"))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run(tmp_path, *args, path=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bins = tmp_path / "bin"
    if not bins.exists():
        stubs(bins)
    env = {"HOME": str(home), "PATH": path if path is not None else str(bins), "USER": "tester"}
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/install_machine.py"), *args],
        env=env, capture_output=True, text=True, timeout=60,
    ), home


def test_dry_run_touches_nothing_and_prints_address(tmp_path):
    result, home = run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(home.iterdir()) == []
    assert f"https://{HOST}:8787/" in result.stdout
    assert "tailscale serve --bg --https=8787 http://127.0.0.1:8787" in result.stdout
    assert "funnel" not in result.stdout.lower().replace("never funnel", "")
    assert "Machines > Add machine" in result.stdout
    assert not (ROOT / ".git/install-machine-marker").exists()


def test_human_step_stops_with_exit_2(tmp_path):
    (tmp_path / "empty").mkdir()
    result, _ = run(tmp_path, "--dry-run", path=str(tmp_path / "empty"))
    assert result.returncode == 2 and "HUMAN STEP:" in result.stdout


def test_readme_links_to_guide_and_no_duplicate_guide():
    head = README[: README.index("## How work runs")]
    assert "Install on a new machine (for AI agents)" in head
    assert "(docs/INSTALL.md)" in head and "install_machine.py" in head
    assert sorted(p.name for p in (ROOT / "docs").glob("*INSTALL*")) == ["INSTALL.md"]


def test_guide_commands_and_paths_match_real_scripts():
    quick = GUIDE[GUIDE.index("## Quick path for agents"): GUIDE.index("## 1. Install prerequisites")]
    for script in set(re.findall(r"scripts/([a-z_]+\.py)", quick)):
        assert (ROOT / "scripts" / script).is_file(), script
    helper = (ROOT / "scripts/install_machine.py").read_text()
    for token in ("--dry-run", "--hostname", "--port", "--username", "--https=", "playwright"):
        assert token in helper and token in quick, token
    for token in ("install_service.py", "setup-owner", "--generate-password", "linko-bot-manager.env"):
        assert token in helper, token
    assert "tailscale serve --bg --https=8787 http://127.0.0.1:8787" in quick
    assert "funnel" in quick.lower() and "never use `tailscale funnel`" in GUIDE
    assert quick.count("HUMAN STEP") >= 4 and quick.count("Verify") >= 6
    assert "menu > Machines > Add machine" in quick
    assert "linko-bot-manager.service" in quick and (ROOT / "deploy/linko-bot-manager.service.in").is_file()
    cli = (ROOT / "ai_manager/cli.py").read_text()
    assert "--generate-password" in cli and "/api/auth-status" in (ROOT / "ai_manager/web.py").read_text()


def test_claude_only_passes_and_no_provider_stops(tmp_path):
    result, _ = run(tmp_path, "--dry-run")  # stubs hold claude and no codex
    assert result.returncode == 0 and "provider CLI" in result.stdout
    only = tmp_path / "only"
    stubs(only)
    (only / "claude").unlink()
    result, _ = run(tmp_path, "--dry-run", path=str(only))
    assert result.returncode == 2 and "at least one provider CLI" in result.stdout


def test_serve_prints_sudo_command_and_guide_agrees(tmp_path):
    result, _ = run(tmp_path, "--dry-run")
    assert "sudo tailscale serve --bg --https=8787 http://127.0.0.1:8787" in result.stdout
    quick = GUIDE[GUIDE.index("## Quick path for agents"): GUIDE.index("## 1. Install prerequisites")]
    assert "Claude Code or Codex" in quick and "Acquire::Retries" in quick
    assert "sudo tailscale serve" in quick and "install_worker_user.py install" in quick
    assert quick.index("install_worker_user.py") < quick.index("install_repair.py install") < quick.index("Verify the service")
    assert "change it after first login" in quick


# ---- --secrets: restore the encrypted credentials before the service starts ----
import argparse
import importlib.util
import pwd

PASS = "correct horse battery staple"
FAKE_KEY = "xai-" + "K" * 40  # built at runtime (AGENTS.md rule 10)


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backup_file(tmp_path, monkeypatch):
    sb = load("secrets_backup")
    src = tmp_path / "src"
    (src / ".config").mkdir(parents=True)
    (src / ".claude").mkdir()
    (src / ".config/linko-bot-manager.env").write_text(
        f"XAI_API_KEY={FAKE_KEY}\nAI_MANAGER_ALLOWED_HOSTS=localhost,old.example.ts.net\n")
    (src / ".claude/.credentials.json").write_text('{"t": "' + "C" * 30 + '"}')
    (src / ".config/linko-bot-manager.env").chmod(0o644)
    monkeypatch.setenv("HOME", str(src))
    monkeypatch.setenv(sb.PASSPHRASE_ENV, PASS)
    out = tmp_path / "repo"
    sb.backup(out)
    return out  # a directory holding secrets-backup.age


def installer(tmp_path, monkeypatch, secrets, dry=False):
    im = load("install_machine")
    home = tmp_path / "newhome"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    args = argparse.Namespace(dry_run=dry, port=8787, username="owner", hostname=HOST, secrets=str(secrets))
    return im, im.Installer(args, home, dict(os.environ)), home


def test_secrets_restore_mode_600_owner_and_new_host(tmp_path, monkeypatch, backup_file, capsys):
    sb = load("secrets_backup")
    monkeypatch.setenv(sb.PASSPHRASE_ENV, PASS)
    monkeypatch.setattr(sb.os, "geteuid", lambda: 0)
    chowned = []
    monkeypatch.setattr(sb.os, "chown", lambda path, uid, gid: chowned.append((Path(path).name, uid, gid)))
    calls = []
    real = subprocess.run
    def fake_run(command, *a, **k):
        if command and command[0] == "openssl" or str(command[0]).endswith("openssl"):
            return real(command, *a, **k)
        calls.append((command, k))
        return real(["true"])
    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    im, inst, home = installer(tmp_path, monkeypatch, backup_file)
    monkeypatch.setattr(im.secrets_backup, "restore", sb.restore)
    inst.restore_secrets()
    inst.hostname = HOST
    inst.allow_new_host()
    env_file = home / ".config/linko-bot-manager.env"
    login = home / ".claude/.credentials.json"
    for path in (env_file, login):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert FAKE_KEY in env_file.read_text()
    assert HOST in env_file.read_text().split("ALLOWED_HOSTS=")[1].split("\n")[0].split(",")
    assert ("linko-bot-manager.env.restore-tmp", pwd.getpwuid(os.getuid()).pw_uid, os.getgid()) in chowned
    # logins are placed for linko-worker/root through sudo with the body on stdin, never in argv
    for args, kwargs in calls:  # replicate_for only runs when linko-worker exists
        assert "C" * 30 not in " ".join(map(str, args)) and FAKE_KEY not in " ".join(map(str, args))
    assert PASS not in capsys.readouterr().out


def test_restored_env_file_never_carries_the_autopilot_lock(tmp_path, monkeypatch, backup_file, capsys):
    im, inst, home = installer(tmp_path, monkeypatch, backup_file)
    inst.env_file.parent.mkdir(parents=True, exist_ok=True)
    inst.env_file.write_text("XAI_API_KEY=x\nAI_MANAGER_AUTOPILOT=full\nAI_MANAGER_SNAPSHOT_CMD=ssh hv snap\n"
                             f"AI_MANAGER_ALLOWED_HOSTS=localhost,{HOST}\n")
    inst.allow_new_host()
    assert inst.env_file.read_text() == f"XAI_API_KEY=x\nAI_MANAGER_ALLOWED_HOSTS=localhost,{HOST}\n"
    assert "dropped the Autopilot lines" in capsys.readouterr().out


def test_secrets_dry_run_writes_nothing(tmp_path, monkeypatch, backup_file, capsys):
    im, inst, home = installer(tmp_path, monkeypatch, backup_file, dry=True)
    inst.restore_secrets()
    assert list(home.iterdir()) == []
    assert "would restore" in capsys.readouterr().out


def test_wrong_passphrase_stops_cleanly(tmp_path, monkeypatch, backup_file, capsys):
    sb = load("secrets_backup")
    monkeypatch.setenv(sb.PASSPHRASE_ENV, "a different passphrase")
    im, inst, home = installer(tmp_path, monkeypatch, backup_file)
    monkeypatch.setenv(sb.PASSPHRASE_ENV, "a different passphrase")
    with pytest.raises(im.Stop) as stop:
        inst.restore_secrets()
    assert stop.value.code == 1 and list(home.iterdir()) == []
    err = capsys.readouterr()
    assert "wrong passphrase" in err.err and "a different passphrase" not in err.out + err.err


def test_cli_wrong_passphrase_exit_1_and_passphrase_not_in_argv_or_output(tmp_path, backup_file):
    home = tmp_path / "h2"
    home.mkdir()
    bins = tmp_path / "bin"
    stubs(bins)
    env = {"HOME": str(home), "PATH": str(bins) + ":/usr/bin:/bin", "LINKO_SECRETS_PASSPHRASE": "wrong passphrase 123"}
    argv = [sys.executable, str(ROOT / "scripts/install_machine.py"), "--dry-run", "--secrets", str(backup_file)]
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and "wrong passphrase" in result.stderr
    assert "wrong passphrase 123" not in " ".join(argv) + result.stdout + result.stderr
    assert not any(home.iterdir())


def test_passphrase_removed_from_child_environment(tmp_path, monkeypatch, backup_file):
    im, inst, _ = installer(tmp_path, monkeypatch, backup_file)
    assert "LINKO_SECRETS_PASSPHRASE" not in inst.env


def test_secrets_option_has_no_value_for_passphrase():
    text = (ROOT / "scripts/install_machine.py").read_text()
    assert "--passphrase" not in text


def test_backup_covers_logins_and_tokens_and_guide_lists_human_steps():
    sb = load("secrets_backup")
    listed = set(sb.DEFAULT_FILES) | set(sb.OPTIONAL_FILES)
    for needle in (".claude/.credentials.json", ".codex/auth.json", "tailscale-authkey", "github-token", "deploy-key"):
        assert any(needle in p for p in listed)
    for words in ("--secrets", "NOT portable", "device approval", "LINKO_SECRETS_PASSPHRASE"):
        assert words in GUIDE


def test_no_key_shaped_literals_in_repo_scripts_and_docs():
    pattern = re.compile(r"(ghp_|xai-|sk-ant-|-----BEGIN)[A-Za-z0-9_-]{16,}")
    for path in [*(ROOT / "scripts").glob("*.py"), ROOT / "docs/INSTALL.md", Path(__file__)]:
        assert not pattern.search(path.read_text()), path

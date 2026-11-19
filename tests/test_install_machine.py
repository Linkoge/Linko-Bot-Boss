import os
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

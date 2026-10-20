"""Workers can reproduce delivery checks from another project's isolated worktree."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_core import git, make_checked_repo, run_next
from test_core import world as world_fixture

from ai_manager.core import run_gate, run_secret_gate, self_check_command
from ai_manager.fence import main_branch

world = world_fixture


@pytest.mark.parametrize("failure", [None, "tests", "secrets", "inherited"])
def test_absolute_command_reuses_gate_and_scanner_for_other_project(tmp_path, monkeypatch, failure):
    shared, base = make_checked_repo(tmp_path)
    venv = shared / ".venv" / "bin"
    (shared / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    root = tmp_path / "worker's worktree"
    git(shared, "worktree", "add", "-qb", "task", str(root))
    (root / "tests" / "test_environment.py").write_text(
        "import os, sys\nfrom pathlib import Path\n\n"
        "def test_environment():\n"
        "    assert os.environ['PYTHONPATH'] == str(Path.cwd())\n"
        "    assert os.environ['AI_MANAGER_SECURE_COOKIE'] == '0'\n"
        "    assert Path(os.environ['AI_MANAGER_DATA']).name.startswith('linko-gate-')\n"
        f"    assert sys.executable == {str(venv / 'python')!r}\n"
    )  # untracked test files are included, just as in the application gate
    if failure == "tests":
        (root / "tests" / "test_smoke.py").write_text(
            "import pytest\n@pytest.mark.smoke\ndef test_broken():\n    assert False\n")
    if failure == "secrets":
        (root / "temporary.txt").write_text("ghp_" + "A" * 36 + "\n")
        git(root, "add", "temporary.txt")
        git(root, "commit", "-qm", "synthetic finding")
        git(root, "rm", "temporary.txt")
        git(root, "commit", "-qm", "removed later")
    if failure == "inherited":
        (shared / "existing.txt").write_text("ghp_" + "A" * 36 + "\n")
        git(shared, "add", "existing.txt")
        git(shared, "commit", "-qm", "upstream addition")
        git(root, "merge", "--no-edit", "main")
    data = tmp_path / "untouched-data"
    data.mkdir()
    (data / "sentinel").write_text("keep")
    monkeypatch.setenv("AI_MANAGER_DATA", str(data))
    monkeypatch.setenv("AI_MANAGER_SECURE_COOKIE", "1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "unrelated"))
    tests = run_gate(root, base)
    secrets = run_secret_gate(root, main_branch(root))
    before = git(root, "status", "--porcelain"), git(root, "rev-parse", "HEAD")
    command = shlex.split(self_check_command(root, base))
    assert command[0] == sys.executable
    assert Path(command[1]).is_absolute()
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    result = json.loads(completed.stdout)
    assert completed.returncode == (1 if failure in ("tests", "secrets") else 0), completed.stderr
    assert result["failures"] == tests["failures"] + secrets["failures"]
    assert result["checks"] == tests["checks"] + secrets["checks"]
    assert result["count"] == tests["count"] + secrets["count"]
    if failure == "tests":
        assert result["checks"] == ["smoke", "targeted"]
        assert "assert False" in result["output"]
    if failure == "secrets":
        assert result["failures"] == ["temporary.txt:1: github-token"]
        assert "ghp_" + "A" * 36 not in completed.stdout
    assert (git(root, "status", "--porcelain"), git(root, "rev-parse", "HEAD")) == before
    assert list(data.iterdir()) == [data / "sentinel"]


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
async def test_first_and_repair_prompts_include_exact_command_and_only_worker_rules(world, restricted):
    world.deployable(False)
    world.settings.restricted = restricted
    instructions = []
    original = world.lab.factory

    def factory(settings, native=False):
        server = original(settings, native)
        thread = server.thread

        async def capture(cwd, text, thread_id=None, model=None, dynamic_tools=None):
            instructions.append(text)
            return await thread(cwd, text, thread_id, model, dynamic_tools)

        server.thread = capture
        return server

    world.core.factory = factory
    world.lab.results = [{"ok": False, "checks": ["secrets"], "count": 1,
                          "failures": ["config:1: github-token"], "output": ""}]
    world.tasks.add(world.project["id"], "implement")
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1
    assert len(world.lab.turns) == 2
    for prompt in world.lab.turns:
        check = prompt["self_check"]
        assert check["command"] == self_check_command(done["workspace"], done["workspace_base"])
        assert shlex.split(check["command"])[2:] == ["--root", done["workspace"], "--base", done["workspace_base"]]
        assert check["instruction"] == "Run it before finishing; it is the same check the application will run."
    assert "preserve main" in world.lab.turns[1]["repair"]["instruction"]
    for text in instructions:
        assert "7. Update the relevant docs in place" in text
        assert "Managers read/search" not in text
        assert "8. The owner's host agent" not in text
        assert "9. A restricted work instance" not in text
        assert "This is a restricted work instance" not in text

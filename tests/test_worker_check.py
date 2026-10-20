"""Workers can reproduce delivery checks from another project's isolated worktree."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_core import git, make_checked_repo, run_next
from test_core import world as world_fixture

from ai_manager.core import run_gate, self_check_command
from scripts.worker_check import stale_pitfalls

world = world_fixture


@pytest.mark.parametrize("content", [None, b"# Instructions\nSee `missing.py`.\n", b"\xff\xfe"])
def test_stale_pitfalls_tolerates_absent_section_or_unreadable_document(tmp_path, content):
    if content is not None:
        (tmp_path / "AGENTS.md").write_bytes(content)
    assert stale_pitfalls(tmp_path) == []


@pytest.mark.parametrize("failure", [None, "tests", "key_content", "inherited"])
def test_absolute_command_reuses_gate_for_other_project(tmp_path, monkeypatch, failure):
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
    if failure == "key_content":
        (root / "temporary.txt").write_text("ghp_" + "A" * 36 + "\n")
        git(root, "add", "temporary.txt")
        git(root, "commit", "-qm", "synthetic key-shaped content")
        git(root, "rm", "temporary.txt")
        git(root, "commit", "-qm", "removed later")
    if failure == "inherited":
        (shared / "existing.txt").write_text("ghp_" + "A" * 36 + "\n")
        git(shared, "add", "existing.txt")
        git(shared, "commit", "-qm", "upstream addition")
        git(root, "merge", "--no-edit", "main")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("still here")
    stale_entry = (
        "### Old helper   (2026-10-20, task abc123)\n"
        "When: changing the check helper\n"
        "Symptom: a surprising import error\n"
        "Cause: a moved module\n"
        "Do: use `scripts/removed.py`, `scripts/removed.py:12` and `MISSING.md`; compare `docs/guide.md#usage`."
    )
    (root / "AGENTS.md").write_text(
        "# Project guidance\nSee `outside/missing.md`.\n"
        "## Known pitfalls\n" + stale_entry + "\n\n"
        "### Current helper\nDo: read `docs/guide.md`, call `run_gate()`, run `python -m pytest`, "
        "and see `https://example.com/guide`.\n"
        "## Other guidance\n### Other entry\nDo: use `outside/missing.md`.\n"
    )
    data = tmp_path / "untouched-data"
    data.mkdir()
    (data / "sentinel").write_text("keep")
    monkeypatch.setenv("AI_MANAGER_DATA", str(data))
    monkeypatch.setenv("AI_MANAGER_SECURE_COOKIE", "1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "unrelated"))
    tests = run_gate(root, base)
    before = git(root, "status", "--porcelain"), git(root, "rev-parse", "HEAD")
    command = shlex.split(self_check_command(root, base))
    assert command[0] == sys.executable
    assert Path(command[1]).is_absolute()
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    result = json.loads(completed.stdout)
    assert completed.returncode == (1 if failure == "tests" else 0), completed.stderr
    assert result["failures"] == tests["failures"]
    assert result["checks"] == tests["checks"]
    assert result["count"] == tests["count"]
    assert result["ok"] == tests["ok"]
    assert result["stale_pitfalls"] == [{"entry": stale_entry, "missing_paths": ["scripts/removed.py", "MISSING.md"]}]
    if failure == "tests":
        assert result["checks"] == ["smoke", "targeted"]
        assert "assert False" in result["output"]
    if failure == "key_content":
        assert result["ok"] and result["failures"] == []
        assert "ghp_" + "A" * 36 not in completed.stdout
    assert (git(root, "status", "--porcelain"), git(root, "rev-parse", "HEAD")) == before
    assert list(data.iterdir()) == [data / "sentinel"]


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("failed_check", ["smoke", "merge"])
async def test_first_and_repair_prompts_include_exact_command_and_only_worker_rules(world, restricted, failed_check):
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
    world.lab.results = [{"ok": False, "checks": [failed_check], "count": 1,
                          "failures": ["permanent check failed"], "output": ""}]
    world.tasks.add(world.project["id"], "implement")
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1
    assert len(world.lab.turns) == 2
    for prompt in world.lab.turns:
        check = prompt["self_check"]
        assert check["command"] == self_check_command(done["workspace"], done["workspace_base"])
        assert shlex.split(check["command"])[2:] == ["--root", done["workspace"], "--base", done["workspace_base"]]
        assert check["instruction"] == "Run it before finishing; it is the same check the application will run."
    repair = world.lab.turns[1]["repair"]["instruction"]
    assert {"merge": "commit the merge", "smoke": "Fix the cause"}[failed_check] in repair
    assert ("When the fix reveals a reusable pitfall, add or update its Known pitfalls entry "
            "(or fix it in code) in the same commit.") in repair
    assert "repair" not in world.lab.turns[0]
    for text in instructions:
        assert "7. Update the relevant docs in place" in text
        assert "Managers read/search" not in text
        assert "8. The owner's host agent" not in text
        assert "9. A restricted work instance" not in text
        assert "This is a restricted work instance" not in text
        assert 'Known pitfalls' in text

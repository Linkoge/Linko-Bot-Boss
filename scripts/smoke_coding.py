"""Real coding + exact approval + independent review in a disposable fixture."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.policies import Policies
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    with tempfile.TemporaryDirectory(prefix="ai-manager-coding-") as directory:
        root = Path(directory)
        source = root / "project"
        source.mkdir()
        (source / "calculator.py").write_text("def add(a,b):\n    return a-b\n")
        (source / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\nclass CalculatorTest(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2,3),5)\n  self.assertEqual(add(-2,3),1)\n"
        )
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Smoke",
                "-c",
                "user.email=smoke@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        settings = Settings(data_dir=root / "data", project_roots=(root,))
        settings.prepare()
        db = Database(settings.db_path)
        registry = Registry(db, (root,))
        project = registry.add(source)
        db.set_setting(f"verification_commands:{project['id']}", [["python3", "-m", "unittest", "discover", "-v"]])
        task = Tasks(db).add(
            project["id"],
            "Fix calculator.add to add its arguments. Read files first, invoke the write capability to request exact approval using the current hash, run registered tests, and checkpoint progress. Do not edit tests, commit, or touch any other files.",
            "add(2,3)==5 and add(-2,3)==1; existing tests unchanged",
            verification="Run existing unittest and independent code review",
        )
        workers = Workers(db, registry, settings)
        scheduler = Scheduler(
            db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
        )
        approved = False
        try:
            await workers.start(task["id"])
            deadline = asyncio.get_running_loop().time() + 240
            while asyncio.get_running_loop().time() < deadline:
                row = db.one("SELECT * FROM approvals WHERE status='pending'")
                if row:
                    args = json.loads(row["arguments"])
                    assert row["action"] == "edit_files" and args["path"] == "calculator.py"
                    execution = db.one("SELECT worktree FROM executions WHERE id=?", (row["execution_id"],))
                    assert "a-b" in (Path(execution["worktree"]) / "calculator.py").read_text()
                    Policies(db).decide(row["id"], True)  # Simulated owner for disposable test only.
                    approved = True
                await scheduler.tick()
                current = Tasks(db).get(task["id"])
                if current["status"] in {"completed", "blocked", "failed"}:
                    break
                await asyncio.sleep(0.2)
            print(
                {
                    "status": current["status"],
                    "blocker": current["blocker"],
                    "exact_approval_consumed": bool(db.one("SELECT id FROM approvals WHERE status='consumed'")),
                    "source_preserved": "a-b" in (source / "calculator.py").read_text(),
                }
            )
            if current["status"] != "completed":
                print(db.all("SELECT agent_id,status,error,output FROM executions"))
            assert approved and current["status"] == "completed" and "a-b" in (source / "calculator.py").read_text()
        finally:
            await scheduler.close()
            await workers.close()


if __name__ == "__main__":
    asyncio.run(main())

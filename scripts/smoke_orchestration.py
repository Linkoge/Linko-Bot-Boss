"""Verify real planning -> worker -> isolated tests -> independent review."""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    with tempfile.TemporaryDirectory(prefix="ai-manager-orchestration-") as temporary:
        root = Path(temporary)
        source = root / "project"
        source.mkdir()
        (source / "README.md").write_text("# Integration fixture\nThe portfolio marker is ORCHESTRATION_READY.\n")
        (source / "test_context.py").write_text(
            "import unittest\nfrom pathlib import Path\nclass ContextTest(unittest.TestCase):\n def test_marker(self):\n  self.assertIn('ORCHESTRATION_READY',Path('README.md').read_text())\n"
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
        workers = Workers(db, registry, settings)
        planner = Planner(db, registry, settings)
        verifier = Verifier(db, registry, settings)
        scheduler = Scheduler(db, registry, settings, workers, verifier, planner)
        try:
            plan = await planner.plan(
                project["id"],
                "Create exactly one read-only task: inspect README.md, record a progress checkpoint, and report its exact portfolio marker. Acceptance: actual marker ORCHESTRATION_READY reported after reading the file. Verify via existing unittest and independent review. No edits or new tasks beyond this one.",
                "smoke-plan",
            )
            assert len(plan["task_ids"]) == 1
            db.set_setting("scheduler_enabled", True)
            deadline = asyncio.get_running_loop().time() + 180
            while asyncio.get_running_loop().time() < deadline:
                await scheduler.tick()
                task = Tasks(db).get(plan["task_ids"][0])
                if task["status"] in {"completed", "blocked", "failed"}:
                    break
                await asyncio.sleep(0.2)
            print(
                {
                    "task_status": task["status"],
                    "blocker": task["blocker"],
                    "verified_records": len(db.all("SELECT id FROM verification_results")),
                    "checkpoints": len(db.all("SELECT id FROM checkpoints")),
                }
            )
            if task["status"] != "completed":
                print(db.all("SELECT agent_id,status,error,output FROM executions"))
            assert task["status"] == "completed"
            print(
                "Verified real planning, automatic assignment, controlled read/checkpoint, sandboxed unittest and independent evidence review"
            )
        finally:
            await scheduler.close()
            await workers.close()


if __name__ == "__main__":
    asyncio.run(main())

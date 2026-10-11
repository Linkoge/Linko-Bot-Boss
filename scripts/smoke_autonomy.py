"""Opt-in real Codex delegation/worker/review, using disposable repos and no approvals."""

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
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    observations = []
    with tempfile.TemporaryDirectory(prefix="linko-autonomy-") as directory:
        root = Path(directory)
        settings = Settings(data_dir=root / "data", project_roots=(root / "projects",))
        settings.prepare()
        db = Database(settings.db_path)
        db.set_setting("scheduler_enabled", False)
        registry = Registry(db, settings.project_roots)
        workers = Workers(db, registry, settings)
        scheduler = Scheduler(
            db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
        )
        try:
            for case in ("hello", "coding"):
                source = root / "projects" / case
                source.mkdir(parents=True)
                (source / "README.md").write_text("# Disposable autonomy fixture\n")
                if case == "coding":
                    (source / "calculator.py").write_text("def add(a,b):\n    return a-b\n")
                    (source / "test_calculator.py").write_text(
                        "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n"
                        " def test_add(self):\n  self.assertEqual(add(2,3),5)\n  self.assertEqual(add(-2,3),1)\n"
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
                        "Fixture",
                    ],
                    check=True,
                )
                project = registry.add(source)
                goal = (
                    "Create hello.txt containing exactly Hello World followed by a newline."
                    if case == "hello"
                    else "Fix calculator.add to add its arguments. Run the existing tests; keep tests unchanged."
                )
                plan = await scheduler.planner.plan(project["id"], goal, "autonomy-" + case)
                assert len(plan["task_ids"]) == 1
                task_id = plan["task_ids"][0]
                await workers.start(task_id)
                deadline = asyncio.get_running_loop().time() + 240
                while asyncio.get_running_loop().time() < deadline:
                    assert not db.all("SELECT * FROM approvals"), "Unexpected owner intervention"
                    await scheduler.tick()
                    task = Tasks(db).get(task_id)
                    if task["status"] in {"completed", "blocked", "failed"}:
                        break
                    await asyncio.sleep(0.2)
                evidence = db.one("SELECT evidence FROM verification_results WHERE task_id=? AND passed=1", (task_id,))
                observation = {
                    "case": case,
                    "status": task["status"],
                    "blocker": task["blocker"],
                    "approval_count": len(db.all("SELECT * FROM approvals")),
                    "worker_count": len(
                        db.all("SELECT id FROM executions WHERE task_id=? AND worktree IS NOT NULL", (task_id,))
                    ),
                    "independent_evidence": json.loads(evidence["evidence"]) if evidence else None,
                }
                observations.append(observation)
                print(json.dumps({k: v for k, v in observation.items() if k != "independent_evidence"}), flush=True)
                assert task["status"] == "completed", task["blocker"]
                assert evidence and observation["worker_count"] == 1
                assert not db.setting("verification_commands:" + project["id"])
            print(
                json.dumps(
                    {
                        "cases": [
                            {k: v for k, v in item.items() if k != "independent_evidence"} for item in observations
                        ],
                        "tasks": len(Tasks(db).list()),
                        "result": "passed",
                    }
                ),
                flush=True,
            )
        finally:
            await scheduler.close()
            await workers.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Real manager → Codex → manager acceptance in disposable project/data directories."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.core import Core, Tasks
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default="/home/codex/.local/bin/codex")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="linko-native-smoke-") as directory:
        base = Path(directory)
        root = base / "projects"
        source = root / "calculator"
        source.mkdir(parents=True)
        (source / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        (source / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2,3),5)\n  self.assertEqual(add(-2,3),1)\n"
        )
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        settings = Settings(data_dir=base / "data", project_roots=(root,), codex_bin=args.codex)
        settings.prepare()
        db = Database(settings.db_path)
        registry = Registry(db, (root,))
        project = registry.add(source)
        chat = Chat(db, registry, AppServer(settings))
        workers = Core(db, registry, settings, Pipeline(db, registry))
        scheduler = Scheduler(db, registry, settings, workers, chat)
        chat.action_handler = scheduler.manager_action
        try:
            started = time.monotonic()
            origin = chat.conversation(project["id"])["id"]
            scheduler.start()
            await chat.submit(
                project["id"],
                "Please fix calculator.add in this project so it adds rather than subtracts. Preserve the tests. Create hello.txt containing exactly Hello World followed by a newline. Run the tests, create a Git commit locally, and complete the assignment. Do not push. Delegate this complete objective to Codex now.",
            )
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                tasks = Tasks(db).list(project["id"])
                if db.one("SELECT id FROM questions WHERE status='pending'"):
                    raise AssertionError("Routine native development requested approval")
                result = db.one("SELECT * FROM executions WHERE task_id IS NOT NULL AND delivery='delivered'")
                if result:
                    assert len(tasks) == 1
                    assert tasks[0]["status"] == "done", tasks[0]
                    # The fixture project has no saved deploy setup: passing work merges only.
                    work = Path(tasks[0]["workspace"])
                    assert (work / "calculator.py").read_text().find("a + b") >= 0
                    check = subprocess.run(
                        ["python3", "-m", "unittest", "discover", "-v"], cwd=work, capture_output=True, text=True
                    )
                    assert check.returncode == 0, check.stderr
                    assert int(subprocess.check_output(["git", "-C", str(work), "rev-list", "--count", "HEAD"])) == 2
                    assert (work / "hello.txt").read_text() == "Hello World\n"
                    assert (source / "calculator.py").read_text().find("a + b") >= 0  # merged into main
                    assert (source / "hello.txt").read_text() == "Hello World\n"
                    reply = db.one("SELECT * FROM executions WHERE id=?", (result["delivery_execution"],))
                    assert reply["conversation_id"] == origin and reply["output"].strip()
                    workers.end(result["id"], "done", "", "Repeated event")
                    await scheduler.tick()
                    assert len(db.all("SELECT * FROM executions WHERE agent_id='manager'")) == 2
                    print(
                        json.dumps(
                            {
                                "passed": True,
                                "seconds": round(time.monotonic() - started, 1),
                                "task_count": len(tasks),
                                "worker_output": result["output"][-1500:],
                                "manager_reply": chat.history(project["id"])["messages"][-1]["content"][-1500:],
                            }
                        )
                    )
                    return
                failed = db.one("SELECT * FROM executions WHERE status='failed'")
                if failed:
                    raise RuntimeError(failed["error"])
                await asyncio.sleep(1)
            raise RuntimeError(
                "Timed out: "
                + json.dumps(
                    {
                        "tasks": Tasks(db).list(),
                        "executions": db.all("SELECT agent_id,status,error,output FROM executions"),
                    }
                )
            )
        finally:
            await scheduler.close()
            await workers.close()
            await chat.close()


if __name__ == "__main__":
    asyncio.run(main())

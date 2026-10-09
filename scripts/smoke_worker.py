"""Real worker capability test in a disposable committed repository."""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    with tempfile.TemporaryDirectory(prefix="ai-manager-worker-") as temporary:
        root = Path(temporary)
        source = root / "project"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "README.md").write_text("The required integration marker is WORKER_CAPABILITY_READY.\n")
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
        task = Tasks(db).add(
            project["id"],
            "Use manager_workspace to read README.md, record a progress checkpoint, and report the exact integration marker found there. Do not edit files.",
            "Marker read through controlled capability and reported",
        )
        workers = Workers(db, registry, settings)
        try:
            job = await workers.start(task["id"])
            await asyncio.wait_for(workers.jobs[job["execution_id"]], 120)
            result = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
            print(
                {
                    k: result[k]
                    for k in ("status", "model", "effort", "thread_id", "turn_id", "output", "error", "latency_ms")
                }
            )
            assert result["status"] == "verifying" and "WORKER_CAPABILITY_READY" in result["output"]
            events = db.all("SELECT payload FROM events WHERE kind='worker.capability'")
            assert any('"operation": "read"' in e["payload"] for e in events)
            assert Tasks(db).get(task["id"])["status"] != "completed"
            print("Verified real worker read capability, isolation, progress persistence and verification gate")
            current = Tasks(db).get(task["id"])
            paused = Tasks(db).transition(task["id"], "paused", current["revision"])
            resumed = await workers.resume(task["id"], paused["revision"])
            await asyncio.wait_for(workers.jobs[resumed["execution_id"]], 120)
            again = db.one("SELECT * FROM executions WHERE id=?", (resumed["execution_id"],))
            assert again["thread_id"] == result["thread_id"] and again["worktree"] == result["worktree"]
            assert again["status"] == "verifying" and "WORKER_CAPABILITY_READY" in again["output"]
            print("Verified real worker resumes the same persisted thread and isolated workspace")
        finally:
            await workers.close()


if __name__ == "__main__":
    asyncio.run(main())

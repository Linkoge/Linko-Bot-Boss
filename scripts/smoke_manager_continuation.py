"""Real manager → Codex → manager acceptance with isolated data and a disposable project."""

import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    with tempfile.TemporaryDirectory(prefix="linko-continuation-") as directory:
        base = Path(directory)
        root = base / "projects"
        source = root / "text-file"
        source.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        settings = Settings(data_dir=base / "data", project_roots=(root,))
        settings.prepare()
        db = Database(settings.db_path)
        registry = Registry(db, (root,))
        project = registry.add(source)
        chat = Chat(db, registry, AppServer(settings))
        workers = Workers(db, registry, settings)
        scheduler = Scheduler(db, registry, settings, workers, chat)
        chat.action_handler = scheduler.manager_action
        origin = chat.conversation(project["id"])["id"]
        started = time.monotonic()
        try:
            scheduler.start()
            await chat.submit(
                project["id"],
                "Create hello.txt containing exactly 'Automatic continuation works.\\n' in this project. "
                "Delegate this to Codex now, then report the result to me when it finishes. No Git commit is needed.",
                origin,
            )
            # No further user input or manual scheduler ticks.
            async with asyncio.timeout(300):
                while not (
                    result := db.one("SELECT * FROM executions WHERE task_id IS NOT NULL AND delivery='delivered'")
                ):
                    if db.one("SELECT id FROM questions WHERE status='pending'"):
                        raise AssertionError("Text-file creation unexpectedly requested confirmation")
                    if scheduler.error:
                        raise RuntimeError(scheduler.error)
                    failure = db.one("SELECT error FROM executions WHERE status='failed'")
                    if failure:
                        raise RuntimeError(failure["error"])
                    await asyncio.sleep(0.5)
            tasks = Tasks(db).list(project["id"])
            assert len(tasks) == 1 and tasks[0]["status"] == "completed", tasks
            assert (source / "hello.txt").read_text() == "Automatic continuation works.\n"
            reply = db.one("SELECT * FROM executions WHERE id=?", (result["delivery_execution"],))
            assert reply["conversation_id"] == origin and reply["status"] == "completed"
            assert reply["output"].strip() and "hello.txt" in reply["output"]
            manager_turns = db.all("SELECT * FROM executions WHERE agent_id='manager' ORDER BY started_at")
            assert len(manager_turns) == 2
            assert manager_turns[0]["thread_id"] == reply["thread_id"]
            assert not db.one(
                "SELECT seq FROM events WHERE kind='item/started' AND json_extract(payload,'$.item.type')='sleep'"
            )
            assert len([m for m in chat.history(project["id"])["messages"] if m["role"] == "user"]) == 1
            # A replay and another scheduler pass cannot send another report.
            workers.finish(result["id"], "completed", "repeated event", "")
            await scheduler.tick()
            assert len(db.all("SELECT id FROM executions WHERE agent_id='manager'")) == 2
            print(
                json.dumps(
                    {
                        "passed": True,
                        "seconds": round(time.monotonic() - started, 1),
                        "original_conversation": origin,
                        "file_content": (source / "hello.txt").read_text(),
                        "worker_result": result["output"],
                        "manager_report": reply["output"],
                        "user_messages": 1,
                    }
                ),
                flush=True,
            )
        finally:
            await scheduler.close()
            await workers.close()
            await chat.close()


if __name__ == "__main__":
    asyncio.run(main())

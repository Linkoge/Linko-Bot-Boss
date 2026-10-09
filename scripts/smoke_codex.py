"""Opt-in real subscription smoke test; no API billing provider is used."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry


async def main():
    with tempfile.TemporaryDirectory(prefix="ai-manager-smoke-") as directory:
        settings = Settings(data_dir=Path(directory), project_roots=())
        db = Database(settings.db_path)
        server = AppServer(settings)
        chat = Chat(db, Registry(db, ()), server)
        try:
            job = await chat.submit(
                None, "Integration test: reply with exactly MANAGER_READY. Do not use any tools or take actions."
            )
            await asyncio.wait_for(chat.jobs[job["execution_id"]], 120)
            execution = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
            print(
                {
                    key: execution[key]
                    for key in (
                        "status",
                        "model",
                        "effort",
                        "thread_id",
                        "turn_id",
                        "output",
                        "error",
                        "latency_ms",
                        "usage",
                    )
                }
            )
            assert execution["status"] == "completed" and "MANAGER_READY" in execution["output"]
            # Resume the exact persisted thread in a fresh process.
            thread_id = execution["thread_id"]
            await chat.close()
            resumed = AppServer(settings)
            chat = Chat(db, Registry(db, ()), resumed)
            job = await chat.submit(None, "Integration test: reply with exactly RESUME_READY. Do not use tools.")
            await asyncio.wait_for(chat.jobs[job["execution_id"]], 120)
            result = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
            print({key: result[key] for key in ("status", "thread_id", "turn_id", "output", "error")})
            assert (
                result["thread_id"] == thread_id
                and result["status"] == "completed"
                and "RESUME_READY" in result["output"]
            )
        finally:
            await chat.close()


if __name__ == "__main__":
    asyncio.run(main())

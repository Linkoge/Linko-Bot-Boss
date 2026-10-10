"""Opt-in real manager/planner checks in disposable repositories; scheduling stays paused."""

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main(output_path):
    cases = [
        ("implementation_auto", "auto", "Make the Tasks page display newest tasks first.", [], 1),
        (
            "investigation_execute",
            "execute",
            "Investigate why the Tasks page displays older tasks first and report findings.",
            [],
            1,
        ),
        (
            "complex_execute",
            "execute",
            "Create two independent tasks explicitly: write an onboarding guide in docs/onboarding.md; produce a research report comparing SQLite and PostgreSQL in docs/storage-research.md. These are separate deliverables and neither needs the other.",
            [],
            2,
        ),
        (
            "dependencies_execute",
            "execute",
            "Create two separate execution stages: first produce and validate an API contract in docs/api.md; then implement a client against the resulting contract. The client requires the validated contract output.",
            [],
            2,
        ),
        (
            "followup_auto",
            "auto",
            "do it",
            [
                ("user", "I want newest tasks first on the Tasks page."),
                ("assistant", "I can change the Tasks ordering to newest first and test it."),
            ],
            1,
        ),
        ("conversation", "conversation", "Make the Tasks page display newest tasks first.", [], 0),
    ]
    output = []
    for name, mode, message, history, expected in cases:
        with tempfile.TemporaryDirectory(prefix="stage2-model-") as directory:
            root = Path(directory)
            source = root / "project"
            source.mkdir()
            (source / "README.md").write_text("# Fixture\nA Tasks page backed by SQLite.\n")
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            settings = Settings(data_dir=root / "data", project_roots=(root,))
            settings.prepare()
            db = Database(settings.db_path)
            registry = Registry(db, (root,))
            project = registry.add(source)
            server = AppServer(settings)
            workers = Workers(db, registry, settings)
            planner = Planner(db, registry, settings)
            scheduler = Scheduler(db, registry, settings, workers, Verifier(db, registry, settings), planner)
            chat = Chat(db, registry, server)
            chat.action_handler, chat.receipt_provider = scheduler.manager_action, scheduler.receipts
            conversation = chat.conversation(project["id"])
            with db.transaction() as conn:
                for index, (role, content) in enumerate(history):
                    conn.execute(
                        "INSERT INTO messages VALUES(?,?,?,?,'completed',NULL,?)",
                        (str(index), conversation["id"], role, content, index + 1),
                    )
            try:
                job = await chat.submit(project["id"], message, mode)
                await asyncio.wait_for(chat.jobs[job["execution_id"]], 180)
                tasks = Tasks(db).list()
                execution = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
                record = {
                    "case": name,
                    "mode": mode,
                    "task_count": len(tasks),
                    "chat_status": execution["status"],
                    "chat_output": execution["output"],
                    "worker_count": len(workers.jobs),
                    "tasks": [
                        {k: t[k] for k in ("description", "kind", "status", "acceptance", "verification_spec")}
                        for t in tasks
                    ],
                    "dependency_count": len(db.all("SELECT * FROM dependencies")),
                    "proposals": [json.loads(p["proposal"]) for p in db.all("SELECT proposal FROM plans")],
                }
                if name == "implementation_auto" and len(tasks) == 1:
                    task_id = tasks[0]["id"]
                    with db.transaction() as conn:
                        conn.execute(
                            "UPDATE tasks SET status='failed',blocker='Usage limit interruption' WHERE id=?", (task_id,)
                        )
                    reuse = await planner.plan(
                        project["id"],
                        "Resume the newest-first ordering assignment that failed because of usage limits.",
                        "resume",
                    )
                    record["resume_same_task"] = reuse["task_ids"] == [task_id]
                    record["resume_task_status"] = Tasks(db).get(task_id)["status"]
                    record["resume_total_tasks"] = len(Tasks(db).list())
                    assert (
                        record["resume_same_task"]
                        and record["resume_task_status"] == "failed"
                        and record["resume_total_tasks"] == 1
                    )
                output.append(record)
                print(json.dumps(record), flush=True)
                assert len(tasks) == expected, (name, record)
                assert not workers.jobs and all(t["status"] == "pending" for t in tasks)
                if name == "complex_execute":
                    assert record["dependency_count"] == 0
                if name == "dependencies_execute":
                    assert record["dependency_count"] == 1
            finally:
                await chat.close()
                await scheduler.close()
                await workers.close()
                await server.close()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    asyncio.run(main(parser.parse_args().output))

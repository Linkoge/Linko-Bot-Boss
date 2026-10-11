"""Report saved App Server usage; optional four-case acceptance in disposable data."""

import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

from ai_manager.chat import Chat
from ai_manager.codex import AppServer, TurnUsage
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.workers import Workers

KEYS = ("inputTokens", "cachedInputTokens", "outputTokens", "totalTokens")


def report(db, ids):
    rows = [db.one("SELECT * FROM executions WHERE id=?", (identifier,)) for identifier in ids]
    result = {"success": all(r["status"] == "completed" for r in rows), "executions": ids, "roles": {}}
    result["latency_seconds"] = round(max(r["finished_at"] for r in rows) - min(r["started_at"] for r in rows), 3)
    for role in ("manager", "execution"):
        selected = [r for r in rows if r["agent_id"] == role]
        counts = {"turns": len(selected), "usage_reports": 0, **dict.fromkeys(KEYS, 0)}
        for row in selected:
            usage = TurnUsage()
            for event in db.all(
                "SELECT payload FROM events WHERE execution_id=? AND kind='thread/tokenUsage/updated' ORDER BY seq",
                (row["id"],),
            ):
                usage.record(json.loads(event["payload"])["tokenUsage"])
            counts["usage_reports"] += len(usage.seen)
            for key in KEYS:
                counts[key] += usage.turn.get(key, 0)
        counts["estimatedUncachedInputTokens"] = counts["inputTokens"] - counts["cachedInputTokens"]
        counts["output_characters"] = sum(len(r["output"]) for r in selected)
        result["roles"][role] = counts
    return result


async def live(args):
    base = Path(args.live_directory).resolve()
    base.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = base / "projects" / "fixture"
    source.mkdir(parents=True)
    (source / "unrelated.txt").write_bytes(b"preserve me\n")
    settings = Settings(data_dir=base / "data", project_roots=(source.parent,), codex_bin=args.codex)
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, settings.project_roots)
    project = registry.add(source)
    # Reuse owner memory from the recorded project without touching its files or database.
    with sqlite3.connect(f"file:{Path(args.seed_database).resolve()}?mode=ro", uri=True) as seed:
        seed.row_factory = sqlite3.Row
        with db.transaction() as conn:
            for row in seed.execute("SELECT * FROM memory WHERE project_id=?", (args.seed_project,)):
                conn.execute(
                    "INSERT INTO memory VALUES(?,?,?,?,?)",
                    (project["id"], row["name"], row["content"], row["revision"], row["updated_at"]),
                )
            for row in seed.execute("SELECT * FROM tasks WHERE project_id=?", (args.seed_project,)):
                conn.execute(
                    "INSERT INTO tasks(id,project_id,description,lifecycle,legacy,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                    (row["id"], project["id"], row["description"], "completed", row["created_at"], row["updated_at"]),
                )
    chat = Chat(db, registry, AppServer(settings))
    workers = Workers(db, registry, settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    cases = (
        ("A", "hey qhats up ", 0),
        ("B", "Create auto-test.txt containing Hello from Codex.", 1),
        ("C", "Change auto-test.txt to contain Second version.", 1),
        (
            "D",
            'I want you to execute five separate tasks sequentially in the current project.\nUse only a new folder called "sequential-test/". Do not modify anything outside it.\n1. Create "sequential-test/data.txt" containing "10".\n2. Read that file, add 5 to the number, and save "15".\n3. Read the updated file, multiply the number by 2, and save "30".\n4. Create "sequential-test/result.txt" and write the final number into it.\n5. Verify that both files contain "30".\nUse five distinct worker assignments, starting each only after its predecessor completes. Execute the entire chain automatically without further user messages.',
            5,
        ),
    )
    results = {}
    try:
        scheduler.start()
        for name, request, worker_count in cases:
            before = {r["id"] for r in db.all("SELECT id FROM executions")}
            await chat.submit(project["id"], request)
            deadline = time.monotonic() + 300
            while True:
                rows = [r for r in db.all("SELECT * FROM executions ORDER BY started_at") if r["id"] not in before]
                if db.one("SELECT id FROM questions WHERE status='pending'"):
                    raise AssertionError("Routine operation requested approval")
                if any(r["status"] == "failed" for r in rows):
                    raise AssertionError("Execution failed; inspect retained fixture diagnostics")
                work = [r for r in rows if r["task_id"]]
                if (
                    not chat.jobs
                    and not workers.jobs
                    and len({r["task_id"] for r in work}) == worker_count
                    and all(r["delivery"] == "delivered" for r in work)
                ):
                    break
                if time.monotonic() > deadline:
                    raise AssertionError(f"Case {name} timed out; inspect retained fixture diagnostics")
                await asyncio.sleep(0.25)
            if name in {"B", "C"}:
                assert (source / "auto-test.txt").read_bytes() == (
                    b"Hello from Codex." if name == "B" else b"Second version."
                )
            if name == "D":
                assert (source / "sequential-test/data.txt").read_bytes() == b"30"
                assert (source / "sequential-test/result.txt").read_bytes() == b"30"
                assert all(a["finished_at"] <= b["started_at"] for a, b in zip(work, work[1:]))
                assert {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()} == {
                    "unrelated.txt",
                    "auto-test.txt",
                    "sequential-test/data.txt",
                    "sequential-test/result.txt",
                }
            assert (source / "unrelated.txt").read_bytes() == b"preserve me\n"
            assert len(db.all("SELECT id FROM messages WHERE role='worker'")) == len(
                db.all("SELECT id FROM executions WHERE task_id IS NOT NULL")
            )
            results[name] = report(db, [r["id"] for r in rows])
            print(json.dumps({"case": name, **results[name]}), flush=True)
            Path(args.output).write_text(json.dumps(results, indent=2) + "\n")
    finally:
        await scheduler.close()
        await workers.close()
        await chat.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database")
    parser.add_argument("--ids", help="Comma-separated recorded execution IDs")
    parser.add_argument("--live-directory", help="Opt in to paid/subscription inference in a new disposable directory")
    parser.add_argument("--seed-database")
    parser.add_argument("--seed-project")
    parser.add_argument("--codex", default="/home/codex/.local/bin/codex")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.live_directory:
        asyncio.run(live(args))
    else:
        # Read-only adapter avoids migrations or any mutation of recorded executions.
        class Records:
            def all(self, sql, params=()):
                with sqlite3.connect(f"file:{Path(args.database).resolve()}?mode=ro", uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    return [dict(r) for r in conn.execute(sql, params)]

            def one(self, sql, params=()):
                return self.all(sql, params)[0]

        Path(args.output).write_text(json.dumps(report(Records(), args.ids.split(",")), indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Synthetic Stage 1 request capture and opt-in real Codex response comparison.

Uses temporary databases and repositories, never the running application. Live tools
return fixture records only: no planning, workers, scheduling or administration run.
"""

import argparse
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import PLAN_SCHEMA, Planner
from ai_manager.registry import Registry

CASES = {
    "conversation": ("auto", "Hello! How is your day?", []),
    "reasoning": (
        "conversation",
        "Explain the tradeoffs between SQLite WAL and PostgreSQL for this small project.",
        [],
    ),
    "brainstorming": ("auto", "Brainstorm three options for improving the error page. We are exploring ideas.", []),
    "implementation": ("auto", "Implement an accessible error page in this project and test it.", []),
    "followup": (
        "auto",
        "do it",
        [
            ("user", "I want an accessible error page."),
            ("assistant", "I can implement an accessible error page and test its keyboard navigation."),
        ],
    ),
    "failed_report": ("auto", "What happened to the worker, and what are our options?", []),
    "completed_report": ("auto", "What did the worker complete?", []),
    "history": (
        "conversation",
        "What did we decide, and why?",
        [
            (
                "user",
                "Earlier notes. " * 100
                + "DECISION: use SQLite WAL for the local prototype; reconsider PostgreSQL after multi-user deployment.",
            ),
            ("assistant", "Recorded that decision and its reason."),
        ],
    ),
    "execute_followup": (
        "execute",
        "do it",
        [
            ("user", "Implement an accessible error page and test it."),
            ("assistant", "The implementation will include keyboard navigation tests."),
        ],
    ),
}
FIXTURE_PLAN = {
    "tasks": [
        {
            "description": "Implement accessible error page",
            "acceptance": "Keyboard navigation works",
            "verification": "Run project tests",
            "priority": 50,
            "dependencies": [],
            "kind": "code",
            "verification_spec": {
                "files": [],
                "sections": [],
                "allowed_paths": [],
                "evidence_file": "",
                "require_commands": True,
                "integration": "none",
            },
        }
    ]
}


def tool_result():
    return {
        "plan_id": "fixture-plan",
        "task_ids": ["fixture-task"],
        "status": "queued",
        "tasks": [
            {
                "task_id": "fixture-task",
                "project_id": "fixture-project",
                "project": "sample",
                "status": "pending",
                "blocker": "Scheduler paused",
                "worker": None,
            }
        ],
        "scheduler": {"enabled": False},
    }


class CaptureServer(AppServer):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []
        self.process = SimpleNamespace(pid=os.getpid())
        self.catalog = [
            {
                "model": "gpt-6.1-sol",
                "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high")],
                "defaultReasoningEffort": "low",
            }
        ]

    async def start(self):
        pass

    async def close(self):
        pass

    async def request(self, method, params, timeout=30):
        self.calls.append({"method": method, "params": copy.deepcopy(params)})
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "fixture-thread"}}
        if method == "turn/start":
            queue = self.queues[params["threadId"]]
            output = json.dumps(FIXTURE_PLAN) if params.get("outputSchema") else "Fixture answer"
            queue.put_nowait({"method": "item/agentMessage/delta", "params": {"delta": output}})
            queue.put_nowait(
                {"method": "turn/completed", "params": {"turn": {"id": "fixture-turn", "status": "completed"}}}
            )
            return {"turn": {"id": "fixture-turn"}}
        return {}


async def capture():
    captured = {}
    for name, (mode, message, history) in CASES.items():
        with tempfile.TemporaryDirectory(prefix="stage1-capture-") as directory:
            root = Path(directory)
            repo = root / "projects" / "sample"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "PROJECT.md").write_text("# Project\nA local prototype.\n")
            (repo / "DECISIONS.md").write_text("# Decisions\nUse SQLite WAL for the local prototype.\n")
            settings = Settings(data_dir=root / "data", project_roots=(repo.parent,))
            settings.prepare()
            db = Database(settings.db_path)
            db.set_setting("scheduler_enabled", False)  # Capture manager behavior without dispatch.
            registry = Registry(db, settings.project_roots)
            project = registry.add(repo)
            registry.update(project["id"], project["revision"], {"objective": "Build a local project dashboard"})
            if name in {"failed_report", "completed_report"}:
                with db.transaction() as conn:
                    conn.execute(
                        "INSERT INTO tasks(id,project_id,description,acceptance,status,blocker,created_at,updated_at) VALUES('fixture-task',?,'Accessible error page','Keyboard navigation works',?,?,1,1)",
                        (
                            project["id"],
                            "blocked" if name == "failed_report" else "completed",
                            "Independent keyboard test failed" if name == "failed_report" else "",
                        ),
                    )
                if name == "completed_report":
                    registry.checkpoint(
                        project["id"], "Accessible error page independently verified", ["fixture keyboard test passed"]
                    )
            server = CaptureServer(settings)
            chat = Chat(db, registry, server)
            actions = []

            async def handler(selected, params):
                actions.append(copy.deepcopy(params))
                return tool_result()

            chat.action_handler = handler
            conversation = chat.conversation(project["id"])
            if history:
                with db.transaction() as conn:
                    # Force the existing schema-rollover path to exercise history assembly.
                    conn.execute(
                        "UPDATE conversations SET thread_id='old-fixture-thread' WHERE id=?", (conversation["id"],)
                    )
                    for index, (role, content) in enumerate(history):
                        conn.execute(
                            "INSERT INTO messages VALUES(?,?,?,?,'completed',NULL,?)",
                            (f"history-{index}", conversation["id"], role, content, index + 1),
                        )
            job = await chat.submit(project["id"], message, mode)
            await chat.jobs[job["execution_id"]]
            execution = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
            record = {
                "mode": mode,
                "calls": server.calls,
                "actions": actions,
                "application_output": execution["output"],
                "status": execution["status"],
            }
            encoded = json.dumps(record).replace(directory, "/fixture")
            # All content is synthetic; normalize generated IDs for readable comparisons.
            encoded = encoded.replace(project["id"], "fixture-project").replace(
                conversation["id"], "fixture-conversation"
            )
            encoded = re.sub(r"[0-9a-f]{32}", "fixture-generated-id", encoded)
            captured[name] = json.loads(encoded)
            await chat.close()
    with tempfile.TemporaryDirectory(prefix="stage1-planning-") as directory:
        root = Path(directory)
        repo = root / "sample"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        settings = Settings(data_dir=root / "data", project_roots=(root,))
        db = Database(settings.db_path)
        registry = Registry(db, settings.project_roots)
        project = registry.add(repo)
        server = CaptureServer(settings)
        planner = Planner(db, registry, settings, lambda _: server)
        planner.apply = lambda *args: {}
        await planner.plan(project["id"], "Implement an accessible error page and test it", "fixture-plan-request")
        encoded = json.dumps(server.calls).replace(directory, "/fixture").replace(project["id"], "fixture-project")
        captured["planning"] = {"calls": json.loads(encoded), "schema": PLAN_SCHEMA}
    return captured


async def replay(record, server, root):
    calls = record["calls"]
    thread = next(c["params"] for c in calls if c["method"] in {"thread/start", "thread/resume"})
    turn = next(c["params"] for c in calls if c["method"] == "turn/start")
    tid = await server.thread(
        root, thread["developerInstructions"], model=turn["model"], dynamic_tools=thread.get("dynamicTools")
    )
    queue = asyncio.Queue()
    server.queues[tid] = queue
    tool_calls = []

    async def handler(params):
        tool_calls.append({"tool": params["tool"], "arguments": params["arguments"]})
        args = params["arguments"]
        args = json.loads(args) if isinstance(args, str) else args
        if record.get("mode") == "conversation" and args.get("action") not in {"read_document", "show_approvals"}:
            raise ValueError("Conversation mode does not delegate work or mutate administration")
        if args.get("action") == "read_document":
            return {
                "path": args.get("path"),
                "content": "Synthetic local project documentation. No additional decisions recorded.",
            }
        return tool_result()

    server.tool_handlers[tid] = handler
    text = ""
    try:
        await server.request("turn/start", {**turn, "threadId": tid})
        while True:
            event = await asyncio.wait_for(queue.get(), 120)
            if event["method"] == "item/agentMessage/delta":
                text += event["params"].get("delta", "")
            elif turn.get("outputSchema") and event["method"] == "item/completed":
                item = event["params"].get("item", {})
                if item.get("type") == "agentMessage":
                    # Match structured_turn: schema output is the completed final
                    # agent message, rather than commentary plus final deltas.
                    text = item.get("text", text)
            elif event["method"] == "turn/completed":
                result = event["params"]["turn"]
                return {
                    "status": result["status"],
                    "response": text,
                    "tool_calls": tool_calls,
                    "error": result.get("error"),
                }
            elif event["method"] == "transport/lost":
                raise RuntimeError("Transport lost")
    finally:
        server.queues.pop(tid, None)
        server.tool_handlers.pop(tid, None)


async def main(args):
    if args.capture:
        result = await capture()
    else:
        before, after = (json.loads(Path(p).read_text()) for p in args.live)
        result = {
            "model": "gpt-6.1-sol",
            "manager_effort": "low",
            "planning_effort": "medium",
            "tools": "synthetic, no application execution",
        }
        with tempfile.TemporaryDirectory(prefix="stage1-live-") as directory:
            root = Path(directory)
            server = AppServer(Settings(data_dir=root, project_roots=()))
            try:
                await server.start()
                for name in args.cases or CASES:
                    if not before[name]["calls"]:
                        continue  # Execute is an application path, not a model turn.
                    print(f"Evaluating {name}", flush=True)
                    minimal = copy.deepcopy(before[name])
                    # Controlled comparison: same complete user input, model and effort.
                    for call in minimal["calls"]:
                        if call["method"] in {"thread/start", "thread/resume"}:
                            call["params"]["developerInstructions"] = next(
                                c["params"]["developerInstructions"]
                                for c in after[name]["calls"]
                                if c["method"] in {"thread/start", "thread/resume"}
                            )
                            call["params"]["dynamicTools"] = next(
                                c["params"].get("dynamicTools")
                                for c in after[name]["calls"]
                                if c["method"] in {"thread/start", "thread/resume"}
                            )
                    result[name] = {}
                    variants = (
                        ("before", before[name]),
                        ("minimal_same_context", minimal),
                        ("after_assembly", after[name]),
                    )
                    for label, record in variants:
                        result[name][label] = await replay(record, server, root)
                        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
            except Exception as error:
                result["unavailable"] = str(error)[:1000]
            finally:
                await server.close()
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--live", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.capture and not args.live:
        parser.error("Choose --capture or --live")
    asyncio.run(main(args))

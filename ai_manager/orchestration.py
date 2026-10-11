"""Direct manager delegation and an always-on project queue."""

import asyncio
import json
import logging

from .recovery import SilentObserver
from .tasks import Tasks
from .todos import Todos

MANAGER_TOOL = {
    "type": "function",
    "name": "manager_action",
    "description": "Record/update deferred ideas with save_todo (never schedules execution), retrieve list_todos, delegate or continue work, wait with a reason, pause/resume a project, or retrieve project documents, saved memory, assignments and conversation history. Read actions support offset pagination; history query searches older messages.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_todos",
                    "save_todo",
                    "delegate",
                    "continue",
                    "wait",
                    "read_document",
                    "read_memory",
                    "read_task",
                    "list_tasks",
                    "read_history",
                    "pause_project",
                    "resume_project",
                ],
            },
            "project_id": {"type": "string"},
            "objective": {"type": "string"},
            "task_id": {"type": "string"},
            "todo_id": {"type": "string"},
            "title": {"type": "string"},
            "details": {"type": "string"},
            "revision": {"type": "integer", "minimum": 0},
            "path": {"type": "string"},
            "reason": {"type": "string"},
            "query": {"type": "string"},
            "message_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


class Scheduler:
    def __init__(self, db, registry, settings, workers, chat):
        self.db, self.registry, self.settings, self.workers, self.chat = db, registry, settings, workers, chat
        self.observer = SilentObserver(db)
        self.tasks, self.job = Tasks(db), None
        self.lock = asyncio.Lock()
        self.error = ""

    async def manager_action(self, selected_project, params):
        args = params["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        if params["tool"] != "manager_action":
            raise ValueError("Unknown manager action")
        action = args["action"]
        execution = self.db.one(
            "SELECT stop_requested,finished_at FROM executions WHERE id=?", (params.get("manager_execution_id"),)
        )
        if execution and (execution["stop_requested"] or execution["finished_at"] is not None):
            raise ValueError("This manager execution has stopped")
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Offset must be a nonnegative integer")
        if action == "read_history":
            conversation = self.db.one(
                "SELECT id FROM conversations WHERE id=? AND project_id IS ?",
                (params["conversation_id"], selected_project),
            )
            if not conversation:
                raise ValueError("Unknown conversation")
            if args.get("message_id"):
                message = self.db.one(
                    "SELECT id,role,content FROM messages WHERE id=? AND conversation_id=?",
                    (args["message_id"], conversation["id"]),
                )
                if not message:
                    raise ValueError("Unknown message in this conversation")
                content = message.pop("content")
                return {
                    **message,
                    "content": content[offset : offset + 12000],
                    "next_offset": offset + 12000 if len(content) > offset + 12000 else None,
                }
            rows = self.db.all(
                """SELECT id,role,content FROM messages WHERE conversation_id=? AND status='completed'
                AND instr(lower(content),lower(?))>0 ORDER BY created_at DESC,rowid DESC LIMIT 6 OFFSET ?""",
                (conversation["id"], args.get("query", ""), offset),
            )
            return {
                "messages": [
                    {**r, "content": r["content"][:2000], "content_truncated": len(r["content"]) > 2000}
                    for r in rows[:5]
                ],
                "next_offset": offset + 5 if len(rows) > 5 else None,
            }
        if action == "list_todos":
            project_id = args.get("project_id") or selected_project
            if selected_project and selected_project != project_id:
                raise ValueError("To-Do belongs to another project")
            rows = Todos(self.db).list(project_id)
            rows = [r for r in rows if args.get("query", "").lower() in (r['title']+' '+r['details']).lower()]
            return {"entries": rows[offset:offset+20], "next_offset": offset+20 if len(rows)>offset+20 else None}
        project_id = args.get("project_id") or selected_project
        if not project_id or (selected_project and selected_project != project_id):
            raise ValueError("Select the intended project; cross-project work requires All Projects context")
        project = self.registry.get(project_id)
        if action == "save_todo":
            # Deferred recording is allowed in every mode and never calls Tasks.add/control.
            return Todos(self.db).save(project_id, args["title"], args.get("details", ""),
                                       args.get("todo_id"), args.get("revision"))
        if params.get("mode") == "conversation" and action not in {
            "read_document",
            "read_memory",
            "read_task",
            "list_tasks",
        }:
            raise ValueError("Conversation mode is read-only; use Auto or Execute for work")
        if action == "delegate":
            return self.tasks.context(
                self.tasks.add(
                    project_id,
                    args["objective"],
                    params["conversation_id"],
                    params["manager_execution_id"] + ":" + project_id,
                )
            )
        if action == "wait":
            task = self.tasks.get(args["task_id"])
            reason = args.get("reason", "").strip()
            if task["project_id"] != project_id or task["lease_owner"] or task["status"] == "cancelled" or not reason:
                raise ValueError("Waiting needs an inactive assignment in this project and a reason")
            with self.db.transaction() as conn:
                changed = conn.execute(
                    "UPDATE tasks SET lifecycle='waiting',blocker=?,revision=revision+1 WHERE id=? AND revision=? AND lease_owner IS NULL",
                    (reason[:2000], task["id"], task["revision"]),
                )
                if changed.rowcount != 1:
                    raise ValueError("Assignment changed while recording the wait")
                self.db.event(conn, "task.waiting", {"reason": reason[:2000]}, project_id, task["id"])
            return self.tasks.context(self.tasks.get(task["id"]))
        if action == "continue":
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Assignment belongs to another project")
            with self.db.transaction() as conn:
                previous = conn.execute(
                    "SELECT payload FROM events WHERE kind='manager.continued' AND execution_id=? AND task_id=?",
                    (params["manager_execution_id"], task["id"]),
                ).fetchone()
                if previous:
                    return self.tasks.context(self.tasks.get(task["id"]))
                if task["lease_owner"] or task["status"] == "cancelled":
                    raise ValueError("Assignment is still active or cancelled")
                objective = args.get("objective") or task["description"]
                changed = conn.execute(
                    "UPDATE tasks SET lifecycle='queued',description=?,blocker='',origin_conversation=?,revision=revision+1 WHERE id=? AND revision=? AND lease_owner IS NULL",
                    (objective, params["conversation_id"], task["id"], task["revision"]),
                )
                if changed.rowcount != 1:
                    raise ValueError("Assignment changed while continuing")
                self.db.event(
                    conn,
                    "manager.continued",
                    {"call_id": params["callId"]},
                    project_id,
                    task["id"],
                    params["manager_execution_id"],
                )
            return self.tasks.context(self.tasks.get(task["id"]))
        if action in {"pause_project", "resume_project"}:
            updated = self.registry.update(
                project_id, project["revision"], {"status": "paused" if action == "pause_project" else "active"}
            )
            return {k: updated[k] for k in ("id", "status")}
        if action == "list_tasks":
            tasks = self.tasks.list(project_id)
            tasks = [t for t in tasks if args.get("query", "").lower() in t["description"].lower()]
            return {
                "assignments": [
                    {
                        **self.tasks.context(t),
                        "description": t["description"][:300],
                        "description_truncated": len(t["description"]) > 300,
                    }
                    for t in tasks[offset : offset + 20]
                ],
                "next_offset": offset + 20 if len(tasks) > offset + 20 else None,
            }
        if action == "read_task":
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Assignment belongs to another project")
            result = self.db.one(
                "SELECT status,output,error FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
                (task["id"],),
            )
            if result:
                output = result["output"]
                result["output"] = output[offset : offset + 12000]
                result["next_offset"] = offset + 12000 if len(output) > offset + 12000 else None
            evidence = self.db.all(
                "SELECT payload FROM events WHERE task_id=? AND kind='task.evidence' ORDER BY seq DESC LIMIT 5",
                (task["id"],),
            )
            return {**self.tasks.context(task), "result": result, "evidence": [json.loads(e["payload"]) for e in evidence]}
        if action == "read_memory":
            row = self.db.one("SELECT content FROM memory WHERE project_id=? AND name=?", (project_id, args["path"]))
            if not row:
                raise ValueError("Unknown saved memory")
            content = row["content"]
            return {
                "path": args["path"],
                "content": content[offset : offset + 12000],
                "next_offset": offset + 12000 if len(content) > offset + 12000 else None,
            }
        if action == "read_document":
            root = self.registry.allowed_path(project["path"])
            path = (root / args["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 100000:
                raise ValueError("Read a project text document smaller than 100KB")
            return {"path": args["path"], "content": path.read_text(errors="replace")[:12000]}
        raise ValueError("Unsupported action")

    async def tick(self):
        async with self.lock:
            await self.observer.observe(
                {
                    **{key: self.chat.server for key in self.chat.jobs},
                    **{key: server for key, server in self.workers.servers.items() if key in self.workers.jobs},
                }
            )
            await self.workers.recover()
            await self.chat.recover()
            while task := self.tasks.claim(self.settings.max_workers):
                self.workers.launch(task)
            await self.chat.deliver_results()

    async def run(self):
        while True:
            try:
                await self.tick()
                self.error = ""
            except Exception as error:
                self.error = str(error)[:1000]
                logging.getLogger(__name__).exception("queue failed")
            await asyncio.sleep(1)

    def start(self):
        self.job = asyncio.create_task(self.run())

    async def close(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

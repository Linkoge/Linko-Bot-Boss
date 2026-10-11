"""Direct manager delegation and an always-on project queue."""

import asyncio
import json
import logging

from .tasks import Tasks

MANAGER_TOOL = {
    "type": "function",
    "name": "manager_action",
    "description": "Delegate one complete objective to native Codex, continue an existing assignment, mark incomplete work waiting with a reason, read a project document, or pause/resume a project.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["delegate", "continue", "wait", "read_document", "pause_project", "resume_project"],
            },
            "project_id": {"type": "string"},
            "objective": {"type": "string"},
            "task_id": {"type": "string"},
            "path": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


class Scheduler:
    def __init__(self, db, registry, settings, workers, chat):
        self.db, self.registry, self.settings, self.workers, self.chat = db, registry, settings, workers, chat
        self.tasks, self.job = Tasks(db), None
        self.lock = asyncio.Lock()
        self.error = ""

    async def manager_action(self, selected_project, params):
        args = params["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        if params["tool"] != "manager_action":
            raise ValueError("Unknown manager action")
        project_id = args.get("project_id") or selected_project
        if not project_id or (selected_project and selected_project != project_id):
            raise ValueError("Select the intended project; cross-project work requires All Projects context")
        project = self.registry.get(project_id)
        action = args["action"]
        if params.get("mode") == "conversation" and action != "read_document":
            raise ValueError("Conversation mode is read-only; use Auto or Execute for work")
        if action == "delegate":
            return self.tasks.add(
                project_id,
                args["objective"],
                params["conversation_id"],
                params["manager_execution_id"] + ":" + project_id,
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
            return self.tasks.get(task["id"])
        if action == "continue":
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Assignment belongs to another project")
            with self.db.transaction() as conn:
                previous = conn.execute(
                    "SELECT payload FROM events WHERE kind='manager.continued' AND execution_id=? AND json_extract(payload,'$.call_id')=?",
                    (params["manager_execution_id"], params["callId"]),
                ).fetchone()
                if previous:
                    return self.tasks.get(task["id"])
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
            return self.tasks.get(task["id"])
        if action in {"pause_project", "resume_project"}:
            return self.registry.update(
                project_id, project["revision"], {"status": "paused" if action == "pause_project" else "active"}
            )
        if action == "read_document":
            root = self.registry.allowed_path(project["path"])
            path = (root / args["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 100000:
                raise ValueError("Read a project text document smaller than 100KB")
            return {"path": args["path"], "content": path.read_text(errors="replace")[:12000]}
        raise ValueError("Unsupported action")

    async def tick(self):
        async with self.lock:
            await self.workers.recover()
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

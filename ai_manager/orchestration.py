"""Manager actions and the scheduler loop that drives the task core."""

import asyncio
import hashlib
import json
import logging

from .chat import internal_result
from .core import Tasks
from .history import Maintenance
from .project_memory import ProjectMemory
from .recovery import SilentObserver
from .todos import Todos

MANAGER_TOOL = {
    "type": "function",
    "name": "manager_action",
    "description": "Record deferred ideas (To-Dos), delegate and control tasks and projects, or retrieve scoped evidence/history. Passing work is merged and deployed automatically; use deploy only when the owner asks to deploy now (optionally with task_id of a finished task whose branch was never merged). model and effort apply to delegate only when the worker setting is Automatic. Read actions support offset pagination.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "deploy",
                    "deployment_status",
                    "emergency_repair",
                    "repair_status",
                    "list_todos",
                    "save_todo",
                    "delegate",
                    "continue",
                    "hold",
                    "release",
                    "cancel",
                    "read_document",
                    "read_memory",
                    "read_project",
                    "list_memory",
                    "list_history",
                    "read_task",
                    "read_result",
                    "list_tasks",
                    "read_history",
                    "pause_project",
                    "resume_project",
                ],
            },
            "project_id": {"type": "string"},
            "conversation_id": {"type": "string"},
            "acceptance": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "dependencies": {"type": "array", "items": {"type": "string"}},
            "selected_context": {"type": "array", "items": {"type": "string"}},
            "objective": {"type": "string"},
            "task_id": {"type": "string"},
            "execution_id": {"type": "string"},
            "todo_id": {"type": "string"},
            "todos": {"type": "array", "minItems": 1, "maxItems": 200, "items": {"type": "string"}},
            "model": {"type": "string"},
            "effort": {"type": "string"},
            "title": {"type": "string"},
            "details": {"type": "string", "description": "To-Do details, or additional instructions for continue; appended alongside objective."},
            "path": {"type": "string"},
            "reason": {"type": "string"},
            "query": {"type": "string"},
            "status": {"type": "string", "enum": ["queued", "held", "running", "done", "failed"]},
            "message_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


class Scheduler:
    def __init__(self, db, registry, settings, core, chat, pipeline=None):
        self.db, self.registry, self.settings, self.core, self.chat = db, registry, settings, core, chat
        self.observer = SilentObserver(db)
        self.maintenance = Maintenance(db)
        self.tasks, self.job = Tasks(db), None
        self.lock = asyncio.Lock()
        self.error = ""
        self.pipeline = pipeline or core.pipeline

    async def manager_action(self, selected_project, params):
        args = params["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        if params["tool"] != "manager_action":
            raise ValueError("Unknown manager action")
        action = args["action"]
        execution = self.db.one(
            "SELECT stop_requested,finished_at,rowid AS queue_position FROM executions WHERE id=?", (params.get("manager_execution_id"),)
        )
        if execution and (execution["stop_requested"] or execution["finished_at"] is not None):
            raise ValueError("This manager execution has stopped")
        internal = params.get('internal', False)
        if not internal and action in {'delegate', 'continue'} and not args.get('todos'):
            sources = self.db.setting('execution_todo_sources:' + str(params.get('manager_execution_id')))
            if sources:
                args = {**args, 'todos': sources}
        if internal and action in {'delegate', 'continue', 'hold', 'release', 'cancel', 'resume_project', 'deploy', 'emergency_repair'}:
            raise ValueError('Worker result notices are not explicit owner authorization; the owner decides what happens next')
        if args.get("todos") and action not in {"delegate", "continue"}:
            raise ValueError("To-Do ids go only with delegate or continue, when the owner asks to start them")
        if action in {'emergency_repair', 'repair_status'}:
            from scripts.emergency_repair import Repair

            repair = Repair(self.settings.data_dir)
            if action == 'repair_status':
                return repair.records()
            return repair.submit('manager:' + str(params.get('manager_execution_id')), args.get('objective', ''),
                                 params['conversation_id'])
        if action == 'deployment_status':
            return self.pipeline.status()
        if action == 'deploy':
            project_id = args.get('project_id') or selected_project
            if not project_id or (selected_project and project_id != selected_project):
                raise ValueError('Select the manager project to deploy')
            if args.get('task_id'):
                task = self.tasks.get(args['task_id'])
                if task['project_id'] != project_id:
                    raise ValueError('Task belongs to another project')
                self.tasks.request_merge(task['id'])
                return {'merge': 'requested', 'task_id': task['id'],
                        'next': 'the worker merges it and a deploy is requested when the merge succeeds'}
            return self.pipeline.request(project_id, 'owner', args.get('task_id'), params['conversation_id'],
                                         args.get('reason', 'owner requested deploy'))
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Offset must be a nonnegative integer")
        if action == "list_history":
            project_id = args.get("project_id") or selected_project
            if selected_project and selected_project != project_id:
                raise ValueError("History belongs to another project")
            return self.chat.search_history(project_id, args.get("query", ""), offset)
        if action == "read_history":
            # A running turn must not consume future queued owner instructions.
            cutoff = execution["queue_position"] if execution else None
            visible = """ AND (? IS NULL OR execution_id IS NULL OR execution_id IN
                (SELECT id FROM executions WHERE rowid<=?))"""
            history_project = args.get("project_id") or selected_project
            if selected_project and selected_project != history_project:
                raise ValueError("History belongs to another project")
            conversation = self.db.one(
                "SELECT id FROM conversations WHERE id=? AND project_id IS ?",
                (args.get("conversation_id") or params["conversation_id"], history_project),
            )
            if not conversation:
                raise ValueError("Unknown conversation")
            if args.get("message_id"):
                message = self.db.one(
                    "SELECT id,role,content FROM messages WHERE id=? AND conversation_id=?" + visible,
                    (args["message_id"], conversation["id"], cutoff, cutoff),
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
                """SELECT id,role,content FROM messages WHERE NOT """ + internal_result() + """ AND conversation_id=? AND status='completed'
                AND (?='' OR instr(lower(history_text(content)),lower(?))>0)""" + visible + " ORDER BY rowid DESC LIMIT 6 OFFSET ?",
                (conversation["id"], args.get("query", ""), args.get("query", ""), cutoff, cutoff, offset),
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
            if args.get("todo_id"):
                row = self.db.one("SELECT * FROM todos WHERE id=?", (args["todo_id"],))
                if not row or (project_id and row["project_id"] != project_id):
                    raise ValueError("To-Do belongs to another project or does not exist")
                return {"entry": row, "handoff": self.db.setting("todo_handoff:" + row["id"])}
            rows = Todos(self.db).list(project_id)
            rows = [r for r in rows if args.get("query", "").lower() in (r['title']+' '+r['details']).lower()]
            return {"entries": rows[offset:offset+20], "next_offset": offset+20 if len(rows)>offset+20 else None}
        if action == "list_tasks":
            project_id = args.get('project_id') or selected_project
            if selected_project and selected_project != project_id:
                raise ValueError('Tasks belong to another project')
            if project_id:
                self.registry.get(project_id)
            page = self.tasks.page(project_id, limit=5, offset=offset, query=args.get('query', ''),
                                   status=args.get('status'))
            return {'assignments': page['tasks'], 'next_offset': page['next_offset']}
        project_id = args.get("project_id") or selected_project
        if not project_id or (selected_project and selected_project != project_id):
            raise ValueError("Select the intended project; cross-project work requires All Projects context")
        project = self.registry.get(project_id)
        if action == "save_todo":
            # Recording a To-Do never calls Tasks.add/control.
            return Todos(self.db).save(project_id, args["title"], args.get("details", ""), args.get("todo_id"))
        if action == "list_memory":
            rows = self.db.all("SELECT name,updated_at FROM memory WHERE project_id=? AND instr(lower(name),lower(?))>0 ORDER BY name LIMIT 21 OFFSET ?", (project_id,args.get("query", ""),offset))
            return {"records":rows[:20], "next_offset":offset+20 if len(rows)>20 else None}
        if action == "read_project":
            return project
        selected = {k: args[k] for k in ("constraints", "dependencies", "selected_context") if args.get(k)}
        if action in {'delegate', 'continue'}:
            attachments = self.db.setting('execution_attachments:' + params['manager_execution_id'], [])
            if attachments:
                selected['attachments'] = attachments
        if len(json.dumps(selected)) > 12000:
            raise ValueError("Selected task context must be at most 12000 characters")
        if action == "delegate":
            if args.get("task_id"):
                raise ValueError("Use continue to add instructions to an existing task")
            from .model_settings import delegation
            await self.chat.server.start()
            route = delegation(self.db, self.chat.server, args.get("model"), args.get("effort"), project_id)
            task = self.tasks.add(
                project_id, args["objective"], params["conversation_id"],
                f"{params['manager_execution_id']}:{project_id}:{hashlib.sha256(args['objective'].encode()).hexdigest()[:12]}",
                acceptance=args.get("acceptance", ""), selected=selected, model_route=route, todos=args.get("todos"),
            )
            return self.tasks.context(task)
        if action in {"hold", "release", "cancel", "continue"}:
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Task belongs to another project")
            if action == "continue":
                # Both fields are accepted by the tool. Preserve distinct details
                # even when an objective is supplied, without repeating identical text.
                additions = "\n\n".join(dict.fromkeys(
                    text.strip() for text in (args.get("objective", ""), args.get("details", "")) if text.strip()))
                with self.db.transaction() as conn:
                    previous = conn.execute(
                        "SELECT 1 FROM events WHERE kind='manager.continued' AND execution_id=? AND task_id=?",
                        (params["manager_execution_id"], task["id"])).fetchone()
                    if previous:
                        return self.tasks.context(task)  # replayed call
                    if args.get("todos"):
                        Todos(self.db).consume(conn, args["todos"], project_id, task["id"])
                    if selected:
                        old = conn.execute("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task["id"],)).fetchone()
                        context = json.loads(old["payload"]) if old else {}
                        for key, values in selected.items():
                            context[key] = list(context.get(key, [])) + [v for v in values if v not in context.get(key, [])]
                        self.db.event(conn, "task.context", context, project_id, task["id"])
                    self.db.event(conn, "manager.continued", {"call_id": params["callId"]}, project_id, task["id"], params["manager_execution_id"])
                task = await self.core.control(task["id"], "continue", None, additions, args.get("acceptance", ""))
            else:
                task = await self.core.control(task["id"], action)
            return self.tasks.context(task)
        if action in {"pause_project", "resume_project"}:
            updated = self.registry.update(
                project_id, project["revision"], {"status": "paused" if action == "pause_project" else "active"}
            )
            return {k: updated[k] for k in ("id", "status")}
        if action == "read_result":
            result = self.db.one("SELECT id,task_id,project_id,status,output,error,input,delivery FROM executions WHERE id=? AND task_id IS NOT NULL", (args["execution_id"],))
            if not result or result["project_id"] != project_id:
                raise ValueError("Result belongs to another project or does not exist")
            output = result.pop("output")
            return {**result, "output": output[offset:offset + 12000],
                    "next_offset": offset + 12000 if len(output) > offset + 12000 else None,
                    "checks": self.db.all("SELECT seq,payload FROM events WHERE execution_id=? AND kind IN ('task.check','task.round') ORDER BY seq", (result["id"],)),
                    "evidence": self.db.all("SELECT seq,payload FROM events WHERE execution_id=? AND kind='task.evidence' ORDER BY seq", (result["id"],))}
        if action == "read_task":
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Task belongs to another project")
            result = self.db.one(
                "SELECT status,output,error FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
                (task["id"],),
            )
            if result:
                output = result["output"]
                result["output"] = output[offset : offset + 12000]
                result["next_offset"] = offset + 12000 if len(output) > offset + 12000 else None
            def events(*kinds):
                rows = self.db.all(f"SELECT payload FROM events WHERE task_id=? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY seq DESC LIMIT 5",
                                   (task["id"], *kinds))
                return [json.loads(e["payload"]) for e in rows]
            return {**self.tasks.context(task), "result": result, "checks": events("task.check", "task.round"),
                    "evidence": events("task.evidence")}
        if action == "read_memory":
            if not args.get("path"):
                return ProjectMemory(self.db, self.registry).snapshot(project_id)
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
            if args.get('task_id'):
                task = self.tasks.get(args['task_id'])
                if task['project_id'] != project_id:
                    raise ValueError('Task document belongs to another project')
                if task['workspace']:
                    root = self.core.workspace(task, project)
            path = (root / args["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 100000:
                raise ValueError("Read a project text document smaller than 100KB")
            content = path.read_text(errors="replace")
            return {"path": args["path"], "content": content[offset:offset+12000],
                    "next_offset": offset+12000 if len(content)>offset+12000 else None}
        raise ValueError("Unsupported action")

    async def tick(self):
        async with self.lock:
            self.pipeline.report()
            await self.core.runner.tick()  # first: no chat state can keep queued work from starting
            await self.observer.observe({key: self.chat.server for key in self.chat.jobs})
            await self.chat.recover()
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
        self.maintenance.start()
        self.job = asyncio.create_task(self.run())

    async def close(self):
        await self.maintenance.close()
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

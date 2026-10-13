"""Direct manager delegation and an always-on project queue."""

import asyncio
import json
import logging
import time

from .chat import internal_result
from .deployment import Handoff
from .project_memory import ProjectMemory
from .recovery import SilentObserver
from .tasks import Tasks
from .todos import Todos

MANAGER_TOOL = {
    "type": "function",
    "name": "manager_action",
    "description": "Record/update deferred ideas with save_todo (never schedules execution), retrieve list_todos, delegate, pause/cancel an assignment or append instructions and continue the same assignment, wait with a reason, pause/resume a project, or retrieve project documents, saved memory, assignments and conversation history. Read actions support offset pagination; history query searches older messages.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "deploy_release",
                    "deployment_status",
                    "list_todos",
                    "save_todo",
                    "delegate",
                    "continue",
                    "pause",
                    "cancel",
                    "wait",
                    "job_followup",
                    "read_document",
                    "read_memory",
                    "read_project",
                    "list_memory",
                    "list_history",
                    "update_brief",
                    "record_delivery",
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
            "brief": {"type": "object"},
            "retained": {"type": "array", "items": {"type": "object"}},
            "resolved": {"type": "array", "items": {"type": "object"}},
            "entry": {"type": "object"},
            "established": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "string"},
            "deployment_receipt": {"type": "string"},
            "worker_route": {"type": "object", "properties": {
                "model": {"type": "string"}, "effort": {"type": "string", "enum": ["low", "medium", "high"]}},
                "required": ["model", "effort"], "additionalProperties": False},
            "objective": {"type": "string"},
            "task_id": {"type": "string"},
            "execution_id": {"type": "string"},
            "todo_id": {"type": "string"},
            "todos": {"type": "array", "minItems": 1, "maxItems": 200, "items": {
                "type": "object", "properties": {"id": {"type": "string"}, "revision": {"type": "integer"}},
                "required": ["id", "revision"], "additionalProperties": False}},
            "release_id": {"type": "string"},
            "title": {"type": "string"},
            "details": {"type": "string"},
            "revision": {"type": "integer", "minimum": 0},
            "path": {"type": "string"},
            "reason": {"type": "string"},
            "followup_state": {"type": "string", "enum": ["actionable", "owner_wait", "deferred", "complete"]},
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
        self.followup_check_at = 0.0
        self.deployment = Handoff(settings.data_dir)
        self.deployment_busy = lambda: False

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
        if internal and action in {'deploy_release', 'resume_project'}:
            raise ValueError('Internal results/recovery are not owner authorization for deployment or project resume')
        if args.get("todos") and (internal or action not in {"delegate", "continue"}):
            raise ValueError("To-Do handoff requires explicit owner scheduling")
        if internal and action in {'continue', 'delegate'}:
            from .assignment_workflow import get
            target_id = args.get('task_id') if action == 'continue' else params.get('internal_task')
            with self.db.transaction() as conn:
                held = conn.execute('SELECT id,lifecycle FROM tasks WHERE id=?', (target_id,)).fetchone()
                workflow = get(conn, target_id) if held else None
                if held and (held['lifecycle'] in {'paused', 'waiting', 'cancelled', 'pause_requested', 'cancel_requested'} or
                             (workflow or {}).get('state') in {'owner_wait', 'deferred', 'complete'}):
                    raise ValueError('Owner pauses/waits are retained; explicit owner continuation is required')
        if action == 'deployment_status':
            return self.deployment.status()
        if action == 'deploy_release':
            if params.get('mode') == 'conversation':
                raise ValueError('Use Auto or Execute to request deployment')
            from .deployment import read
            config = read(self.deployment.root / 'config.json')
            if not config or (selected_project and self.registry.get(selected_project)['path'] != config['repo']):
                raise ValueError('Deployment is available only for this manager or All Projects')
            return self.deployment.request(args['release_id'], {
                'project_id': selected_project, 'conversation_id': params['conversation_id'],
                'execution_id': params['manager_execution_id']})
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
                AND instr(lower(content),lower(?))>0""" + visible + " ORDER BY rowid DESC LIMIT 6 OFFSET ?",
                (conversation["id"], args.get("query", ""), cutoff, cutoff, offset),
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
        project_id = args.get("project_id") or selected_project
        if not project_id or (selected_project and selected_project != project_id):
            raise ValueError("Select the intended project; cross-project work requires All Projects context")
        project = self.registry.get(project_id)
        if internal and action in {'continue', 'delegate'} and project['status'] != 'active':
            raise ValueError('Internal review cannot schedule work in a paused project')
        if action == "save_todo":
            # Deferred recording is allowed in every mode and never calls Tasks.add/control.
            return Todos(self.db).save(project_id, args["title"], args.get("details", ""),
                                       args.get("todo_id"), args.get("revision"))
        if params.get("mode") == "conversation" and action not in {
            "read_document",
            "read_memory",
            "read_project",
            "list_memory",
            "read_task",
            "read_result",
            "list_tasks",
        }:
            raise ValueError("Conversation mode is read-only; use Auto or Execute for work")
        if action == "list_memory":
            rows = self.db.all("SELECT name,updated_at FROM memory WHERE project_id=? AND instr(lower(name),lower(?))>0 ORDER BY name LIMIT 21 OFFSET ?", (project_id,args.get("query", ""),offset))
            return {"records":rows[:20], "next_offset":offset+20 if len(rows)>20 else None}
        if action == "read_project":
            return project
        if action == "update_brief":
            return ProjectMemory(self.db, self.registry).update(project_id, args["revision"], args["brief"], retained=args.get("retained"), resolved=args.get("resolved"))
        if action == "record_delivery":
            return ProjectMemory(self.db, self.registry).delivery(project_id, args["revision"], args["entry"],
                       args.get("established", []), args["evidence"], args.get("deployment_receipt"), args.get("release_id"))
        selected = {k: args[k] for k in ("constraints", "dependencies", "selected_context") if args.get(k)}
        if action in {'delegate', 'continue'}:
            attachments = self.db.setting('execution_attachments:' + params['manager_execution_id'], [])
            if attachments:
                selected['attachments'] = attachments
        if len(json.dumps(selected)) > 12000:
            raise ValueError("Selected task context must be at most 12000 characters")
        if action == "delegate":
            if args.get("task_id"):
                raise ValueError("Use continue to bundle instructions into an existing assignment")
            from .model_settings import delegation
            await self.chat.server.start()
            route = delegation(self.db, self.chat.server, args.get('worker_route'))
            return self.tasks.context(
                self.tasks.add(
                    project_id,
                    args["objective"],
                    params["conversation_id"],
                    params["manager_execution_id"] + ":" + project_id,
                    acceptance=args.get("acceptance", ""), selected=selected, model_route=route, todos=args.get("todos"),
                )
            )
        if action == 'job_followup':
            task = self.tasks.get(args['task_id'])
            state, reason = args.get('followup_state'), args.get('reason', '').strip()
            if task['project_id'] != project_id or state not in {'actionable', 'owner_wait', 'deferred', 'complete'} or not reason:
                raise ValueError('Follow-up needs the existing assignment, a state and review/reason')
            if state in {'complete', 'actionable'} and (task['lease_owner'] or task['status'] not in {'completed', 'failed'}):
                raise ValueError('Review the inactive worker result first; use explicit continue for held work')
            from .assignment_workflow import get, update
            with self.db.transaction() as conn:
                actual = conn.execute('SELECT lifecycle,lease_owner FROM tasks WHERE id=?', (task['id'],)).fetchone()
                if state in {'complete', 'actionable'} and (actual['lease_owner'] or actual['lifecycle'] not in {'completed', 'failed'}):
                    raise ValueError('Assignment changed while recording follow-up')
                previous = get(conn, task['id'])
                if internal and state == 'actionable' and previous and previous['state'] in {'owner_wait', 'deferred', 'complete'}:
                    raise ValueError('An internal result cannot reopen held or completed work')
                return update(self.db, conn, task['id'], state, reason, params['manager_execution_id'], reset_retry=not internal)
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
                from .assignment_workflow import update
                update(self.db, conn, task["id"], "owner_wait", reason)
                self.db.event(conn, "task.waiting", {"reason": reason[:2000]}, project_id, task["id"])
            return self.tasks.context(self.tasks.get(task["id"]))
        if action in {"pause", "cancel"}:
            task = self.tasks.get(args["task_id"])
            if task["project_id"] != project_id:
                raise ValueError("Assignment belongs to another project")
            if task["status"] in {"cancelled", action + "_requested"} or (action == "pause" and task["status"] == "paused"):
                return self.tasks.context(task)
            return self.tasks.context(await self.workers.control(task["id"], action, args.get("revision", task["revision"])))
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
                    if args.get("todos"):
                        Todos(self.db).consume(conn, args["todos"], project_id, task["id"])
                    return self.tasks.context(self.tasks.get(task["id"]))
                if task["lease_owner"] or task["status"] in {"cancelled", "queued", "running", "pause_requested", "cancel_requested"}:
                    raise ValueError("Wait for confirmed pause/cleanup before bundling this assignment")
                # Append, never silently replace unfinished objective/acceptance/context.
                additions = args.get("objective", "").strip()
                objective = task["description"]
                if additions and additions != objective:
                    objective += "\n\nAdditional instructions (preserve prior requirements):\n" + additions
                acceptance = task["acceptance"]
                extra = args.get("acceptance", "").strip()
                if extra and extra != acceptance:
                    acceptance += ("\n\n" if acceptance else "") + extra
                old = conn.execute("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task["id"],)).fetchone()
                context = json.loads(old["payload"]) if old else {}
                for key, values in selected.items():
                    context[key] = list(context.get(key, []))
                    for value in values:
                        if value not in context[key]:
                            context[key].append(value)
                if len(objective) > 24000 or len(acceptance) > 12000 or len(json.dumps(context)) > 12000:
                    raise ValueError("Bundled instructions exceed assignment limits; prior requirements remain intact")
                changed = conn.execute(
                    "UPDATE tasks SET lifecycle='queued',description=?,acceptance=?,blocker='',origin_conversation=?,revision=revision+1 WHERE id=? AND revision=? AND lease_owner IS NULL",
                    (objective, acceptance, params["conversation_id"], task["id"], args.get("revision", task["revision"])),
                )
                if changed.rowcount != 1:
                    raise ValueError("Assignment changed while continuing")
                if args.get("todos"):
                    Todos(self.db).consume(conn, args["todos"], project_id, task["id"])
                from .assignment_workflow import update
                update(self.db, conn, task["id"], "actionable", "Explicit authorized continuation", reset_retry=True)
                if context:
                    self.db.event(conn, "task.context", context, project_id, task["id"])
                self.db.event(
                    conn, "manager.continued", {"call_id": params["callId"], "added_instructions": additions, "added_acceptance": extra},
                    project_id, task["id"], params["manager_execution_id"],
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
        if action == "read_result":
            result = self.db.one("SELECT id,task_id,project_id,status,output,error,input,delivery FROM executions WHERE id=? AND task_id IS NOT NULL", (args["execution_id"],))
            if not result or result["project_id"] != project_id:
                raise ValueError("Result belongs to another project or does not exist")
            output = result.pop("output")
            return {**result, "output": output[offset:offset + 12000],
                    "next_offset": offset + 12000 if len(output) > offset + 12000 else None,
                    "evidence": self.db.all("SELECT seq,payload FROM events WHERE execution_id=? AND kind='task.evidence' ORDER BY seq", (result["id"],))}
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
            path = (root / args["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 100000:
                raise ValueError("Read a project text document smaller than 100KB")
            content = path.read_text(errors="replace")
            return {"path": args["path"], "content": content[offset:offset+12000],
                    "next_offset": offset+12000 if len(content)>offset+12000 else None}
        raise ValueError("Unsupported action")

    async def tick(self):
        async with self.lock:
            self.deployment.report(self.db)
            if self.deployment.ready_to_stop():
                return
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
            if not self.deployment.gated() and time.monotonic() >= self.followup_check_at:
                from .assignment_workflow import CHECK_INTERVAL, due
                self.followup_check_at = time.monotonic() + CHECK_INTERVAL
                for task in due(self.db):
                    try:
                        await self.chat.submit(task['project_id'], 'Internal unfinished assignment review',
                                               task['origin_conversation'], recovery_task=task['id'])
                    except ValueError:
                        pass  # A competing checker/owner transition won the transaction.
            self.deployment.acknowledge(self.db, bool(self.chat.jobs or self.workers.jobs or self.deployment_busy()))

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

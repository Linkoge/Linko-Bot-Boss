import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone

from .codex import MANAGER_INSTRUCTIONS, AppServer, CodexError, process_start
from .db import Database, new_id
from .memory import END, START
from .registry import Registry
from .tasks import Tasks

ROLES = {
    "interactive": "low",
    "planning": "medium",
    "execution": "medium",
    "verification": "medium",
    "summarization": "low",
}


class Chat:
    def __init__(self, db: Database, registry: Registry, server: AppServer):
        self.db, self.registry, self.server = db, registry, server
        self.jobs = {}
        self.action_handler = None
        self.receipt_provider = None
        self.workspace = db.path.parent / "chat-workspace"
        self.workspace.mkdir(exist_ok=True, mode=0o700)

    def conversation(self, project_id=None):
        if project_id:
            project = self.registry.get(project_id)
            if project["status"] == "removed":
                raise ValueError("Project removed")
        context = project_id or "global"
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations(id,context,project_id,created_at) VALUES(?,?,?,?)",
                (new_id(), context, project_id, time.time()),
            )
            return dict(conn.execute("SELECT * FROM conversations WHERE context=?", (context,)).fetchone())

    def history(self, project_id=None, search=""):
        conversation = self.conversation(project_id)
        rows = self.db.all(
            "SELECT m.*, (SELECT COALESCE(max(seq),0) FROM events e WHERE e.execution_id=m.execution_id) AS stream_cursor FROM messages m WHERE conversation_id=? AND instr(lower(content),lower(?))>0 ORDER BY created_at DESC,m.rowid DESC LIMIT 200",
            (conversation["id"], search),
        )
        return {"conversation": conversation, "messages": list(reversed(rows))}

    def reset(self, project_id, conversation_id, last_message_id, confirmed, archive=False, title=""):
        if not confirmed:
            raise ValueError("Confirm before clearing or archiving a chat")
        if project_id:
            self.registry.get(project_id)
        title = title.strip()
        if len(title) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError("Archive name must be at most 120 characters on one line")
        now = time.time()
        with self.db.transaction() as conn:
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE context=?", (project_id or "global",)
            ).fetchone()
            if not conversation or conversation["id"] != conversation_id:
                raise ValueError("Chat changed. Refresh and confirm the current chat again")
            last = conn.execute(
                "SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not last:
                raise ValueError("This chat is already empty")
            if last["id"] != last_message_id:
                raise ValueError("New messages arrived. Refresh and confirm the current chat again")
            if conn.execute(
                """SELECT 1 FROM messages m LEFT JOIN executions e ON e.id=m.execution_id
                WHERE m.conversation_id=? AND (m.status IN ('queued','streaming')
                OR (e.id IS NOT NULL AND e.finished_at IS NULL)) LIMIT 1""",
                (conversation_id,),
            ).fetchone():
                raise ValueError("Wait for the response to finish or stop it before changing this chat")
            count = conn.execute(
                "SELECT count(*) FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchone()[0]
            if archive:
                if not title:
                    project = self.registry.get(project_id) if project_id else None
                    label = (project["display_name"] or project["name"]) if project else "All Projects"
                    title = f"{label} · {datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d %H:%M UTC}"
                conn.execute(
                    "UPDATE conversations SET context=? WHERE id=?", ("archive:" + conversation_id, conversation_id)
                )
                conn.execute("INSERT INTO chat_archives VALUES(?,?,?)", (conversation_id, title, now))
            else:
                conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
                conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
                conn.execute("DELETE FROM settings WHERE key=?", ("manager_tool_schema:" + conversation_id,))
            fresh_id = new_id()
            conn.execute(
                "INSERT INTO conversations(id,context,project_id,created_at) VALUES(?,?,?,?)",
                (fresh_id, project_id or "global", project_id, now),
            )
            self.db.event(
                conn,
                "chat.archived" if archive else "chat.cleared",
                {
                    "conversation_id": conversation_id,
                    "new_conversation_id": fresh_id,
                    "message_count": count,
                    "title": title if archive else None,
                },
                project_id,
            )
        return {
            "result": "accepted",
            "message": f"Chat archived as “{title}”. A fresh chat is ready."
            if archive
            else "Chat cleared. A fresh chat is ready.",
            "conversation_id": fresh_id,
        }

    def search_history(self, project_id=None, search="", offset=0):
        if project_id:
            self.registry.get(project_id)
        rows = self.db.all(
            """SELECT c.id, c.project_id, c.created_at, a.title, a.archived_at,
            (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
            (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id
             AND instr(lower(m.content),lower(?))>0) AS match_count
            FROM conversations c LEFT JOIN chat_archives a ON a.conversation_id=c.id
            WHERE c.project_id IS ? AND (c.context=? OR a.conversation_id IS NOT NULL)
            AND (instr(lower(COALESCE(a.title,'Current chat')),lower(?))>0 OR EXISTS
              (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND instr(lower(m.content),lower(?))>0))
            ORDER BY COALESCE(a.archived_at,c.created_at) DESC,c.id LIMIT 101 OFFSET ?""",
            (search, project_id, project_id or "global", search, search, offset),
        )
        return {"conversations": rows[:100], "has_more": len(rows) > 100}

    def saved_history(self, conversation_id, project_id=None, before=None, search=""):
        conversation = self.db.one(
            """SELECT c.*,a.title,a.archived_at FROM conversations c
            LEFT JOIN chat_archives a ON a.conversation_id=c.id
            WHERE c.id=? AND c.project_id IS ? AND (c.context=? OR a.conversation_id IS NOT NULL)""",
            (conversation_id, project_id, project_id or "global"),
        )
        if not conversation:
            raise ValueError("Chat was not found in the selected context")
        boundary = None
        if before:
            boundary = self.db.one(
                "SELECT rowid AS position FROM messages WHERE id=? AND conversation_id=?", (before, conversation_id)
            )
            if not boundary:
                raise ValueError("Message was not found in this chat")
        rows = self.db.all(
            """SELECT * FROM messages WHERE conversation_id=? AND (? IS NULL OR rowid<?)
            AND instr(lower(content),lower(?))>0 ORDER BY rowid DESC LIMIT 201""",
            (
                conversation_id,
                boundary["position"] if boundary else None,
                boundary["position"] if boundary else None,
                search,
            ),
        )
        return {
            "conversation": conversation,
            "messages": list(reversed(rows[:200])),
            "has_more": len(rows) > 200,
            "search": search,
        }

    def context(self, project_id):
        from .administration import Administration

        grants = [
            g
            for g in Administration(self.db, self.registry, self.server.settings).grants()
            if g["active"] and (not project_id or g["project_id"] == project_id)
        ]
        if not project_id:
            return {
                "owner_administration_grants": grants,
                "selected_context": "All Projects",
                "projects": [
                    {k: p[k] for k in ("id", "name", "objective", "priority", "status")} for p in self.registry.list()
                ],
                "tasks": [
                    {k: t[k] for k in ("id", "project_id", "description", "status", "blocker")}
                    for t in Tasks(self.db).list()[:40]
                ],
                "instructions": self.db.setting("global_instructions", ""),
            }
        project = self.registry.get(project_id)
        return {
            "owner_administration_grants": grants,
            "selected_context": {
                k: project[k]
                for k in (
                    "id",
                    "name",
                    "description",
                    "objective",
                    "success_criteria",
                    "status",
                    "instructions",
                    "repository",
                    "path",
                )
            },
            "global_instructions": self.db.setting("global_instructions", ""),
            "memory": {m["name"]: self.memory_excerpt(m["content"]) for m in self.registry.memory(project_id)},
            "tasks": [
                {k: t[k] for k in ("id", "description", "status", "acceptance", "blocker")}
                for t in Tasks(self.db).list(project_id)[:30]
            ],
            "checkpoint": self.db.one(
                "SELECT summary,evidence,created_at FROM checkpoints WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ),
        }

    @staticmethod
    def memory_excerpt(content):
        # Generated sections duplicate structured project/tasks/checkpoint data and
        # include old execution identifiers. Preserve owner-authored text and storage.
        before, start, remainder = content.partition(START)
        if start:
            _, end, after = remainder.partition(END)
            if end:
                content = (before + after).strip()
        return content if len(content) <= 2000 else content[:800] + "\n…\n" + content[-1200:]

    def recent_messages(self, conversation_id, execution_id):
        rows = self.db.all(
            "SELECT role,content FROM messages WHERE conversation_id=? AND (execution_id IS NULL OR execution_id!=?) ORDER BY created_at DESC,rowid DESC LIMIT 16",
            (conversation_id, execution_id),
        )
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def routes(self):
        await self.server.start()
        saved = self.db.setting("model_routes", {})
        return {role: self.server.route(**saved.get(role, {"effort": effort})) for role, effort in ROLES.items()}

    async def submit(self, project_id, content, mode="auto"):
        if mode == "execute" and not project_id:
            raise ValueError("Select a project for execution mode")
        if not content.strip() or len(content) > 24000:
            raise ValueError("Message must be 1–24000 characters")
        conversation = self.conversation(project_id)
        routes = await self.routes()
        execution_id, response_id, now = new_id(), new_id(), time.time()
        with self.db.transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM conversations WHERE id=? AND context=?", (conversation["id"], project_id or "global")
            ).fetchone():
                raise ValueError("Chat changed. Please send your message in the fresh chat")
            if conn.execute(
                "SELECT id FROM messages WHERE conversation_id=? AND status IN ('queued','streaming')",
                (conversation["id"],),
            ).fetchone():
                raise ValueError("A response is already running in this conversation")
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (new_id(), conversation["id"], "user", content, "completed", execution_id, now),
            )
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (response_id, conversation["id"], "assistant", "", "queued", execution_id, now + 0.0001),
            )
            conn.execute(
                "INSERT INTO executions(id,project_id,agent_id,model,effort,status,started_at) VALUES(?,?,?,?,?,?,?)",
                (
                    execution_id,
                    project_id,
                    "manager",
                    routes["interactive"]["model"],
                    routes["interactive"]["effort"],
                    "queued",
                    now,
                ),
            )
            self.db.event(conn, "chat.queued", {"message_id": response_id}, project_id, execution_id=execution_id)
        job = asyncio.create_task(
            self._run(execution_id, response_id, conversation, content, routes["interactive"], mode)
        )
        self.jobs[execution_id] = job
        job.add_done_callback(lambda _: self.jobs.pop(execution_id, None))
        return {
            "result": "accepted",
            "status": "queued",
            "mode": mode,
            "execution_id": execution_id,
            "message_id": response_id,
            "message": "Execution request accepted; planning and dispatch follow the configured scheduler policy"
            if mode == "execute"
            else "Conversation accepted",
        }

    async def _run(self, execution_id, response_id, conversation, content, route, mode="auto"):
        thread_id, output, now = None, "", time.monotonic()
        try:
            from .orchestration import MANAGER_TOOL

            if mode == "execute":
                if not self.action_handler:
                    raise ValueError("Execution delegation is unavailable")
                history = self.recent_messages(conversation["id"], execution_id)
                goal = json.dumps({"message": content, "conversation_history": history}) if history else content
                result = await self.action_handler(
                    conversation["project_id"],
                    {
                        "tool": "manager_action",
                        "callId": "execute:" + execution_id,
                        "arguments": {"action": "plan_goal", "goal": goal},
                        "manager_execution_id": execution_id,
                    },
                )
                with self.db.transaction() as conn:
                    self.db.event(
                        conn,
                        "chat.execution_records",
                        {"tasks": result.get("tasks", [])},
                        conversation["project_id"],
                        execution_id=execution_id,
                    )
                output = "Execution request recorded.\n" + self.receipt_text(result.get("tasks", []))
                self._finish(execution_id, response_id, "completed", "", output, now)
                return

            tool_schema = hashlib.sha256(json.dumps(MANAGER_TOOL, sort_keys=True).encode()).hexdigest()
            prior_thread = conversation["thread_id"]
            rollover = bool(
                prior_thread and self.db.setting("manager_tool_schema:" + conversation["id"]) != tool_schema
            )
            thread_id = await self.server.thread(
                self.workspace,
                MANAGER_INSTRUCTIONS,
                None if rollover else prior_thread,
                route["model"],
                dynamic_tools=[MANAGER_TOOL] if self.action_handler else None,
            )
            if self.action_handler:

                async def action(params):
                    args = params["arguments"]
                    args = json.loads(args) if isinstance(args, str) else args
                    if mode == "conversation" and args.get("action") not in {"read_document", "show_approvals"}:
                        if (
                            args.get("action") != "owner_admin"
                            or json.loads(args.get("request", "{}")).get("action") != "snapshot"
                        ):
                            raise ValueError(
                                "Conversation mode does not delegate work or mutate administration; choose Execute or Auto"
                            )
                    result = await self.action_handler(
                        conversation["project_id"], {**params, "manager_execution_id": execution_id}
                    )
                    if args.get("action") == "plan_goal":
                        with self.db.transaction() as conn:
                            self.db.event(
                                conn, "chat.delegated", result, conversation["project_id"], execution_id=execution_id
                            )
                    return result

                self.server.tool_handlers[thread_id] = action
            queue = asyncio.Queue()
            self.server.queues[thread_id] = queue
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("manager_tool_schema:" + conversation["id"], json.dumps(tool_schema)),
                )
                if rollover:
                    self.db.event(
                        conn,
                        "chat.tool_schema_upgraded",
                        {
                            "previous_thread": prior_thread,
                            "new_thread": thread_id,
                            "schema_sha256": tool_schema,
                            "history_preserved": True,
                        },
                        conversation["project_id"],
                        execution_id=execution_id,
                    )
                conn.execute("UPDATE conversations SET thread_id=? WHERE id=?", (thread_id, conversation["id"]))
                conn.execute(
                    "UPDATE executions SET thread_id=?,pid=?,process_start=?,status='running' WHERE id=?",
                    (thread_id, self.server.process.pid, process_start(self.server.process.pid), execution_id),
                )
                conn.execute("UPDATE messages SET status='streaming' WHERE id=?", (response_id,))
            context = {"context": self.context(conversation["project_id"]), "mode": mode, "message": content}
            if rollover or not prior_thread:
                context["conversation_history"] = self.recent_messages(conversation["id"], execution_id)
            prompt = json.dumps(context)
            started = await self.server.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    **route,
                    "serviceTierForTurn": "default",
                },
            )
            turn_id = started["turn"]["id"]
            with self.db.transaction() as conn:
                conn.execute("UPDATE executions SET turn_id=? WHERE id=?", (turn_id, execution_id))
                self.db.event(
                    conn,
                    "chat.started",
                    {"thread_id": thread_id, "turn_id": turn_id},
                    conversation["project_id"],
                    execution_id=execution_id,
                )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 300)
                except TimeoutError:
                    if (
                        self.db.one("SELECT status FROM executions WHERE id=?", (execution_id,))["status"]
                        == "awaiting_approval"
                    ):
                        continue
                    raise
                method, params = event["method"], event.get("params", {})
                if method == "transport/lost":
                    raise CodexError("Connection lost; execution outcome is uncertain and was not retried")
                if params.get("turnId") and params["turnId"] != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params.get("delta", "")
                    output += delta
                    with self.db.transaction() as conn:
                        conn.execute("UPDATE messages SET content=? WHERE id=?", (output, response_id))
                        conn.execute("UPDATE executions SET output=? WHERE id=?", (output, execution_id))
                        self.db.event(
                            conn, "chat.delta", {"delta": delta}, conversation["project_id"], execution_id=execution_id
                        )
                elif method == "thread/tokenUsage/updated":
                    with self.db.transaction() as conn:
                        conn.execute(
                            "UPDATE executions SET usage=? WHERE id=?",
                            (json.dumps(params.get("tokenUsage", {})), execution_id),
                        )
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item", {})
                    with self.db.transaction() as conn:
                        self.db.event(
                            conn,
                            "chat.item",
                            {"type": item.get("type"), "status": item.get("status"), "lifecycle": method},
                            conversation["project_id"],
                            execution_id=execution_id,
                        )
                elif method == "turn/completed":
                    turn = params["turn"]
                    status = turn["status"]
                    if status not in {"completed", "interrupted", "failed"}:
                        status = "failed"
                    error = (turn.get("error") or {}).get("message", "")
                    delegated = self.db.all(
                        "SELECT payload FROM events WHERE execution_id=? AND kind='chat.delegated' ORDER BY seq",
                        (execution_id,),
                    )
                    if delegated:
                        ids = [
                            task_id
                            for event in delegated
                            for task_id in json.loads(event["payload"]).get("task_ids", [])
                        ]
                        receipts = (
                            self.receipt_provider(ids)
                            if self.receipt_provider
                            else [
                                record
                                for event in delegated
                                for record in json.loads(event["payload"]).get("tasks", [])
                            ]
                        )
                        with self.db.transaction() as conn:
                            self.db.event(
                                conn,
                                "chat.execution_records",
                                {"tasks": receipts},
                                conversation["project_id"],
                                execution_id=execution_id,
                            )
                        output += "\n\nManager execution records:\n" + self.receipt_text(receipts)
                    administration = self.db.all(
                        "SELECT seq,kind,payload FROM events WHERE execution_id=? AND kind IN ('owner_admin.completed','owner_admin.failed','owner_admin.rejected') ORDER BY seq",
                        (execution_id,),
                    )
                    if administration:
                        output += "\n\nPersistent owner administration receipts:\n" + self.administration_text(
                            administration
                        )
                    self._finish(execution_id, response_id, status, error, output, now)
                    return
        except asyncio.CancelledError:
            self._finish(
                execution_id, response_id, "interrupted", "Manager stopped; inspect thread before resuming", output, now
            )
            raise
        except Exception as error:
            self._finish(execution_id, response_id, "failed", str(error)[:2000], output, now)
        finally:
            if thread_id:
                self.server.queues.pop(thread_id, None)
                self.server.tool_handlers.pop(thread_id, None)

    def _finish(self, execution_id, response_id, status, error, output, start):
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE approvals SET status='cancelled' WHERE execution_id=? AND status IN ('pending','approved')",
                (execution_id,),
            )
            conn.execute(
                "UPDATE executions SET status=?,error=?,output=?,finished_at=?,latency_ms=? WHERE id=?",
                (status, error, output, time.time(), (time.monotonic() - start) * 1000, execution_id),
            )
            conn.execute("UPDATE messages SET status=? WHERE id=?", (status, response_id))
            conn.execute("UPDATE messages SET content=? WHERE id=?", (output, response_id))
            self.db.event(conn, "chat.finished", {"status": status, "error": error}, execution_id=execution_id)

    @staticmethod
    def administration_text(events):
        lines = []
        for event in events:
            receipt = json.loads(event["payload"])
            request = receipt.get("request", {})
            identifier = receipt.get("id") or request.get("id") or f"event {event['seq']}"
            status = receipt.get("status") or event["kind"].removeprefix("owner_admin.")
            line = f"Receipt {identifier} · {status} · {request.get('action', 'administration')}"
            blocker = receipt.get("error") or receipt.get("reason")
            lines.append(line + (f" · {blocker}" if blocker else ""))
        return "\n".join(lines)

    @staticmethod
    def receipt_text(records):
        return (
            "\n".join(
                f"Task {r['task_id'][:8]} · {r['project']} · {r['status']}"
                + (f" · Blocker: {r['blocker']}" if r["blocker"] else "")
                for r in records
            )
            or "No task records available; inspect planning execution before retry."
        )

    async def interrupt(self, execution_id):
        execution = self.db.one("SELECT * FROM executions WHERE id=? AND agent_id='manager'", (execution_id,))
        if not execution or execution["finished_at"]:
            raise ValueError("No active response")
        if not execution["turn_id"]:
            job = self.jobs.get(execution_id)
            if job:
                job.cancel()
            return
        await self.server.interrupt(execution["thread_id"], execution["turn_id"])

    def recover(self):
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM executions WHERE agent_id='manager' AND finished_at IS NULL").fetchall()
            for row in rows:
                observed = bool(
                    row["pid"] and row["process_start"] and process_start(row["pid"]) == row["process_start"]
                )
                error = (
                    "Service restarted; previous process still observed"
                    if observed
                    else "Service restarted; previous process not observed; inspect persisted thread before retry"
                )
                conn.execute(
                    "UPDATE executions SET status='interrupted',error=?,finished_at=? WHERE id=?",
                    (error, time.time(), row["id"]),
                )
                conn.execute(
                    "UPDATE messages SET status='interrupted' WHERE execution_id=? AND role='assistant'", (row["id"],)
                )
                self.db.event(conn, "chat.recovered", {"process_observed": observed}, execution_id=row["id"])

    async def close(self):
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await self.server.close()

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone

from .codex import MANAGER_INSTRUCTIONS, AppServer, CodexError, process_start
from .db import Database, new_id
from .registry import Registry
from .tasks import Tasks

ROLES = {"interactive": "low", "execution": "medium"}


class Chat:
    def __init__(self, db: Database, registry: Registry, server: AppServer):
        self.db, self.registry, self.server = db, registry, server
        self.jobs = {}
        self.action_handler = None
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
        if not project_id:
            return {
                "projects": [{k: p[k] for k in ("id", "name", "objective", "status")} for p in self.registry.list()],
                "instructions": self.db.setting("global_instructions", ""),
            }
        p = self.registry.get(project_id)
        return {
            "project": {k: p[k] for k in ("id", "name", "path", "objective", "success_criteria", "instructions")},
            "instructions": self.db.setting("global_instructions", ""),
            "owner_notes": self.registry.notes(project_id),
            "assignments": [
                {k: t[k] for k in ("id", "description", "status", "blocker")}
                for t in Tasks(self.db).list(project_id)[:20]
            ],
        }

    async def routes(self):
        await self.server.start()
        return {
            role: self.server.route(**self.db.setting("model_routes", {}).get(role, {"effort": effort}))
            for role, effort in ROLES.items()
        }

    async def submit(self, project_id, content, conversation_id=None, delivery=None, mode="auto"):
        if mode not in {"conversation", "auto", "execute"}:
            raise ValueError("Unknown chat mode")
        if not content.strip() or (not delivery and len(content) > 48000):
            raise ValueError("Message must be 1–48000 characters")
        conversation = (
            self.db.one("SELECT * FROM conversations WHERE id=? AND project_id IS ?", (conversation_id, project_id))
            if delivery and conversation_id
            else self.conversation(project_id)
        )
        if not conversation or (conversation_id and conversation["id"] != conversation_id):
            raise ValueError("Chat changed. Refresh before sending")
        route = (await self.routes())["interactive"]
        execution_id, response_id, now = new_id(), new_id(), time.time()
        with self.db.transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM conversations WHERE id=? AND project_id IS ? AND (? OR context=?)",
                (conversation["id"], project_id, bool(delivery), project_id or "global"),
            ).fetchone():
                raise ValueError("Chat changed. Refresh before sending")
            if conn.execute(
                "SELECT 1 FROM messages WHERE conversation_id=? AND status IN ('queued','streaming')",
                (conversation["id"],),
            ).fetchone():
                raise ValueError("A response is already running in this conversation")
            conn.execute(
                "INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?)",
                (
                    "result:" + delivery if delivery else new_id(),
                    conversation["id"],
                    "worker" if delivery else "user",
                    content,
                    "completed",
                    execution_id,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (response_id, conversation["id"], "assistant", "", "queued", execution_id, now + 0.0001),
            )
            conn.execute(
                """INSERT INTO executions(id,project_id,agent_id,model,effort,status,started_at,conversation_id,input)
                         VALUES(?,?,'manager',?,?,'queued',?,?,?)""",
                (execution_id, project_id, route["model"], route["effort"], now, conversation["id"], content),
            )
            if delivery:
                cursor = conn.execute(
                    """UPDATE executions SET delivery='running',delivery_execution=? WHERE id=? AND delivery='pending'
                    AND finished_at IS NOT NULL
                    AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=executions.task_id AND t.lease_owner IS NULL
                        AND (t.origin_conversation=? OR t.origin_conversation IS NULL))""",
                    (execution_id, delivery, conversation["id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Result already claimed")
            self.db.event(conn, "chat.queued", {"message_id": response_id}, project_id, execution_id=execution_id)
        job = asyncio.create_task(self._run(execution_id, response_id, conversation, content, route, mode))
        self.jobs[execution_id] = job
        job.add_done_callback(lambda _: self.jobs.pop(execution_id, None))
        return {"result": "accepted", "status": "queued", "execution_id": execution_id, "message_id": response_id}

    async def _run(self, execution_id, response_id, conversation, content, route, mode):
        thread_id, output = None, ""
        try:
            from .orchestration import MANAGER_TOOL

            schema = hashlib.sha256(json.dumps(MANAGER_TOOL, sort_keys=True).encode()).hexdigest()
            prior = conversation["thread_id"]
            if self.db.setting("manager_tool_schema:" + conversation["id"]) != schema:
                prior = None
            thread_id = await self.server.thread(
                self.workspace, MANAGER_INSTRUCTIONS, prior, route["model"], dynamic_tools=[MANAGER_TOOL]
            )
            queue = asyncio.Queue()
            self.server.queues[thread_id] = queue
            self.server.tool_handlers[thread_id] = lambda params: self.action_handler(
                conversation["project_id"],
                {**params, "manager_execution_id": execution_id, "conversation_id": conversation["id"], "mode": mode},
            )
            with self.db.transaction() as conn:
                conn.execute("UPDATE conversations SET thread_id=? WHERE id=?", (thread_id, conversation["id"]))
                conn.execute(
                    "UPDATE executions SET thread_id=?,pid=?,process_start=?,status='starting' WHERE id=?",
                    (thread_id, self.server.process.pid, process_start(self.server.process.pid), execution_id),
                )
                conn.execute("UPDATE messages SET status='streaming' WHERE id=?", (response_id,))
                conn.execute(
                    "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("manager_tool_schema:" + conversation["id"], json.dumps(schema)),
                )
            prompt = {"context": self.context(conversation["project_id"]), "mode": mode, "message": content}
            if not prior:
                rows = self.db.all(
                    "SELECT role,content FROM messages WHERE conversation_id=? AND execution_id!=? ORDER BY created_at DESC LIMIT 16",
                    (conversation["id"], execution_id),
                )
                prompt["conversation_history"] = list(reversed(rows))
            turn = await self.server.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": json.dumps(prompt)}],
                    **route,
                    "serviceTierForTurn": "default",
                },
            )
            turn_id = turn["turn"]["id"]
            with self.db.transaction() as conn:
                conn.execute("UPDATE executions SET turn_id=?,status='running' WHERE id=?", (turn_id, execution_id))
            while True:
                event = await queue.get()
                method, params = event["method"], event.get("params", {})
                if method == "transport/lost":
                    raise CodexError("Manager connection lost; inspect saved thread before retry")
                if params.get("turnId") and params["turnId"] != turn_id:
                    continue
                with self.db.transaction() as conn:
                    if method == "item/agentMessage/delta":
                        output = (output + params.get("delta", ""))[-1000000:]
                        conn.execute("UPDATE messages SET content=? WHERE id=?", (output, response_id))
                        conn.execute("UPDATE executions SET output=? WHERE id=?", (output, execution_id))
                    self.db.event(
                        conn,
                        "chat.delta" if method == "item/agentMessage/delta" else method,
                        params,
                        conversation["project_id"],
                        execution_id=execution_id,
                    )
                if method == "turn/completed":
                    self._finish(
                        execution_id,
                        response_id,
                        params["turn"]["status"],
                        (params["turn"].get("error") or {}).get("message", ""),
                        output,
                    )
                    return
        except asyncio.CancelledError:
            self._finish(execution_id, response_id, "interrupted", "Service stopped or turn interrupted", output)
            raise
        except Exception as error:
            self._finish(execution_id, response_id, "failed", str(error)[:2000], output)
        finally:
            if thread_id:
                self.server.queues.pop(thread_id, None)
                self.server.tool_handlers.pop(thread_id, None)

    def _finish(self, execution_id, response_id, status, error, output):
        with self.db.transaction() as conn:
            execution = conn.execute("SELECT finished_at FROM executions WHERE id=?", (execution_id,)).fetchone()
            if not execution or execution["finished_at"] is not None:
                return
            conn.execute(
                "UPDATE executions SET status=?,error=?,output=?,finished_at=? WHERE id=?",
                (status, error, output, time.time(), execution_id),
            )
            conn.execute("UPDATE messages SET status=?,content=? WHERE id=?", (status, output, response_id))
            conn.execute(
                "UPDATE executions SET delivery=? WHERE delivery_execution=?",
                ("delivered" if status == "completed" else "pending", execution_id),
            )
            self.db.event(conn, "chat.finished", {"status": status, "error": error}, execution_id=execution_id)

    async def deliver_results(self):
        for result in self.db.all(
            """SELECT e.*,t.origin_conversation,t.description,t.lifecycle AS task_status,t.blocker
            FROM executions e JOIN tasks t ON t.id=e.task_id
            LEFT JOIN executions reply ON reply.id=e.delivery_execution
            WHERE e.delivery='pending' AND e.finished_at IS NOT NULL AND t.lease_owner IS NULL
            AND (reply.id IS NULL OR reply.finished_at<=?) ORDER BY e.finished_at""",
            (time.time() - 30,),
        ):
            if result["origin_conversation"]:
                conversation = self.db.one("SELECT * FROM conversations WHERE id=?", (result["origin_conversation"],))
                # A deliberately cleared chat must not leak into its replacement.
                if not conversation:
                    with self.db.transaction() as conn:
                        conn.execute(
                            "UPDATE executions SET delivery='discarded' WHERE id=? AND delivery='pending'",
                            (result["id"],),
                        )
                    continue
            else:
                conversation = self.conversation(result["project_id"])
            if self.db.one(
                "SELECT 1 FROM messages WHERE conversation_id=? AND status IN ('queued','streaming')",
                (conversation["id"],),
            ):
                continue
            try:
                objective = json.loads(result["input"]).get("objective", result["description"])
            except (ValueError, AttributeError):
                objective = result["description"]
            content = "Codex result:\n" + json.dumps(
                {
                    "task_id": result["task_id"],
                    "objective": objective,
                    "task_status": result["task_status"],
                    "blocker": result["blocker"],
                    "output": result["output"][-24000:],
                    "output_truncated": len(result["output"]) > 24000,
                    "execution": {
                        k: result[k]
                        for k in (
                            "id",
                            "status",
                            "error",
                            "thread_id",
                            "turn_id",
                            "worktree",
                            "branch",
                            "commit_ref",
                            "model",
                            "effort",
                            "started_at",
                            "finished_at",
                            "latency_ms",
                            "usage",
                        )
                    },
                },
                ensure_ascii=False,
            )
            try:
                await self.submit(conversation["project_id"], content, conversation["id"], result["id"])
            except ValueError:
                continue

    async def interrupt(self, execution_id):
        row = self.db.one("SELECT * FROM executions WHERE id=? AND agent_id='manager'", (execution_id,))
        if not row or row["finished_at"]:
            raise ValueError("No active manager turn")
        if row["turn_id"]:
            await self.server.interrupt(row["thread_id"], row["turn_id"])
        elif execution_id in self.jobs:
            self.jobs[execution_id].cancel()

    async def recover(self):
        for row in self.db.all("SELECT * FROM executions WHERE agent_id='manager' AND finished_at IS NULL"):
            status, output, error = "interrupted", row["output"], "Service restarted; turn outcome uncertain"
            if row["thread_id"]:
                try:
                    await self.server.start()
                    saved = await self.server.request(
                        "thread/read", {"threadId": row["thread_id"], "includeTurns": True}
                    )
                    turn = next((x for x in saved["thread"].get("turns", []) if x["id"] == row["turn_id"]), None)
                    if turn and turn["status"] == "completed":
                        status, error = "completed", ""
                        output = "\n".join(
                            x.get("text", "") for x in turn.get("items", []) if x.get("type") == "agentMessage"
                        )
                except Exception:
                    pass
            message = self.db.one("SELECT id FROM messages WHERE execution_id=? AND role='assistant'", (row["id"],))
            self._finish(row["id"], message["id"] if message else "", status, error, output)

    async def close(self):
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        await self.server.close()

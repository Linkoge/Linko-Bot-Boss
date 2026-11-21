"""Office mode (every instance but Main): several chats inside the instance's one fixed project.

Storage is the existing tables only. Exactly one chat of the project is *open*: its conversation has the usual context
(the project id), so sending, history, attachments, compaction and Clear work on it unchanged. Every other chat is
*parked* with context `chat:<id>`; opening one swaps the two contexts in one transaction. A running reply or a worker
result keeps writing to its chat by id. Deleting a chat turns it into a normal named chat archive (`archive:<id>` +
`chat_archives`), nothing is hard-deleted.

Per-chat metadata lives in one settings record per chat, `office_chat:<conversation id>`:
title / title_state (pending → running → done | failed; owner after a rename), pinned_at, and the memory mark:
memory_mark (the last message rowid a memory update read) and memory_at (when it ran).

- Title: after the first owner message, one background call with the newest Haiku at low effort writes a short title
  from that message, once. A failed call leaves the placeholder (the message's first words).
- Memory on leave: switching to another chat or + New checks the chat being left. If it has at least
  `leave_messages` new owner/assistant messages since its mark, OR at least `leave_min_messages` and
  `leave_minutes` have passed since its last memory update (its creation if never), one background job runs the
  existing new/update/keep/drop pipeline on ONLY those new messages, merges the result into the instance's chat memory
  and advances the mark. It never archives, compacts or otherwise changes the chat. One memory job per instance.
"""

import asyncio
import json
import re
import time

from . import chat_memory
from .db import new_id

META = "office_chat:"
PARKED = "chat:"
TITLE_WORDS = 6
TITLE_CHARS = 80
PLACEHOLDER_WORDS = 6
TITLE_TIMEOUT = 60
LIST_LIMIT = 200
TITLE_INSTRUCTIONS = f"""You name a chat. Never call tools. Reply with the title only: at most {TITLE_WORDS} words, in
the language of the message, no quotes, no trailing punctuation. The title says what the owner wants, from their first
message below."""


def placeholder(text):
    words = " ".join((text or "").split()).split(" ")
    shown = " ".join(words[:PLACEHOLDER_WORDS])[:TITLE_CHARS]
    return (shown + ("…" if len(words) > PLACEHOLDER_WORDS else "")) if shown else "New chat"


def clean_title(text):
    line = next((line for line in (text or "").splitlines() if line.strip()), "")
    line = re.sub(r"^(title\s*:\s*)", "", line.strip(), flags=re.I).strip(" \"'`*#“”‘’")
    line = line.rstrip(".!?:;,")
    words = line.split()
    return " ".join(words[:TITLE_WORDS + 2])[:TITLE_CHARS].strip()


async def ask(server, workspace, instructions, text, route, timeout):
    """One tool-less model turn; returns the reply text. Raises on failure or timeout."""
    thread = turn_id = None
    done = False

    async def run():
        nonlocal thread, turn_id, done
        await server.start()
        thread = await server.thread(workspace, instructions, None, route["model"])
        queue = asyncio.Queue()
        server.queues[thread] = queue
        turn = await server.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}],
                                                   **route, "serviceTierForTurn": "default"})
        turn_id, output = turn["turn"]["id"], ""
        while True:
            event = await queue.get()
            method, params = event["method"], event.get("params", {})
            if method == "transport/lost":
                raise ValueError("The model connection was lost")
            event_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
            if event_turn and event_turn != turn_id:
                continue
            if method == "item/agentMessage/delta":
                output += params.get("delta", "")
                if len(output) > 20000:
                    raise ValueError("Output exceeds its safety limit")
            if method == "turn/completed":
                done = True
                if params["turn"]["status"] != "completed":
                    raise ValueError("The model turn did not complete")
                return params.get("reconciled_output", output)

    try:
        return await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError:
        raise ValueError(f"timed out after {timeout} seconds") from None
    finally:
        if thread:
            server.queues.pop(thread, None)
            if turn_id and not done:
                try:
                    await server.interrupt(thread, turn_id)
                except Exception:
                    pass


class OfficeChats:
    def __init__(self, chat):
        self.chat, self.db = chat, chat.db
        self.title_jobs = {}  # conversation id -> running title task
        self.memory_job = None  # the one running memory-on-leave task of this instance
        self.title_timeout = TITLE_TIMEOUT

    # Metadata

    def meta(self, conversation_id):
        return self.db.setting(META + conversation_id) or {}

    def save_meta(self, conn, conversation_id, **changes):
        row = conn.execute("SELECT value FROM settings WHERE key=?", (META + conversation_id,)).fetchone()
        value = {**(json.loads(row["value"]) if row else {}), **changes}
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (META + conversation_id, json.dumps(value, ensure_ascii=False)))
        return value

    def chat_row(self, conn, project_id, conversation_id):
        """An office chat of this project: the open one or a parked one."""
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND project_id IS ? AND (context=? OR context=?)",
                           (conversation_id, project_id, project_id, PARKED + conversation_id)).fetchone()
        if not row:
            raise ValueError("Chat was not found")
        return row

    # Listing and search

    def list(self, project_id, search=""):
        from .chat import hidden_message
        search = (search or "").strip()[:500]
        current = self.chat.conversation(project_id)
        visible = "m.conversation_id=c.id AND m.role IN ('user','assistant') AND NOT " + hidden_message("m")
        rows = self.db.all(
            f"""SELECT c.id,c.created_at,c.context=? AS open,s.value AS meta,
            COALESCE((SELECT max(m.created_at) FROM messages m WHERE {visible}),c.created_at) AS active_at,
            (SELECT m.content FROM messages m WHERE {visible} AND m.role='user' ORDER BY m.rowid LIMIT 1) AS first,
            (SELECT count(*) FROM messages m WHERE {visible}) AS message_count
            FROM conversations c LEFT JOIN settings s ON s.key='{META}' || c.id
            WHERE c.project_id IS ? AND (c.context=? OR c.context='{PARKED}' || c.id)
            AND (c.context=? OR EXISTS(SELECT 1 FROM messages m WHERE {visible}))
            AND (?='' OR instr(lower(COALESCE(json_extract(s.value,'$.title'),'')),lower(?))>0
                 OR EXISTS(SELECT 1 FROM messages m WHERE {visible} AND instr(lower(history_text(m.content)),lower(?))>0))
            ORDER BY json_extract(s.value,'$.pinned_at') IS NULL, active_at DESC, c.id LIMIT ?""",
            (project_id, project_id, project_id, project_id, search, search, search, LIST_LIMIT))
        chats = []
        for row in rows:
            meta = json.loads(row["meta"]) if row["meta"] else {}
            title = meta.get("title") or ""
            chats.append({"id": row["id"], "title": title or placeholder(row["first"]), "titled": bool(title),
                          "title_state": meta.get("title_state", "pending"), "pinned": bool(meta.get("pinned_at")),
                          "open": bool(row["open"]), "active_at": row["active_at"], "created_at": row["created_at"],
                          "message_count": row["message_count"]})
        return {"chats": chats, "open_id": current["id"], "search": search,
                "memory_running": bool(self.memory_job)}

    # Switching

    def open(self, project_id, conversation_id):
        """Make a parked chat the open one; the chat being left gets the memory-on-leave check."""
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM conversations WHERE context=?", (project_id,)).fetchone()
            target = self.chat_row(conn, project_id, conversation_id)
            if current and current["id"] == target["id"]:
                return {"result": "accepted", "conversation_id": target["id"], "memory_update": None}
            if current:
                conn.execute("UPDATE conversations SET context=? WHERE id=?", (PARKED + current["id"], current["id"]))
            conn.execute("UPDATE conversations SET context=? WHERE id=?", (project_id, target["id"]))
            self.db.event(conn, "chat.opened", {"conversation_id": target["id"],
                          "left_conversation_id": current["id"] if current else None}, project_id)
        update = self.left(project_id, current["id"]) if current else None
        return {"result": "accepted", "conversation_id": target["id"], "memory_update": update}

    def new(self, project_id):
        """+ New: a fresh empty chat becomes the open one. An open chat that is still empty is simply kept."""
        current = self.chat.conversation(project_id)
        if not self.has_messages(current["id"]):
            return {"result": "accepted", "conversation_id": current["id"], "memory_update": None}
        fresh = new_id()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM conversations WHERE context=?", (project_id,)).fetchone()
            if row:
                conn.execute("UPDATE conversations SET context=? WHERE id=?", (PARKED + row["id"], row["id"]))
            conn.execute("INSERT INTO conversations(id,context,project_id,created_at) VALUES(?,?,?,?)",
                         (fresh, project_id, project_id, time.time()))
            self.save_meta(conn, fresh, title="", title_state="pending", memory_mark=0)
            self.db.event(conn, "chat.created", {"conversation_id": fresh,
                          "left_conversation_id": row["id"] if row else None}, project_id)
        update = self.left(project_id, row["id"]) if row else None
        return {"result": "accepted", "conversation_id": fresh, "memory_update": update}

    def has_messages(self, conversation_id):
        from .chat import hidden_message
        return bool(self.db.one("SELECT 1 FROM messages WHERE conversation_id=? AND NOT " + hidden_message()
                                + " LIMIT 1", (conversation_id,)))

    # Pin, rename, delete

    def pin(self, project_id, conversation_id, pinned):
        with self.db.transaction() as conn:
            self.chat_row(conn, project_id, conversation_id)
            self.save_meta(conn, conversation_id, pinned_at=time.time() if pinned else None)
        return {"result": "accepted", "conversation_id": conversation_id, "pinned": bool(pinned)}

    def rename(self, project_id, conversation_id, title):
        title = " ".join((title or "").split())
        if not 1 <= len(title) <= 120:
            raise ValueError("Chat name must be 1–120 characters on one line")
        with self.db.transaction() as conn:
            self.chat_row(conn, project_id, conversation_id)
            self.save_meta(conn, conversation_id, title=title, title_state="owner")
        return {"result": "accepted", "conversation_id": conversation_id, "title": title}

    def delete(self, project_id, conversation_id, confirmed):
        """Move the chat to the chat archive (never a hard delete). Deleting the open chat opens the most recently
        active other chat, or a fresh one."""
        if not confirmed:
            raise ValueError("Confirm before deleting this chat")
        if self.chat.compactor.busy(conversation_id):
            raise ValueError("Wait for the response to finish or stop it before deleting this chat")
        listed = {c["id"]: c for c in self.list(project_id)["chats"]}
        now = time.time()
        with self.db.transaction() as conn:
            row = self.chat_row(conn, project_id, conversation_id)
            title = (listed.get(conversation_id) or {}).get("title") or "Chat"
            conn.execute("UPDATE conversations SET context=? WHERE id=?", ("archive:" + conversation_id, conversation_id))
            conn.execute("INSERT INTO chat_archives VALUES(?,?,?)", (conversation_id, title[:120], now))
            self.save_meta(conn, conversation_id, pinned_at=None)
            opened = None
            if row["context"] == project_id:
                other = next((c["id"] for c in sorted(listed.values(), key=lambda c: -c["active_at"])
                              if c["id"] != conversation_id), None)
                if not other:  # an empty parked chat (left right after + New) is reused before making another
                    empty = conn.execute("SELECT id FROM conversations WHERE project_id IS ? AND context='chat:' || id "
                                         "ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                    other = empty["id"] if empty else None
                if other:
                    conn.execute("UPDATE conversations SET context=? WHERE id=?", (project_id, other))
                    opened = other
                else:
                    opened = new_id()
                    conn.execute("INSERT INTO conversations(id,context,project_id,created_at) VALUES(?,?,?,?)",
                                 (opened, project_id, project_id, now))
                    self.save_meta(conn, opened, title="", title_state="pending", memory_mark=0)
            self.db.event(conn, "chat.deleted", {"conversation_id": conversation_id, "archived_as": title[:120],
                          "opened_conversation_id": opened, "evidence_retained": True}, project_id)
        return {"result": "accepted", "conversation_id": conversation_id, "opened_conversation_id": opened,
                "message": f"Chat moved to the archive as “{title[:120]}”."}

    # Title: once, in the background, never blocking the chat

    def owner_message(self, project_id, conversation_id):
        """Called after an owner message was accepted: start the one title call if this chat has none yet."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (META + conversation_id,)).fetchone()
            meta = json.loads(row["value"]) if row else {}
            if meta.get("title") or meta.get("title_state", "pending") != "pending":
                return False
            first = conn.execute("SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY rowid "
                                 "LIMIT 1", (conversation_id,)).fetchone()
            if not first or not first["content"].strip():
                return False
            self.save_meta(conn, conversation_id, title_state="running")
        task = asyncio.create_task(self.make_title(project_id, conversation_id, first["content"]))
        self.title_jobs[conversation_id] = task
        task.add_done_callback(lambda _: self.title_jobs.pop(conversation_id, None))
        return True

    async def make_title(self, project_id, conversation_id, message):
        try:
            server = self.chat.server
            await server.start()
            route = server.route(chat_memory.latest_haiku(server), "low")
            reply = await ask(server, self.chat.compactor.workspace, TITLE_INSTRUCTIONS,
                              "First message:\n" + message[:4000], route, self.title_timeout)
            title = clean_title(chat_memory.scrub(reply))
            if not title:
                raise ValueError("empty title")
            state = {"title": title, "title_state": "done"}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state = {"title_state": "failed", "title_error": str(error)[:200]}
        with self.db.transaction() as conn:
            meta = json.loads((conn.execute("SELECT value FROM settings WHERE key=?",
                                            (META + conversation_id,)).fetchone() or {"value": "{}"})["value"])
            if meta.get("title_state") == "running":  # an owner rename meanwhile wins
                self.save_meta(conn, conversation_id, **state)
                self.db.event(conn, "chat.titled" if "title" in state else "chat.title_failed",
                              {"conversation_id": conversation_id, **state}, project_id)

    # Memory on leave

    def new_messages(self, conversation_id, mark):
        from .chat import hidden_message
        rows = self.db.all("SELECT rowid AS position,id,role,content,created_at FROM messages WHERE conversation_id=? "
                           "AND rowid>? AND role IN ('user','assistant') AND status='completed' AND NOT "
                           + hidden_message() + " ORDER BY rowid", (conversation_id, mark))
        return [r for r in rows if r["content"].strip()]

    def left(self, project_id, conversation_id, now=None):
        """The owner left this chat: start one memory update when the rule says so. Returns what happened."""
        now = time.time() if now is None else now
        settings = chat_memory.load_settings(self.db)
        meta = self.meta(conversation_id)
        rows = self.new_messages(conversation_id, meta.get("memory_mark", 0))
        created = self.db.one("SELECT created_at FROM conversations WHERE id=?", (conversation_id,))
        since = meta.get("memory_at") or (created["created_at"] if created else now)
        count = len(rows)
        due = count >= settings["leave_messages"] or (
            count >= settings["leave_min_messages"] and now - since >= settings["leave_minutes"] * 60)
        result = {"conversation_id": conversation_id, "new_messages": count, "started": False}
        if not due:
            return {**result, "reason": "not due"}
        if self.memory_job or self.chat.compactor.jobs:
            return {**result, "reason": "a memory job is already running"}
        task = asyncio.create_task(self.update_memory(project_id, conversation_id, rows))
        self.memory_job = task
        task.add_done_callback(lambda _: setattr(self, "memory_job", None))
        return {**result, "started": True}

    async def update_memory(self, project_id, conversation_id, rows):
        """The existing pipeline on the new messages only; applies to memory and the mark, never to the chat."""
        try:
            result = await self.chat.compactor.generate(project_id, rows)
            now = time.time()
            with self.db.transaction() as conn:
                active = chat_memory.read_row(conn, project_id, chat_memory.ACTIVE)
                archive = chat_memory.read_row(conn, project_id, chat_memory.ARCHIVE)
                items, archived, report = chat_memory.merge(active["items"], archive["items"], result, now)
                chat_memory.write_row(conn, project_id, chat_memory.ACTIVE,
                                      {**active, "items": items, "updates": active.get("updates", 0) + 1})
                chat_memory.write_row(conn, project_id, chat_memory.ARCHIVE, {**archive, "items": archived})
                saved = conn.execute("SELECT value FROM settings WHERE key=?", (META + conversation_id,)).fetchone()
                mark = max((json.loads(saved["value"]) if saved else {}).get("memory_mark", 0), rows[-1]["position"])
                self.save_meta(conn, conversation_id, memory_mark=mark, memory_at=now)
                self.db.event(conn, "chat_memory.updated", {"conversation_id": conversation_id, "on_leave": True,
                              "messages": len(rows), "mark": mark,
                              "report": {k: len(v) for k, v in report.items()}}, project_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            with self.db.transaction() as conn:
                self.db.event(conn, "chat_memory.update_failed", {"conversation_id": conversation_id,
                              "error": str(error)[:500]}, project_id)

    # Files of a chat: references only

    def files(self, conversation_id):
        """The chat's sent files as short references (name, type, where to read it); never their content."""
        rows = self.db.all("SELECT s.value FROM executions e JOIN settings s ON s.key='execution_attachments:' || e.id "
                           "WHERE e.conversation_id=? ORDER BY e.rowid", (conversation_id,))
        files, seen = [], set()
        for row in rows:
            for item in json.loads(row["value"]):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                files.append({k: item[k] for k in ("name", "mime", "path", "text_path") if item.get(k)})
        return files[-40:]

    async def close(self):
        jobs = [*self.title_jobs.values(), *([self.memory_job] if self.memory_job else [])]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

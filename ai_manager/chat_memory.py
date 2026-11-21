"""Chat compaction with two-tier chat memory, on the existing stores only.

- The full previous chat is moved into a normal named chat archive (`conversations` + `chat_archives`), so it stays
  retrievable through Search in chat, list_history and read_history. Nothing is deleted. Messages sent while a
  compaction runs stay in the chat after the summary message.
- Memory items live in the existing `memory` table, two rows per chat scope (a project, or All Projects with
  project_id NULL): `chat_memory` holds the active items fed to the manager every turn, `chat_memory_archive` holds
  expired or dropped items, searchable on demand with read_memory (path + query).
- Short-term items carry last_used; a compaction that reuses an item refreshes it. Short-term items idle for
  `short_term_days` move to the archive; one reused in PROMOTE_AFTER compactions becomes long-term.
- One settings record `chat_compaction`: model (default Latest Haiku) / effort (default low), the automatic threshold and
  the inactivity window. The same `route()` resolves what the settings panel shows and what a compaction uses.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone

from .db import new_id
from .push_diagnosis import PEM_BLOCK, SECRET_PATTERNS, USERINFO

ACTIVE, ARCHIVE = "chat_memory", "chat_memory_archive"
NAMES = (ACTIVE, ARCHIVE)
SETTINGS_KEY = "chat_compaction"
NOTICE = "chat_compaction_notice:"  # + chat context: the latest automatic-compaction notice for that chat
AUTO_STATE = "chat_autocompact:"  # + conversation id: {"next_at": chars} after an automatic attempt
LATEST_HAIKU = "latest-haiku"  # stored model choice: resolved at call time to the newest Haiku in the catalog
DEFAULTS = {"model": LATEST_HAIKU, "effort": "low", "threshold_tokens": 150000, "short_term_days": 30}
THRESHOLD_RANGE = (10000, 1000000)  # tokens, estimated as characters / CHARS_PER_TOKEN
CHARS_PER_TOKEN = 4
DAYS_RANGE = (1, 3650)
PROMOTE_AFTER = 3  # reuses (compactions that kept an item) before a short-term item becomes long-term
MAX_SHORT_TERM = 30  # active short-term items; the least recently used beyond this are archived
MAX_OUTPUT_ITEMS = 60
ITEM_CHARS = 400
SUMMARY_CHARS = 2000
INPUT_CHARS = 200000  # newest chat text given to the compaction call; older text stays in the archive
MESSAGE_CHARS = 6000
TIMEOUT_SECONDS = 300
CHECK_SECONDS = 10
WAIT_SECONDS = 1800  # a ready summary waits this long for a running turn to end before the attempt fails
POLL_SECONDS = 2
RUNNING_TEXT = "Compacting chat in the background… You can keep using the chat."
TIERS = ("short", "long")
ACTIONS = ("new", "update", "keep", "drop")
MASK = "[redacted]"
EXTRA_SECRETS = re.compile(r"\b(?:tskey-[A-Za-z0-9-]{10,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})")
# `password: hunter2x9` style assignments; a value needs 8+ characters and a digit, so prose stays readable.
ASSIGNED = re.compile(r"(?i)\b([A-Za-z0-9_]*(?:key|token|secret|passw(?:or)?d|credential)[A-Za-z0-9_]*)(\s*[=:]\s*)"
                      r"(?=[^\s]*\d)[^\s\"']{8,}")

INSTRUCTIONS = f"""You compact a long owner/manager chat into durable memory. Never call tools; reply with JSON only.
Work in this order: first read existing_memory (long-term and short-term items with ids), then read the chat.
For each point in the chat decide: new item, update of an existing item, reuse of an existing item that is still
relevant, or noise. Keep decisions together with their reasons, open items and what they wait for, owner preferences,
how the project works, and recurring lessons. Keep one line per dead end ("tried X, failed because Y"). Drop
chit-chat, greetings and finished step-by-step details. Never store secrets, credentials, tokens, passwords or keys:
write [redacted] instead of any key-shaped value.
Mark each item "short" (current state: a bug and its fix, something being waited for, a failed attempt) or "long"
(decisions, preferences, how the project works, recurring lessons). One fact per item, at most {ITEM_CHARS} characters.
Return exactly one JSON object:
{{"summary": "<= {SUMMARY_CHARS} characters: where the conversation stands and what happens next",
 "items": [
  {{"action": "new", "tier": "short|long", "text": "..."}},
  {{"action": "update", "id": "<existing id>", "tier": "short|long", "text": "..."}},
  {{"action": "keep", "id": "<existing id that the chat referenced or that is relevant again>"}},
  {{"action": "drop", "id": "<existing id that is now wrong or obsolete>"}}
 ]}}
Existing items you do not mention stay unchanged. At most {MAX_OUTPUT_ITEMS} entries.
"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def scrub(text):
    """Mask key-shaped values (the shared secret patterns plus a few provider token shapes)."""
    text = PEM_BLOCK.sub(MASK, text)
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    text = EXTRA_SECRETS.sub(MASK, USERINFO.sub(r"\1" + MASK + "@", text))
    return ASSIGNED.sub(lambda m: m[1] + m[2] + MASK, text)


def context_name(project_id):
    return project_id or "global"


# Settings: shown = saved = used.

def migrate(saved):
    """Records saved before tokens/Latest Haiku (they carry `threshold_chars`) move to the new defaults once."""
    if "threshold_chars" not in saved:
        return saved
    saved = dict(saved)
    chars = saved.pop("threshold_chars")
    saved.setdefault("threshold_tokens", DEFAULTS["threshold_tokens"] if chars in (150000, 60000)
                     or not isinstance(chars, int) else chars // CHARS_PER_TOKEN)
    if not saved.get("model") or "haiku" in str(saved["model"]).lower():
        saved["model"] = LATEST_HAIKU
    if saved.get("effort") in (None, "", "medium"):
        saved["effort"] = DEFAULTS["effort"]
    return saved


def load_settings(db):
    saved = migrate(db.setting(SETTINGS_KEY) or {})
    return {**DEFAULTS, "revision": 0, **{k: v for k, v in saved.items() if k in (*DEFAULTS, "revision")}}


def latest_haiku(server):
    """The newest Haiku in the provider catalog: the highest version among ids containing "haiku"."""
    found = [m["model"] for m in getattr(server, "catalog", None) or [] if not m.get("hidden")
             and "haiku" in m["model"].lower()]
    if not found:
        raise ValueError("No Haiku model is available from the provider")
    return max(found, key=lambda name: ([int(n) for n in re.findall(r"\d+", name)], name))


def resolve_model(server, model):
    return latest_haiku(server) if model == LATEST_HAIKU else model


def route(db, server, project_id=None):
    """The exact model/effort a compaction runs with: the saved choice (Latest Haiku resolved now)."""
    saved = load_settings(db)
    return server.route(resolve_model(server, saved["model"]), saved["effort"])


def save_settings(db, server, body):
    current = load_settings(db)
    if body.get("revision") != current["revision"]:
        raise ValueError("Chat memory settings changed; refresh and try again")
    value = {k: body.get(k, current[k]) for k in DEFAULTS}
    value["model"] = (value["model"] or "").strip() or None
    value["effort"] = (value["effort"] or "").strip() or None
    if not value["model"]:
        value["model"] = LATEST_HAIKU
    value["effort"] = value["effort"] or DEFAULTS["effort"]
    server.route(resolve_model(server, value["model"]), value["effort"])  # rejects models/efforts not in the catalog
    for key, (low, high) in (("threshold_tokens", THRESHOLD_RANGE), ("short_term_days", DAYS_RANGE)):
        if not isinstance(value[key], int) or isinstance(value[key], bool) or not low <= value[key] <= high:
            raise ValueError(f"{key} must be a whole number from {low} to {high}")
    value["revision"] = current["revision"] + 1
    db.set_setting(SETTINGS_KEY, value)
    return value


# Storage: two rows of the existing memory table per scope.

def read_row(conn, scope, name):
    row = conn.execute("SELECT content FROM memory WHERE project_id IS ? AND name=?", (scope, name)).fetchone()
    return json.loads(row[0]) if row else {"items": []}


def write_row(conn, scope, name, value):
    now = time.time()
    if not conn.execute("UPDATE memory SET content=?,revision=revision+1,updated_at=? WHERE project_id IS ? AND name=?",
                        (encode(value), now, scope, name)).rowcount:
        conn.execute("INSERT INTO memory VALUES(?,?,?,0,?)", (scope, name, encode(value), now))


def expire(items, archive, now, days):
    """Move short-term items idle longer than the window into the archive; returns (active, archive, moved)."""
    cutoff, keep, moved = now - days * 86400, [], []
    for item in items:
        (moved if item["tier"] == "short" and item["last_used"] < cutoff else keep).append(item)
    shorts = sorted((i for i in keep if i["tier"] == "short"), key=lambda i: i["last_used"], reverse=True)
    overflow = {i["id"] for i in shorts[MAX_SHORT_TERM:]}
    moved += [i for i in keep if i["id"] in overflow]
    keep = [i for i in keep if i["id"] not in overflow]
    archived = archive + [{**i, "archived_at": now, "archive_reason": "capacity" if i["id"] in overflow else "inactive"}
                          for i in moved]
    return keep, archived, moved


def sweep(db, scope, now=None):
    """Expire inactive short-term items of one scope; writes only when something moved. Returns active items."""
    now = time.time() if now is None else now
    days = load_settings(db)["short_term_days"]
    with db.transaction() as conn:
        active, archive = read_row(conn, scope, ACTIVE), read_row(conn, scope, ARCHIVE)
        items, archived, moved = expire(active["items"], archive["items"], now, days)
        if moved:
            write_row(conn, scope, ACTIVE, {**active, "items": items})
            write_row(conn, scope, ARCHIVE, {**archive, "items": archived})
            db.event(conn, "chat_memory.expired", {"items": [i["id"] for i in moved]}, scope)
    return items, len(archived)


def manager_view(db, scope):
    """The active memory sent with the manager's context; archived items are only counted, never sent."""
    items, archived = sweep(db, scope)
    if not items and not archived:
        return None
    return {
        "long_term": [{"id": i["id"], "text": i["text"]} for i in items if i["tier"] == "long"],
        "short_term": [{"id": i["id"], "text": i["text"],
                        "last_used": datetime.fromtimestamp(i["last_used"], timezone.utc).strftime("%Y-%m-%d")}
                       for i in items if i["tier"] == "short"],
        "archived_items": archived,
        "retrieval": f"Archived items: read_memory path={ARCHIVE} with query. Older chats: list_history/read_history.",
    }


def search(db, scope, name, query, offset=0):
    """read_memory with a query over chat memory: matching items only, 20 per page."""
    with db.transaction() as conn:
        items = read_row(conn, scope, name)["items"]
    words = [w for w in query.lower().split() if w]
    hits = [i for i in items if all(w in i["text"].lower() for w in words)]
    return {"path": name, "query": query, "items": hits[offset:offset + 20],
            "next_offset": offset + 20 if len(hits) > offset + 20 else None}


# Compaction result: validated before anything is applied.

def parse(text):
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise ValueError("Compaction did not return JSON: " + scrub(text[:200]).replace("\n", " "))


def validate(result, known_ids):
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise ValueError("Compaction result needs a summary text")
    entries = result.get("items")
    if not isinstance(entries, list) or len(entries) > MAX_OUTPUT_ITEMS:
        raise ValueError(f"Compaction result needs an items list of at most {MAX_OUTPUT_ITEMS} entries")
    clean, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("action") not in ACTIONS:
            raise ValueError("Every compaction item needs an action: new, update, keep or drop")
        action = entry["action"]
        out = {"action": action}
        if action != "new":
            if entry.get("id") not in known_ids:
                raise ValueError(f"Compaction referenced an unknown memory item: {str(entry.get('id'))[:40]}")
            if entry["id"] in seen:
                raise ValueError("Compaction referenced one memory item twice")
            seen.add(entry["id"])
            out["id"] = entry["id"]
        if action in ("new", "update"):
            text = entry.get("text")
            if entry.get("tier") not in TIERS or not isinstance(text, str) or not text.strip():
                raise ValueError("New and updated items need a tier (short or long) and text")
            out.update(tier=entry["tier"], text=scrub(" ".join(text.split()))[:ITEM_CHARS])
        clean.append(out)
    return {"summary": scrub(result["summary"].strip())[:SUMMARY_CHARS], "items": clean}


def merge(items, archive, result, now, days):
    """Apply a validated result to active items; pure. Returns (active, archive, report)."""
    by_id = {i["id"]: dict(i) for i in items}
    report = {"new": [], "updated": [], "reused": [], "dropped": [], "promoted": []}
    archived = list(archive)
    for entry in result["items"]:
        item = by_id.get(entry.get("id"))
        if entry["action"] == "drop":
            if item:
                archived.append({**by_id.pop(item["id"]), "archived_at": now, "archive_reason": "dropped"})
                report["dropped"].append(item["text"])
            continue
        if entry["action"] == "new" or (entry["action"] == "update" and not item):
            fresh = {"id": "m_" + new_id()[:12], "text": entry["text"], "tier": entry["tier"], "created_at": now,
                     "last_used": now, "reuses": 0}
            by_id[fresh["id"]] = fresh
            report["new"].append(fresh["text"])
            continue
        if not item:
            continue  # A kept item that expired meanwhile stays archived.
        if entry["action"] == "update":
            item.update(text=entry["text"], tier="long" if item["tier"] == "long" else entry["tier"])
            report["updated"].append(item["text"])
        else:
            report["reused"].append(item["text"])
        item.update(last_used=now, reuses=item.get("reuses", 0) + 1)
        if item["tier"] == "short" and item["reuses"] >= PROMOTE_AFTER:
            item.update(tier="long", promoted_at=now)
            report["promoted"].append(item["text"])
        by_id[item["id"]] = item
    active, archived, expired = expire(list(by_id.values()), archived, now, days)
    report["expired"] = [i["text"] for i in expired]
    return active, archived, report


class Compactor:
    """Chat compaction: a Compact click or the automatic threshold starts one background job per chat that makes one
    model call and applies the result itself (no proposal, no confirm step)."""

    def __init__(self, chat):
        self.chat, self.db = chat, chat.db
        self.jobs = {}  # "compact:" + chat context -> the running background job; one per chat
        self.sizes = {}  # conversation id -> (last rowid, characters)
        self.checked = 0.0
        self.timeout, self.wait, self.poll = TIMEOUT_SECONDS, WAIT_SECONDS, POLL_SECONDS
        self.workspace = self.db.path.parent / "chat-compaction-workspace"
        self.workspace.mkdir(exist_ok=True, mode=0o700)

    # Chat state

    def busy(self, conversation_id):
        """A turn is queued, running, held or recovering in this chat: compaction never runs mid-turn."""
        if any(job_conversation == conversation_id for job_conversation in self.chat_jobs()):
            return True
        if any(json.loads(r["value"]).get("conversation_id") == conversation_id
               for r in self.db.all("SELECT value FROM settings WHERE key LIKE 'chat_held:%'")):
            return True
        return bool(self.db.one(
            """SELECT 1 FROM messages m LEFT JOIN executions e ON e.id=m.execution_id WHERE m.conversation_id=?
            AND (m.status IN ('queued','streaming','waiting','held') OR (e.id IS NOT NULL AND e.finished_at IS NULL))
            UNION ALL SELECT 1 FROM executions WHERE agent_id='manager' AND conversation_id=? AND finished_at IS NULL
            LIMIT 1""", (conversation_id, conversation_id)))

    def chat_jobs(self):
        for execution_id in list(self.chat.jobs):
            row = self.db.one("SELECT conversation_id FROM executions WHERE id=?", (execution_id,))
            if row:
                yield row["conversation_id"]

    def transcript(self, conversation_id):
        """Owner messages and the manager's saved replies only: no reasoning, tool output or internal notices."""
        from .chat import hidden_message
        rows = self.db.all("SELECT id,role,content,created_at FROM messages WHERE conversation_id=? AND role IN "
                           "('user','assistant') AND status='completed' AND NOT " + hidden_message()
                           + " ORDER BY rowid", (conversation_id,))
        return [r for r in rows if r["content"].strip()]

    def size(self, conversation_id):
        """Characters of the chat history the manager carries: every owner message and reply in the current chat."""
        from .chat import hidden_message
        last = self.db.one("SELECT max(rowid) AS position FROM messages WHERE conversation_id=?", (conversation_id,))
        cached = self.sizes.get(conversation_id)
        if cached and cached[0] == last["position"]:
            return cached[1]
        row = self.db.one("SELECT COALESCE(sum(length(history_text(content))),0) AS chars FROM messages WHERE "
                          "conversation_id=? AND role IN ('user','assistant') AND NOT " + hidden_message(),
                          (conversation_id,))
        self.sizes[conversation_id] = (last["position"], row["chars"])
        return row["chars"]

    # Model call

    def prompt(self, project_id, messages):
        items, archived = sweep(self.db, project_id)
        chat, used = [], 0
        for row in reversed(messages):
            content = row["content"] if len(row["content"]) <= MESSAGE_CHARS else (
                row["content"][:MESSAGE_CHARS // 2] + " [...] " + row["content"][-MESSAGE_CHARS // 2:])
            if used + len(content) > INPUT_CHARS:
                break
            used += len(content)
            chat.append({"role": "owner" if row["role"] == "user" else "manager", "text": content,
                         "at": datetime.fromtimestamp(row["created_at"], timezone.utc).strftime("%Y-%m-%d %H:%M")})
        chat.reverse()
        prompt = {
            "existing_memory": {"long_term": [{"id": i["id"], "text": i["text"]} for i in items if i["tier"] == "long"],
                                "short_term": [{"id": i["id"], "text": i["text"], "reuses": i.get("reuses", 0)}
                                               for i in items if i["tier"] == "short"]},
            "chat": chat,
            "earlier_messages_omitted": len(messages) - len(chat),
        }
        if project_id:
            from .project_memory import ProjectMemory
            prompt["project_brief"] = ProjectMemory(self.db, self.chat.registry).snapshot(project_id)["brief"]
        return prompt, {i["id"] for i in items}

    async def generate(self, project_id, messages):
        """One model call, bounded by the timeout; returns a validated result. Never changes anything."""
        server = self.chat.server
        prompt, known = self.prompt(project_id, messages)
        thread = turn_id = None
        done = False

        async def run():
            nonlocal thread, turn_id, done
            await server.start()
            chosen = route(self.db, server, project_id)
            thread = await server.thread(self.workspace, INSTRUCTIONS, None, chosen["model"])
            queue = asyncio.Queue()
            server.queues[thread] = queue
            turn = await server.request("turn/start", {"threadId": thread, "input": [{"type": "text",
                                        "text": encode(prompt)}], **chosen, "serviceTierForTurn": "default"})
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
                    if len(output) > 200000:
                        raise ValueError("Compaction output exceeds its safety limit")
                if method == "turn/completed":
                    done = True
                    if params["turn"]["status"] != "completed":
                        detail = (params["turn"].get("error") or {}).get("message", "")
                        raise ValueError("Compaction did not complete" + (": " + str(detail) if detail else ""))
                    return validate(parse(params.get("reconciled_output", output)), known)

        try:
            return await asyncio.wait_for(run(), self.timeout)
        except asyncio.TimeoutError:
            raise ValueError(f"Compaction timed out after {self.timeout} seconds") from None
        finally:
            if thread:
                server.queues.pop(thread, None)
                if turn_id and not done:
                    try:
                        await server.interrupt(thread, turn_id)
                    except Exception:
                        pass  # Best effort: the turn is abandoned either way.

    # Starting (manual Compact click and automatic threshold share one background path)

    def key(self, project_id):
        return "compact:" + context_name(project_id)

    def notice(self, project_id, text, state, conn=None):
        value = json.dumps({"text": text, "state": state, "at": time.time()})
        sql = "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        if conn is not None:
            conn.execute(sql, (NOTICE + context_name(project_id), value))
        else:
            with self.db.transaction() as own:
                own.execute(sql, (NOTICE + context_name(project_id), value))

    def start(self, project_id):
        """Compact click: start the background job and return at once. The result is applied automatically."""
        conversation = self.chat.conversation(project_id)
        if self.key(project_id) in self.jobs:
            return {"status": "running", "already_running": True,
                    "message": "A compaction is already running for this chat"}
        if not self.launch(project_id, conversation["id"], automatic=False):
            raise ValueError("This chat has nothing to compact yet")
        return {"status": "running", "already_running": False, "message": RUNNING_TEXT}

    def launch(self, project_id, conversation_id, automatic):
        """Capture the chat now (the summary covers it up to its last message) and compact it in the background."""
        messages = self.transcript(conversation_id)
        if not messages:
            return False
        key = self.key(project_id)
        self.notice(project_id, RUNNING_TEXT, "running")
        task = asyncio.create_task(self.run(project_id, conversation_id, messages, automatic))
        self.jobs[key] = task
        task.add_done_callback(lambda _: self.jobs.pop(key, None))
        return True

    def current_notice(self, project_id):
        """The chat's compaction notice; a "running" one left by a restart (no live job) reads as interrupted."""
        notice = self.db.setting(NOTICE + context_name(project_id))
        if notice and notice.get("state") == "running" and self.key(project_id) not in self.jobs:
            notice = {**notice, "state": "failed",
                      "text": "Chat compaction was interrupted by a restart; the chat is unchanged. Use Compact to retry."}
        return notice

    def status(self, project_id):
        return {"running": self.key(project_id) in self.jobs, "notice": self.current_notice(project_id)}

    async def run(self, project_id, conversation_id, messages, automatic):
        """One model call, then apply once the chat is between turns. Any failure leaves the chat unchanged."""
        try:
            result = await self.generate(project_id, messages)
            deadline = time.monotonic() + self.wait
            while self.busy(conversation_id):  # messages sent meanwhile: apply between turns, keep them
                if time.monotonic() > deadline:
                    raise ValueError(f"the chat stayed busy for {int(self.wait)} seconds after the summary was ready")
                await asyncio.sleep(self.poll)
            self.apply(project_id, conversation_id, messages[-1]["id"], result, automatic=automatic)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            label = "Automatic chat compaction" if automatic else "Chat compaction"
            text = f"{label} failed; the chat is unchanged ({str(error)[:200]}). Use Compact to retry."
            self.notice(project_id, text, "failed")
            with self.db.transaction() as conn:
                self.db.event(conn, "chat.compaction_failed", {"conversation_id": conversation_id,
                              "automatic": automatic, "error": str(error)[:500]}, project_id)

    # Applying

    def apply(self, project_id, conversation_id, last_message_id, result, title="", automatic=False):
        """One transaction: archive the chat up to and including `last_message_id` (what the summary covers), update
        memory, post the summary; newer messages stay in the chat after the summary. Refuses mid-turn."""
        title = " ".join(title.split())[:120]
        now = time.time()
        if self.busy(conversation_id):
            raise ValueError("Wait for the response to finish or stop it before compacting this chat")
        with self.db.transaction() as conn:
            conversation = conn.execute("SELECT * FROM conversations WHERE context=?",
                                        (context_name(project_id),)).fetchone()
            if not conversation or conversation["id"] != conversation_id:
                raise ValueError("the chat was cleared or archived meanwhile")
            cut = conn.execute("SELECT rowid AS position FROM messages WHERE id=? AND conversation_id=?",
                               (last_message_id, conversation_id)).fetchone()
            if not cut:
                raise ValueError("the chat was cleared or archived meanwhile")
            if not title:
                project = self.chat.registry.get(project_id) if project_id else None
                label = (project["display_name"] or project["name"]) if project else "All Projects"
                title = f"{label} · {datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d %H:%M UTC} · before compaction"
            archive_id, count = self.chat.archive_messages(conn, conversation, project_id, title, now,
                                                           cut["position"] + 1)
            kept = conn.execute("SELECT count(*) AS n FROM messages WHERE conversation_id=?",
                                (conversation_id,)).fetchone()["n"]
            if kept:
                # Chat order is rowid order. The newer messages keep their rows (ids, positions, cursors); the archived
                # rows are re-appended in order, unchanged, so the summary can take the freed position before them.
                moved = conn.execute("SELECT id,conversation_id,role,content,status,execution_id,created_at FROM "
                                     "messages WHERE conversation_id=? ORDER BY rowid", (archive_id,)).fetchall()
                conn.execute("DELETE FROM messages WHERE conversation_id=?", (archive_id,))
                conn.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", [tuple(r) for r in moved])
            active, archive = read_row(conn, project_id, ACTIVE), read_row(conn, project_id, ARCHIVE)
            items, archived, report = merge(active["items"], archive["items"], result, now,
                                            load_settings(self.db)["short_term_days"])
            write_row(conn, project_id, ACTIVE, {"items": items, "compactions": active.get("compactions", 0) + 1})
            write_row(conn, project_id, ARCHIVE, {"items": archived})
            long_count = sum(1 for i in items if i["tier"] == "long")
            notice = (f"Chat compacted: {len(items)} items kept ({long_count} long-term, {len(items) - long_count} "
                      f"short-term). The full previous chat ({count} messages) is archived as “{title}”.")
            if kept:
                notice += f" {kept} newer messages sent during compaction stay in the chat."
            summary = notice + "\n\n**Summary:** " + result["summary"]
            message_id = new_id()
            # Readers order by rowid or by (created_at, rowid): the summary sorts before the newer messages in both.
            first = conn.execute("SELECT min(created_at) AS at FROM messages WHERE conversation_id=?",
                                 (conversation_id,)).fetchone()["at"]
            conn.execute("INSERT INTO messages(rowid,id,conversation_id,role,content,status,execution_id,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?)", (cut["position"] if kept else None, message_id, conversation_id,
                                                     "assistant", summary, "completed", None, min(now, first or now)))
            conn.execute("DELETE FROM settings WHERE key=?", (AUTO_STATE + conversation_id,))
            self.notice(project_id, notice, "done", conn)
            self.db.event(conn, "chat.compacted", {"conversation_id": conversation_id, "automatic": automatic,
                          "archive_conversation_id": archive_id, "title": title, "message_count": count,
                          "kept_messages": kept, "kept": len(items),
                          "report": {k: len(v) for k, v in report.items()}}, project_id)
        self.sizes.pop(conversation_id, None)
        return {"result": "accepted", "message": notice, "conversation_id": conversation_id,
                "archive_conversation_id": archive_id, "summary_message_id": message_id, "kept": len(items)}

    # Automatic compaction

    def due(self):
        """Current chats past their threshold, idle, not yet attempted at this crossing, not already compacting."""
        threshold = load_settings(self.db)["threshold_tokens"] * CHARS_PER_TOKEN  # chars
        for conversation in self.db.all(
                "SELECT * FROM conversations WHERE context=COALESCE(project_id,'global') AND (project_id IS NULL "
                "OR project_id IN (SELECT id FROM projects WHERE status!='removed'))"):
            if ("compact:" + conversation["context"]) in self.jobs:
                continue
            chars = self.size(conversation["id"])
            state = self.db.setting(AUTO_STATE + conversation["id"]) or {}
            if chars < max(threshold, state.get("next_at", 0)) or self.busy(conversation["id"]):
                continue
            yield conversation, chars, threshold

    def check(self, now=None):
        """Scheduler tick hook: start at most one automatic compaction per due chat, between turns only."""
        now = time.monotonic() if now is None else now
        if now - self.checked < CHECK_SECONDS:
            return
        self.checked = now
        for conversation, chars, threshold in list(self.due()):
            # Recorded before the model call: one attempt per threshold crossing, even across a crash.
            self.db.set_setting(AUTO_STATE + conversation["id"], {"next_at": chars + threshold, "at": time.time()})
            self.launch(conversation["project_id"], conversation["id"], automatic=True)

    async def close(self):
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)

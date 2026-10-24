"""Deterministic retry policy and native outcome reconciliation; no inference here."""

import asyncio
import errno
import json
import os
import random
import signal
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import isolation
from .codex import CodexError, process_start
from .db import new_id


RETRY_SECONDS = 300


@dataclass
class Failure:
    kind: str
    retry_at: float | None = None


USAGE_LIMITS = {"usageLimitExceeded", "rateLimitExceeded", "usage_limit_exceeded", "rate_limit_exceeded",
                "insufficient_quota", "slow_down"}
def usage_limit(error):
    """True when the provider reported a usage or rate limit."""
    details = error.details if isinstance(error, CodexError) else error if isinstance(error, dict) else {}
    data = details.get("data") if isinstance(details.get("data"), dict) else {}
    info = details.get("codexErrorInfo") or data.get("codexErrorInfo") or data.get("code") or details.get("code")
    status = details.get("httpStatusCode") or data.get("httpStatusCode") or details.get("status")
    if isinstance(info, dict):
        name = next(iter(info), "")
        value = info.get(name)
        status = value.get("httpStatusCode") if isinstance(value, dict) else status
        info = name
    message = str(details.get("message", str(error))).lower()
    return (info in USAGE_LIMITS or status == 429
            or (not info or info in {"other", -32000}) and any(s in message for s in ("usage limit", "rate limit", "quota exceeded")))


def limit_failure(error):
    """A usage limit waits a fixed 5 minutes like a task (no backoff, reset time not consulted); else classify()."""
    if usage_limit(error):
        return Failure("usage_limit", time.time() + RETRY_SECONDS)
    return classify(error)


def waiting_text(failure):
    label = "provider usage limit" if failure.kind == "usage_limit" else "connection"
    when = datetime.fromtimestamp(failure.retry_at, timezone.utc).strftime("%H:%M UTC") if failure.retry_at else ""
    return f"Waiting for the {label}; retrying at {when}" if when else f"Waiting for the {label}"


def classify(error):
    """Prefer native enums, HTTP status and OS codes. Unknown errors are permanent."""
    if isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)):
        return Failure("connection")
    if isinstance(error, OSError) and error.errno in {
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EPIPE,
        errno.ENETDOWN,
    }:
        return Failure("connection")
    details = error.details if isinstance(error, CodexError) else error if isinstance(error, dict) else {}
    data = details.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    info = details.get("codexErrorInfo") or data.get("codexErrorInfo") or data.get("code") or details.get("code")
    status = details.get("httpStatusCode") or data.get("httpStatusCode") or details.get("status")
    if isinstance(info, dict):
        name = next(iter(info), "")
        value = info.get(name)
        status = value.get("httpStatusCode") if isinstance(value, dict) else status
        info = name
    if info in {
        "unauthorized",
        "badRequest",
        "contextWindowExceeded",
        "sessionBudgetExceeded",
        "cyberPolicy",
        "misalignmentPolicyViolation",
        "tooManyDenials",
        -32600,
        -32601,
        -32602,
    } or status in {400, 401, 403, 404}:
        return None
    if (
        info
        in {
            "usageLimitExceeded",
            "rateLimitExceeded",
            "usage_limit_exceeded",
            "rate_limit_exceeded",
            "insufficient_quota",
            "slow_down",
        }
        or status == 429
    ):
        return None
    if info in {
        "transportLost",
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
        "internalServerError",
        "flexUnavailable",
        "serverOverloaded",
        "server_is_overloaded",
    } or status in {408, 500, 502, 503, 504}:
        return Failure("connection", reset_time({**data, **details}))
    # Compatibility for older servers that returned only a message; deliberately narrow.
    message = str(details.get("message", str(error))).lower()
    if not info or info in {"other", -32000}:
        if any(s in message for s in ("usage limit", "rate limit", "quota exceeded")):
            return None
        if any(
            s in message
            for s in (
                "connection reset",
                "connection lost",
                "connection closed",
                "network is unreachable",
                "temporary failure in name resolution",
                "connection timed out",
                "temporary manager outage",
            )
        ):
            return Failure("connection")
    return None


def reset_time(details):
    now = time.time()
    for key in ("retryAt", "retry_at", "resetsAt", "reset_at"):
        value = details.get(key)
        if isinstance(value, (int, float)) and value > now:
            return float(value)
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                if value > now:
                    return value
            except ValueError:
                pass
    headers = details.get("headers") or {}
    value = (
        details.get("retryAfter")
        or details.get("retry_after")
        or headers.get("retry-after")
        or headers.get("Retry-After")
    )
    if value is not None:
        try:
            return now + max(0, float(value))
        except (ValueError, TypeError):
            try:
                return max(now, parsedate_to_datetime(value).timestamp())
            except (ValueError, TypeError):
                pass
    return None


def delay(count, kind):
    return min(300, 5 * 2 ** min(count - 1, 16)) * random.uniform(0.8, 1.0)


def defer(db, execution_id, failure, error="", output=None):
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
        if not row or row["finished_at"] is not None:
            return
        count = row["retry_count"] + 1
        if failure.kind == "usage_limit":
            error = waiting_text(failure)
        retry_at = (
            max(time.time() + 1, failure.retry_at) if failure.retry_at else time.time() + delay(count, failure.kind)
        )
        conn.execute(
            """UPDATE executions SET status='recovering',retry_at=?,retry_count=?,recovery_reason=?,error=?,
                     output=COALESCE(?,output) WHERE id=?""",
            (retry_at, count, failure.kind, error[:2000], output, execution_id),
        )
        conn.execute("UPDATE messages SET status='waiting' WHERE execution_id=? AND role='assistant'", (execution_id,))
        db.event(
            conn,
            "recovery.scheduled",
            {"reason": failure.kind, "retry_at": retry_at, "known_reset": failure.retry_at, "attempt": count},
            row["project_id"],
            row["task_id"],
            execution_id,
        )


def owned_alive(row):
    return bool(row["pid"] and row["process_start"] and process_start(row["pid"]) == row["process_start"])


def reap_orphan(row, force=False):
    """Only signal a verified, dedicated App Server group from our durable receipt."""
    if not owned_alive(row):
        return False
    pid = row["pid"]
    sig = signal.SIGKILL if force or (row["recovery_reason"] == "interrupted" and row["retry_count"]) else signal.SIGTERM
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        # An isolated worker (ai_manager/isolation.py) is tagged with its execution id; its processes belong to the
        # worker user, so only the fixed launcher can signal them.
        found = isolation.detect() if row["id"].encode() in args else None
        if found:
            found.signal(row["id"], sig, wait=False)
        if os.getpgid(pid) != pid or b"app-server" not in args or Path(f"/proc/{pid}").stat().st_uid != os.getuid():
            return True  # Cannot prove ownership of the whole group; keep exclusivity.
        os.killpg(pid, sig)
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        return True
    return True  # Recheck identity on the next eligible scheduler pass.


async def inspect(server, row):
    if not row["thread_id"] or row["status"] == "queued":
        return None
    await server.start()
    saved = await server.request("thread/read", {"threadId": row["thread_id"], "includeTurns": True}, timeout=10)
    turns = saved["thread"].get("turns", [])
    if row["turn_id"]:
        turn = next((t for t in turns if t["id"] == row["turn_id"]), None)
        if turn:
            return turn
        raise CodexError("Persisted native turn is missing; outcome uncertain", {"code": "uncertain"})
    for turn in reversed(turns):
        for item in turn.get("items", []):
            if row["launch_key"] and item.get("clientId") == row["launch_key"]:
                return turn
    for turn in reversed(turns):
        for item in turn.get("items", []):
            if item.get("type") != "userMessage":
                continue
            text = "".join(c.get("text", "") for c in item.get("content", []))
            if not item.get("clientId") and row["launch_input"]:
                try:
                    same = json.loads(text) == json.loads(row["launch_input"])
                except ValueError:
                    same = text == row["launch_input"]
                if same:
                    return turn
    if row["launch_key"] and not (row["launch_key"] == "not-started" and row["status"] == "starting"):
        # Original owned process is gone and its authoritative history has no accepted input.
        return None
    if row["status"] == "starting" or (row["recovery_reason"] and row["thread_id"] and not row["launch_input"]):
        raise CodexError(
            "Launch outcome uncertain; retained thread must be inspected before continuation", {"code": "uncertain"}
        )
    return None


def turn_output(turn):
    return "\n".join(x.get("text", "") for x in turn.get("items", []) if x.get("type") == "agentMessage")


def turn_error(db, execution_id, turn):
    if turn.get("error"):
        return turn["error"]
    # Some transports expose details in an error notification only. It is already
    # durable in events, so recover the exact turn's last error without inference.
    row = db.one(
        """SELECT payload FROM events WHERE execution_id=? AND kind='error'
        AND json_extract(history_text(payload),'$.turnId')=? ORDER BY seq DESC LIMIT 1""",
        (execution_id, turn["id"]),
    )
    return (json.loads(row["payload"]).get("error") or {}) if row else {}


def launching(db, execution_id, prompt):
    with db.transaction() as conn:
        key = f"linko:{execution_id}:{new_id()}"
        conn.execute(
            "UPDATE executions SET status='starting',turn_id=NULL,launch_key=?,launch_input=?,retry_at=NULL WHERE id=?",
            (key, prompt, execution_id),
        )
    return key


class SilentObserver:
    """Silence is only a reason to read history, never evidence of failure."""

    def __init__(self, db):
        self.db = db
        self.checked = {}

    async def observe(self, attached):
        now = time.time()
        self.checked = {key: at for key, at in self.checked.items() if key in attached}
        for execution_id, server in attached.items():
            row = self.db.one("SELECT * FROM executions WHERE id=?", (execution_id,))
            if not row or row["finished_at"] is not None or row["status"] != "running" or not row["turn_id"]:
                continue
            if now - self.checked.get(execution_id, row["started_at"]) < 60:
                continue
            activity = self.db.one(
                """SELECT created_at AS at FROM events WHERE execution_id=? AND
                (kind='chat.delta' OR kind LIKE 'item/%' OR kind IN ('turn/started','chat.started','worker.started','error'))
                ORDER BY seq DESC LIMIT 1""",
                (execution_id,),
            )
            activity = activity["at"] if activity else row["started_at"]
            if now - max(activity, self.checked.get(execution_id, row["started_at"])) < 60:
                continue
            self.checked[execution_id] = now
            if self.db.one("SELECT 1 FROM questions WHERE execution_id=? AND status='pending'", (execution_id,)):
                continue
            try:
                # Bounded metadata read only; do not reserve a gate or touch ownership.
                turn = await asyncio.wait_for(inspect(server, row), 10)
                current = self.db.one("SELECT * FROM executions WHERE id=?", (execution_id,))
                queue = server.queues.get(row["thread_id"])
                if not current or current["finished_at"] is not None or current["turn_id"] != row["turn_id"]:
                    continue
                if turn and turn["status"] in {"completed", "failed", "interrupted"} and queue is not None:
                    # The attached runner retains its cleanup, retry, stop and delivery protections.
                    queue.put_nowait(
                        {
                            "method": "turn/completed",
                            "params": {
                                "turn": turn,
                                "reconciled_output": turn_output(turn),
                            },
                        }
                    )
                outcome = turn["status"] if turn else "uncertain"
            except Exception as error:
                # An inspection error says nothing about the running turn.
                outcome = "inspection_failed:" + type(error).__name__
            with self.db.transaction() as conn:
                self.db.event(
                    conn, "recovery.observed", {"outcome": outcome}, row["project_id"], row["task_id"], execution_id
                )
            return  # At most one bounded read per scheduler tick.

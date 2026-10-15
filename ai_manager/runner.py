"""Queue runner: the one place that starts, resumes and unsticks task work. See README.md ("How work runs").

It needs no chat and no owner message. A tick (once a second, from the scheduler loop) does three things:
  1. once after startup: whatever was running when the service stopped is resumed (nothing we started is still alive),
     and stale backoff times are forgotten, so a restart or reboot always retries at once;
  2. every minute: the watchdog fails or requeues a dead or hung worker, expires a stale deploy request, and fails queued
     work of a removed project;
  3. starts the oldest eligible queued task (not held, not waiting for its retry time, project active), one at a time.
A provider usage limit or a lost connection puts the task back in the queue with a retry time: the provider's reset time
when known, else bounded exponential backoff. Repair-round counters live on the task row and are never touched here.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from .codex import CodexError
from .recovery import Failure, classify, owned_alive, reap_orphan, reset_time

WATCHDOG_SECONDS, STALL_SECONDS, MAX_WAITS = 60, 1800, 10
USAGE_LIMITS = {"usageLimitExceeded", "rateLimitExceeded", "usage_limit_exceeded", "rate_limit_exceeded",
                "insufficient_quota", "slow_down"}
log = logging.getLogger(__name__)


def plain(error):
    return (str(error) or type(error).__name__)[:600]


def usage_limit(error):
    """Failure('usage', provider reset time or None) when the provider reported a usage/rate limit, else None."""
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
    if (info in USAGE_LIMITS or status == 429
            or (not info or info in {"other", -32000}) and any(s in message for s in ("usage limit", "rate limit", "quota exceeded"))):
        return Failure("usage", reset_time({**data, **details}))
    return None


def backoff(kind, waits, reset):
    """Seconds to wait: until the provider's reset time when known, else bounded exponential backoff."""
    if reset:
        return min(12 * 3600, max(60, reset - time.time() + 30))
    return min(3600, 300 * 2 ** waits) if kind == "usage" else min(600, 30 * 2 ** waits)


class Runner:
    def __init__(self, core, stall=STALL_SECONDS):
        self.core, self.stall = core, stall
        self.started, self.watchdog_at = False, 0.0

    async def tick(self):
        core = self.core
        if not self.started:
            self.started = True
            self.startup()
        if time.monotonic() >= self.watchdog_at:
            self.watchdog_at = time.monotonic() + WATCHDOG_SECONDS
            await self.watchdog()
        while not core.pipeline.pending() and (task := core.tasks.claim()):
            core.launch(task)

    def startup(self):
        """The service just started: resume interrupted work without penalty and drop backoff that may be stale."""
        core = self.core
        self.reconcile_leases()
        for task in core.db.all("SELECT * FROM tasks WHERE state='running'"):
            self.stop_orphan(task["lease_owner"])
            self.give_back(task, "Resumed after a restart", penalty=False)
        core.db.run("UPDATE tasks SET retry_at=NULL WHERE state='queued' AND retry_at IS NOT NULL")

    def stop_orphan(self, eid):
        row = self.core.db.one("SELECT * FROM executions WHERE id=?", (eid,)) if eid else None
        if row and owned_alive(row):
            reap_orphan(row, force=True)

    def give_back(self, task, why, penalty=True):
        """Requeue a running task once (a hung or dead worker gets a single extra try), else fail it with a reason."""
        core, eid = self.core, task["lease_owner"]
        if eid is None:
            core.db.run("UPDATE tasks SET state='failed',blocker=?,updated_at=? WHERE id=? AND state='running'",
                        (why + " with no run record; stopped", time.time(), task["id"]))
        elif not penalty:
            core.requeue(eid, why)
        elif task["requeues"] < 1:
            core.requeue(eid, why + "; requeued once", None, requeues=task["requeues"] + 1)
        else:
            core.end(eid, "failed", why + " a second time; stopped")

    def failed(self, task, eid, error):
        """A run ended with an error: wait in the queue for a usage limit or a lost connection, else fail plainly."""
        limit = usage_limit(error)
        failure = None if limit else classify(error)
        if not (limit or failure):
            log.warning("task %s failed: %s", task["id"], error)
            return self.core.end(eid, "failed", plain(error))
        kind, reset = ("usage", limit.retry_at) if limit else ("network", failure.retry_at)
        waits = self.core.tasks.get(task["id"])["waits"]
        label = "provider usage limit" if kind == "usage" else "connection"
        if waits >= MAX_WAITS:
            return self.core.end(eid, "failed", f"The {label} did not clear after {waits} waits; stopped. Last error: {plain(error)[:200]}")
        retry_at = time.time() + backoff(kind, waits, reset)
        when = datetime.fromtimestamp(retry_at, timezone.utc).strftime("%H:%M UTC")
        self.core.requeue(eid, f"Waiting for the {label}; retrying at {when}", retry_at, waits=waits + 1)

    def activity(self, eid):
        db = self.core.db
        row = db.one("SELECT max(created_at) AS at FROM events WHERE execution_id=?", (eid,))
        started = db.one("SELECT started_at FROM executions WHERE id=?", (eid,))
        return max(row["at"] or 0, started["started_at"] if started else 0)

    def reconcile_leases(self):
        """Repair inconsistent receipts only after both the runner and owned process are gone.

        Normal completion/requeue is atomic. Legacy or damaged rows can still
        refer to a missing/terminal execution, which end/release cannot consume.
        Retain the workspace and stage; retry once instead of inventing success.
        """
        core = self.core
        for task in core.db.all("SELECT * FROM tasks WHERE lease_owner IS NOT NULL"):
            eid = task["lease_owner"]
            job = core.jobs.get(eid)
            if (job and not job.done()) or eid in core.servers:
                continue
            execution = core.db.one("SELECT * FROM executions WHERE id=?", (eid,))
            if execution and (execution["finished_at"] is None or owned_alive(execution)):
                continue
            with core.db.transaction() as conn:
                current = conn.execute("SELECT * FROM tasks WHERE id=? AND lease_owner=?", (task["id"], eid)).fetchone()
                if not current:
                    continue
                state, retries = current["state"], current["requeues"]
                reason = "Recovered abandoned lease with missing or terminal run; workspace retained"
                if state == "running":
                    state = "queued" if retries < 1 else "failed"
                    retries += 1
                conn.execute("UPDATE tasks SET state=?,lease_owner=NULL,requeues=?,"
                             "blocker=?,revision=revision+1,updated_at=? WHERE id=? AND lease_owner=?",
                             (state, retries, reason, time.time(), task["id"], eid))
                conn.execute("UPDATE questions SET status='expired' WHERE execution_id=? AND status='pending'", (eid,))
                core.db.event(conn, "task.lease_recovered", {"state": state}, task["project_id"], task["id"], eid)

    async def watchdog(self):
        """Nothing may stay stuck silently: a dead or hung worker, a stale deploy request, a removed project."""
        core = self.core
        core.pipeline.expire()
        self.reconcile_leases()
        for task in core.db.all("SELECT * FROM tasks WHERE state='running'"):
            eid, job = task["lease_owner"], core.jobs.get(task["lease_owner"])
            if job and not job.done():
                waiting = core.db.one("SELECT 1 FROM questions WHERE execution_id=? AND status='pending'", (eid,))
                if waiting or time.time() - self.activity(eid) < self.stall:
                    continue
                if server := core.servers.get(eid):
                    server.terminate()
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                why = "Worker stalled (no activity for %d minutes)" % (self.stall // 60)
            else:
                why = "Worker stopped unexpectedly"
            self.stop_orphan(eid)
            self.give_back(task, why)
        with core.db.transaction() as conn:
            for task in conn.execute("""SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id
                    WHERE t.state='queued' AND p.status='removed'""").fetchall():
                core.tasks.fail_queued(conn, task, "Project was removed")

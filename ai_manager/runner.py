"""Queue runner: the one place that starts, resumes and unsticks task work. See README.md ("How work runs").

It needs no chat and no owner message. A tick (once a second, from the scheduler loop) does three things:
  1. once after startup: whatever was running when the service stopped is resumed (nothing we started is still alive),
     and retry times are forgotten, so a restart or reboot always retries at once;
  2. every minute: the watchdog fails or requeues a dead or hung worker, expires a stale deploy request, fails queued
     work of a removed project, and reports an idle queue that has eligible work but does not start it;
  3. starts, for every project with nothing running, its oldest eligible queued task (not held, not waiting for its
     retry time, project active): one task at a time per project, different projects in parallel.
A provider usage limit or a lost connection never fails a task: it goes back in the queue and is tried again every
5 minutes, indefinitely (a limit that resets early is picked up by the next try). Repair-round counters live on the task
row and are never touched here.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from . import fence
from .codex import CodexError
from .recovery import classify, owned_alive, reap_orphan, usage_limit

WATCHDOG_SECONDS, STALL_SECONDS, RETRY_SECONDS, IDLE_STALL_SECONDS = 60, 1800, 300, 300
log = logging.getLogger(__name__)


def plain(error):
    return (str(error) or type(error).__name__)[:600]


class Runner:
    def __init__(self, core, stall=STALL_SECONDS):
        self.core, self.stall = core, stall
        self.started, self.watchdog_at = False, 0.0
        self.idle_since, self.reported = None, False  # monotonic time the queue first sat idle with eligible work

    async def tick(self):
        core = self.core
        if not self.started:
            self.started = True
            self.startup()
        if time.monotonic() >= self.watchdog_at:
            self.watchdog_at = time.monotonic() + WATCHDOG_SECONDS
            await self.watchdog()
        while not core.pipeline.pending() and (task := core.tasks.claim(core.pipeline.sync.ready)):
            core.pipeline.sync.consumed(task["project_id"])
            core.launch(task)
        core.pipeline.sync.tick()
        core.pipeline.backups.tick()
        self.idle_since = (self.idle_since or time.monotonic()) if self.starved() else None
        self.reported &= self.idle_since is not None

    def starved(self):
        """Eligible queued work in a project that has nothing running, and no deploy pending (a held or paused task, a
        running task of the same project or a deploy is a reason)."""
        core = self.core
        if core.pipeline.pending():
            return None
        return core.db.one("""SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.state='queued' AND t.held=0 AND p.status='active' AND COALESCE(t.retry_at,0)<=?
            AND NOT EXISTS(SELECT 1 FROM tasks r WHERE r.state='running' AND r.project_id=t.project_id)
            ORDER BY t.queued_at,t.rowid LIMIT 1""", (time.time(),))

    def startup(self):
        """The service just started: resume interrupted work without penalty and forget retry times that may be stale."""
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
        """A run ended with an error: a usage limit or lost connection waits in the queue, anything else fails plainly."""
        limit = usage_limit(error)
        if not (limit or classify(error)):
            log.warning("task %s failed: %s", task["id"], error)
            return self.core.end(eid, "failed", plain(error))
        retry_at = time.time() + RETRY_SECONDS
        when = datetime.fromtimestamp(retry_at, timezone.utc).strftime("%H:%M UTC")
        label = "provider usage limit" if limit else "connection"
        self.core.requeue(eid, f"Waiting for the {label}; retrying at {when}", retry_at)

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

    def report_idle_stall(self):
        """The queue sat idle with eligible work for too long: say why, on the task and in the events, once per stall."""
        if self.reported or self.idle_since is None or time.monotonic() - self.idle_since < IDLE_STALL_SECONDS:
            return
        core, task = self.core, self.starved()
        if not task:
            return
        try:
            with fence.hold(core.db.path.parent, exclusive=False):
                why = "the queue runner has not started it"
        except fence.Busy:
            why = "a deploy or merge holds the live checkout"
        self.reported = True
        reason = f"Queue idle for over {IDLE_STALL_SECONDS // 60} minutes with this task eligible: {why}"
        log.error("task %s: %s", task["id"], reason)
        with core.db.transaction() as conn:
            conn.execute("UPDATE tasks SET blocker=? WHERE id=? AND state='queued'", (reason, task["id"]))
            core.db.event(conn, "queue.stalled", {"reason": why}, task["project_id"], task["id"])

    async def watchdog(self):
        """Nothing may stay stuck silently: a dead or hung worker, a stale deploy request, a removed project, an idle queue."""
        core = self.core
        core.pipeline.expire()
        self.reconcile_leases()
        self.report_idle_stall()
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

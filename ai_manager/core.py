"""Task-execution core: one global sequential queue, one worker, durable repair rounds, a watchdog. See README.md.

States: queued -> running -> done | failed. `held` is a flag on a queued task (skipped until released); order is the
only dependency. A worker implements on an isolated branch, the application runs the permanent checks (the fixed smoke
suite plus the test files the branch added or changed), repairs happen in durable rounds (3, then +3 twice when the
checkpoint sees progress, hard cap 9), and a passing branch is merged into main, which asks the deploy script to deploy.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import fence
from .attachments import native_image
from .codex import RULES, AppServer, CodexError, TurnUsage, process_start
from .db import new_id
from .fence import Busy, main_branch
from .model_settings import RouteBlocked, assignment_route
from .pipeline import IDENTITY, Conflict, enabled, git
from .recovery import classify, delay, owned_alive, reap_orphan, turn_error, usage_limit

FIRST_ROUNDS, GRANT, HARD_CAP = 3, 3, 9
SMOKE_SECONDS, SUITE_SECONDS, PROBE_SECONDS = 180, 600, 60
WATCHDOG_SECONDS, STALL_SECONDS, MAX_USAGE_WAITS = 60, 1800, 8
WORKER_INSTRUCTIONS = RULES + "\nYou are a worker. The application runs the permanent checks and merges; you implement.\n"
TEST_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")
log = logging.getLogger(__name__)


# ---- checks: the only thing that can block delivery -------------------------------------------------------------
def execute(argv, cwd, env, timeout):
    """Run one command in its own process group; kill the whole group on timeout. Returns (code, output, timed_out)."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output, False
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        return -1, output or "", True


def changed_tests(root, base):
    """Permanent test files this branch added or changed (committed, modified or new). One-off probes never count."""
    paths = set(git(root, "diff", "--name-only", base).splitlines())
    paths |= set(git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    return sorted(p for p in paths if TEST_FILE.search(p) and (Path(root) / p).is_file())


def suite(name, argv, root, env, timeout):
    code, output, timed_out = execute(argv, root, env, timeout)
    if code in (0, 5):  # 5: nothing collected, so nothing to fail
        return None
    found = re.findall(r"^(?:FAILED|ERROR) (\S+)(?: - (.*))?$", output, re.M)
    failures = [f"{name}: {node}" + (f" - {message[:120]}" if message else "") for node, message in found]
    if timed_out:
        failures = [f"{name}: timed out after {timeout}s"]
    return {"check": name, "failures": failures or [f"{name}: exited with code {code}"], "output": output[-3000:]}


def unresolved(root):
    """Merge state left in the worktree: unresolved conflicts are a failing 'merge' check; a finished merge is committed."""
    conflicted = git(root, "diff", "--name-only", "--diff-filter=U").splitlines()
    if conflicted:
        return outcome([{"check": "merge", "failures": [f"merge: unresolved conflict in {p}" for p in conflicted], "output": ""}])
    if subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0:
        git(root, *IDENTITY, "commit", "--no-edit")
    return None


def interpreter(root):
    """The project's own virtualenv (kept in its shared checkout, not in the worktree) when it has one."""
    try:
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        candidate = common.parent / ".venv" / "bin" / "python"
        return str(candidate) if candidate.exists() else sys.executable
    except (ValueError, OSError, subprocess.SubprocessError):
        return sys.executable


def run_gate(root, base):
    """Fixed smoke suite plus the branch's own test files. Returns {ok, checks, failures, count, sig, output}."""
    root = Path(root)
    env = {**os.environ, "PYTHONPATH": str(root), "AI_MANAGER_SECURE_COOKIE": "0"}
    failed = []
    with tempfile.TemporaryDirectory(prefix="linko-gate-") as data:
        env["AI_MANAGER_DATA"] = data  # disposable runtime data; the real database is never reachable
        base_argv = [interpreter(root), "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"]
        if (root / "tests").is_dir() or (root / "test").is_dir():
            failed.append(suite("smoke", [*base_argv, "-m", "smoke"], root, env, SMOKE_SECONDS))
        tests = changed_tests(root, base)
        if tests:
            failed.append(suite("targeted", [*base_argv, *tests], root, env, SUITE_SECONDS))
    return outcome([f for f in failed if f])


def outcome(failed):
    failures = [line for f in failed for line in f["failures"]]
    normal = [re.sub(r"\d+", "#", line) for line in failures]
    return {"ok": not failed, "checks": [f["check"] for f in failed], "failures": failures[:30], "count": len(failures),
            "sig": hashlib.sha256("\n".join(sorted(normal)).encode()).hexdigest()[:16],
            "output": "\n".join(f["output"] for f in failed)[-3000:]}


def evaluate(history):
    """Checkpoint: ('continue'|'stop', plain reason). Progress = failing check changed or fewer failures."""
    now, before = history[-1], history[-2] if len(history) > 1 else None
    if not before or before.get("ok"):
        return "continue", "no earlier failing round to compare; granting 3 more rounds"
    if now["checks"] != before["checks"]:
        return "continue", f"the failing check changed from {'+'.join(before['checks'])} to {'+'.join(now['checks'])}"
    if now["count"] < before["count"]:
        return "continue", f"fewer failures ({before['count']} then {now['count']})"
    if now["count"] == before["count"] and now["sig"] != before["sig"]:
        return "continue", f"{'+'.join(now['checks'])} still fails but no longer the same way"
    return "stop", (f"No progress: {'+'.join(now['checks'])} failed the same way in consecutive rounds "
                    f"({now['count']} failure(s)); first: {now['failures'][0] if now['failures'] else 'unknown'}")


def plain(error):
    return (str(error) or type(error).__name__)[:600]


# ---- the queue --------------------------------------------------------------------------------------------------
class Tasks:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def record(row):
        row = dict(row)
        row["checkpoints"] = json.loads(row["checkpoints"])
        row["merge"] = json.loads(row["merge"]) if row["merge"] else None
        row["history"] = json.loads(row.pop("gate_history"))
        row["status"] = "held" if row["held"] and row["state"] == "queued" else row["state"]
        return row

    def get(self, task_id):
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise ValueError("Unknown task")
        selected = self.db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected' ORDER BY seq DESC LIMIT 1", (task_id,))
        return {**self.record(row), "worker_route": json.loads(selected["payload"]) if selected else None}

    @staticmethod
    def context(task):
        keys = ("id", "description", "acceptance", "status", "blocker", "lease_owner", "revision", "rounds", "round_limit",
                "workspace", "workspace_branch", "merge", "checkpoints", "retry_at")
        return {k: task.get(k) for k in keys} | {"worker_route": task.get("worker_route")}

    def list(self, project_id=None):
        rows = self.db.all(
            "SELECT * FROM tasks" + (" WHERE project_id=?" if project_id else "")
            + """ ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
            CASE WHEN state='queued' THEN queued_at END,created_at DESC,id""",
            (project_id,) if project_id else (),
        )
        archived = self.db.setting("archived_tasks", {})
        records = [self.record(row) for row in rows if row["id"] not in archived]
        for record in records:
            del record["history"]  # failure output tails stay out of the polled list; get() and the history view have them
        return records

    def add(self, project_id, description, origin_conversation=None, request_key=None, acceptance="", selected=None,
            model_route=None, todos=None):
        if not description.strip() or len(description) > 24000:
            raise ValueError("Objective must be 1–24000 characters")
        if not isinstance(acceptance, str) or len(acceptance) > 12000:
            raise ValueError("Acceptance criteria must be at most 12000 characters")
        now, task_id = time.time(), new_id()
        with self.db.transaction() as conn:
            previous = conn.execute(
                """SELECT id,project_id FROM tasks WHERE request_key=? OR id=(
                    SELECT task_id FROM events WHERE kind='task.reused' AND json_extract(payload,'$.request_key')=?
                    ORDER BY seq DESC LIMIT 1)""", (request_key, request_key)).fetchone() if request_key else None
            if not previous:
                previous = conn.execute(
                    """SELECT id,project_id FROM tasks WHERE project_id=? AND description=? AND origin_conversation IS ?
                    AND state IN ('queued','running') ORDER BY created_at DESC LIMIT 1""",
                    (project_id, description.strip(), origin_conversation)).fetchone()
                if previous and request_key:
                    self.db.event(conn, "task.reused", {"request_key": request_key}, project_id, previous["id"])
            if previous:
                if previous["project_id"] != project_id:
                    raise ValueError("Delegation belongs to another project")
                if todos:
                    from .todos import Todos
                    Todos(self.db).consume(conn, todos, project_id, previous["id"])
                return self.get(previous["id"])
            conn.execute(
                """INSERT INTO tasks(id,project_id,description,acceptance,created_at,updated_at,queued_at,state,
                origin_conversation,request_key) VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
                (task_id, project_id, description.strip(), acceptance, now, now, now, origin_conversation, request_key))
            self.db.event(conn, "task.created", {"objective": description}, project_id, task_id)
            if model_route:
                self.db.event(conn, "task.model_selected", model_route, project_id, task_id)
            if selected:
                self.db.event(conn, "task.context", selected, project_id, task_id)
            if todos:
                from .todos import Todos
                Todos(self.db).consume(conn, todos, project_id, task_id)
        return self.get(task_id)

    def control(self, task_id, action, revision=None, additions="", acceptance=""):
        """hold | release | continue | cancel (a queued task). Cancelling a running task goes through Core."""
        if action not in {"hold", "release", "continue", "cancel"}:
            raise ValueError("Unknown task action")
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or (revision is not None and task["revision"] != revision):
                raise ValueError("Task changed; refresh before editing")
            if task_id in self.db.setting("archived_tasks", {}):
                raise ValueError("Task was archived; its history is preserved")
            state, now, fields = task["state"], time.time(), {}
            if action == "hold" and (state != "queued" or task["held"]):
                raise ValueError("Only a queued task can be held")
            if action == "release" and not (state == "queued" and task["held"]):
                raise ValueError("Task is not held")
            if action == "cancel" and state not in {"queued"}:
                raise ValueError("Only a queued task can be cancelled here" if state == "running" else "Task already finished")
            if action == "continue" and state == "running":
                raise ValueError("Task is running; cancel it first or delegate the new work")
            if action == "continue" and state == "queued" and not (additions or acceptance or task["held"]):
                raise ValueError("Task is already queued")
            if action == "hold":
                fields = {"held": 1}
            elif action == "release":
                fields = {"held": 0}
            elif action == "cancel":
                fields = {"state": "failed", "blocker": "Cancelled by owner", "held": 0}
            else:
                text, extra = task["description"], task["acceptance"]
                if additions.strip() and additions.strip() not in text:
                    text += "\n\nAdditional instructions (preserve prior requirements):\n" + additions.strip()
                if acceptance.strip() and acceptance.strip() not in extra:
                    extra += ("\n\n" if extra else "") + acceptance.strip()
                if len(text) > 24000 or len(extra) > 12000:
                    raise ValueError("Added instructions exceed task limits; prior requirements remain intact")
                fields = {"description": text, "acceptance": extra, "held": 0}
                if state in {"done", "failed"}:  # counters stay: repair rounds are never reset
                    fields |= {"state": "queued", "queued_at": now, "blocker": "", "stage": "work", "requeues": 0,
                               "waits": 0, "retry_at": None, "gate_history": "[]", "open_round": 0}
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE tasks SET {sets},revision=revision+1,updated_at=? WHERE id=?", (*fields.values(), now, task_id))
            self.db.event(conn, "task." + action, {}, task["project_id"], task_id)
        return self.get(task_id)

    def claim(self):
        try:
            with fence.hold(self.db.path.parent, exclusive=False):
                return self._claim()
        except Busy:
            return None  # A deploy holds the fence: no new work starts until it finishes.

    def _claim(self):
        now, claimed = time.time(), None
        with self.db.transaction() as conn:
            for task in conn.execute("""SELECT t.*,p.path,p.status AS project_status FROM tasks t
                    JOIN projects p ON p.id=t.project_id WHERE t.state='queued' AND t.held=0
                    AND NOT EXISTS(SELECT 1 FROM tasks r WHERE r.state='running')
                    ORDER BY t.queued_at,t.rowid""").fetchall():
                if task["project_status"] == "removed":
                    self.fail_queued(conn, task, "Project was removed")
                    continue
                if task["project_status"] != "active":
                    continue  # paused or archived by the owner: skipped like a hold
                if task["retry_at"] and task["retry_at"] > now:
                    break  # strict order: the head waits for its retry time
                execution_id = new_id()
                conn.execute("UPDATE tasks SET state='running',blocker='',retry_at=NULL,lease_owner=?,revision=revision+1,"
                             "updated_at=? WHERE id=?", (execution_id, now, task["id"]))
                conn.execute("""INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,worktree,branch,launch_key)
                    VALUES(?,?,?,'execution','queued',?,?,?,'not-started')""",
                             (execution_id, task["id"], task["project_id"], now, task["workspace"] or task["path"],
                              task["workspace_branch"]))
                self.db.event(conn, "worker.queued", {}, task["project_id"], task["id"], execution_id)
                claimed = task["id"]
                break
        return self.get(claimed) if claimed else None

    def request_merge(self, task_id):
        """Owner asked to deploy this task now even though Auto-deploy is off: merge it (a finished task goes round again
        at the merge stage, without another AI turn)."""
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or not task["workspace_branch"]:
                raise ValueError("Task has no branch to merge")
            if (json.loads(task["merge"]) if task["merge"] else {}).get("state") == "merged":
                raise ValueError("Task is already merged")
            if task["state"] == "failed":
                raise ValueError("A failed task is not merged; continue it first")
            fields = {"merge_requested": 1}
            if task["state"] == "done":
                fields |= {"state": "queued", "stage": "merge", "queued_at": time.time(), "requeues": 0, "waits": 0, "blocker": ""}
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE tasks SET {sets},revision=revision+1,updated_at=? WHERE id=?", (*fields.values(), time.time(), task_id))
            self.db.event(conn, "task.merge_requested", {}, task["project_id"], task_id)
        return self.get(task_id)

    def fail_queued(self, conn, task, reason):
        conn.execute("UPDATE tasks SET state='failed',blocker=?,revision=revision+1,updated_at=? WHERE id=?",
                     (reason, time.time(), task["id"]))
        self.db.event(conn, "task.failed", {"reason": reason}, task["project_id"], task["id"])


# ---- the worker -------------------------------------------------------------------------------------------------
class Core:
    def __init__(self, db, registry, settings, pipeline, server_factory=AppServer, gate=run_gate, stall=STALL_SECONDS):
        self.db, self.registry, self.settings, self.pipeline = db, registry, settings, pipeline
        self.tasks, self.factory, self.gate, self.stall = Tasks(db), server_factory, gate, stall
        self.jobs, self.servers, self.cancelling = {}, {}, set()
        self.watchdog_at = 0.0

    # -- scheduling ------------------------------------------------------------------------------------------------
    async def tick(self):
        if time.monotonic() >= self.watchdog_at:
            self.watchdog_at = time.monotonic() + WATCHDOG_SECONDS
            await self.watchdog()
        while not self.pipeline.pending() and (task := self.tasks.claim()):
            self.launch(task)

    def launch(self, task):
        execution_id = task["lease_owner"]
        if execution_id in self.jobs:
            return
        job = asyncio.create_task(self._run(task))
        self.jobs[execution_id] = job
        job.add_done_callback(lambda _: self.jobs.pop(execution_id, None))

    async def control(self, task_id, action, revision=None, additions="", acceptance=""):
        task = self.tasks.get(task_id)
        if action == "cancel" and task["state"] == "running":
            await self.cancel(task["lease_owner"])
            return self.tasks.get(task_id)
        return self.tasks.control(task_id, action, revision, additions, acceptance)

    async def cancel(self, execution_id):
        """Signal the owned process group at once, retain the workspace, and fail the task as cancelled."""
        self.cancelling.add(execution_id)
        try:
            if server := self.servers.get(execution_id):
                server.terminate()
            if job := self.jobs.get(execution_id):
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            else:
                self.kill_orphan(execution_id)
            self.end(execution_id, "failed", "Cancelled by owner; partial work retained on its branch")
        finally:
            self.cancelling.discard(execution_id)

    # -- one run ---------------------------------------------------------------------------------------------------
    async def _run(self, task):
        eid, started = task["lease_owner"], time.monotonic()
        server = self.servers[eid] = self.factory(self.settings, native=True)
        try:
            await self.work(task, eid, server)
        except asyncio.CancelledError:
            if eid not in self.cancelling:
                log.info("worker %s stopped with the service; the watchdog requeues it", eid)
            raise
        except RouteBlocked as error:
            self.end(eid, "failed", plain(error))
        except Busy as error:
            self.requeue(eid, f"Waiting to merge: {error}", time.time() + 30)
        except Exception as error:
            if reset := usage_limit(error):
                self.wait_usage(task["id"], eid, reset, error)
            else:
                log.warning("task %s failed: %s", task["id"], error)
                self.end(eid, "failed", plain(error))
        finally:
            await server.close()
            self.servers.pop(eid, None)
            self.db.run("UPDATE executions SET latency_ms=? WHERE id=? AND latency_ms IS NULL", ((time.monotonic() - started) * 1000, eid))

    async def work(self, task, eid, server):
        project = self.registry.get(task["project_id"])
        root = self.workspace(task, project)
        await server.start()
        route = assignment_route(self.db, server, task)
        thread_id = await server.thread(root, WORKER_INSTRUCTIONS, task["native_thread"], route["model"])
        queue = server.queues[thread_id] = asyncio.Queue()
        server.question_handlers[thread_id] = lambda method, params: self.question(eid, method, params)
        self.db.run("UPDATE executions SET pid=?,process_start=?,model=?,effort=?,thread_id=? WHERE id=?",
                    (server.process.pid, process_start(server.process.pid), route["model"], route["effort"], thread_id, eid))
        self.db.run("UPDATE tasks SET native_thread=? WHERE id=?", (thread_id, task["id"]))
        isolated = bool(self.tasks.get(task["id"])["workspace"])
        text = ""
        while True:
            task = self.tasks.get(task["id"])
            if task["stage"] == "work":
                kind = "repair" if task["history"] and not task["history"][-1].get("ok") else "work"
                prompt = self.prompt(task, project, root, kind)
                route = assignment_route(self.db, server, task)  # policy may have changed since the last turn
                text = await self.turn(eid, server, thread_id, queue, prompt, route, task)
                if mark := re.search(r"^\W*RESULT:\s*FAILED\s*-?\s*(.*)$", text, re.I | re.M):
                    return self.end(eid, "failed", "Worker could not finish: " + (mark.group(1).strip() or "no reason given")[:400], text)
                if not isolated:
                    return self.end(eid, "done", "", text)  # no Git isolation or checks for this project
                self.event(task, eid, "task.check_started", {"round": task["rounds"]})
                result = await asyncio.to_thread(self.check, root, task["workspace_base"])
                self.event(task, eid, "task.check", {k: result[k] for k in ("ok", "checks", "count", "failures")})
                if not result["ok"]:
                    if stop := self.next_round(task["id"], result):
                        return self.end(eid, "failed", stop, text + "\n\nChecks failed:\n" + result["output"])
                    continue
                self.passed(task["id"], root)
            task = self.tasks.get(task["id"])
            done = await self.deliver(task, project, root, eid)
            if "reason" in done:
                return self.end(eid, "done", done["reason"], text or None, done.get("merge"))
            if stop := self.next_round(task["id"], done["outcome"]):  # merge conflict or checks failed after syncing main
                return self.end(eid, "failed", stop, text)

    def workspace(self, task, project):
        """The assignment's isolated Git worktree (created once), or the project directory when it is not a Git repo."""
        if task["workspace"]:
            root = Path(task["workspace"])
            if root.is_symlink() or not root.is_dir() or git(root, "symbolic-ref", "--short", "HEAD") != task["workspace_branch"]:
                raise ValueError("Retained task workspace is missing or changed; its branch is kept for manual recovery")
            return root
        shared = Path(self.registry.allowed_path(project["path"]))
        try:
            if git(shared, "rev-parse", "--show-toplevel") != str(shared.resolve()):
                return shared
            base = git(shared, "rev-parse", main_branch(shared))
        except (ValueError, OSError, subprocess.SubprocessError):
            return shared
        root = self.db.path.parent / "assignment-workspaces" / task["id"]
        branch = "linko/assignment-" + task["id"]
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.exists():
            pass  # created before a crash, before the row was updated
        elif subprocess.run(["git", "-C", str(shared), "show-ref", "--verify", "--quiet", "refs/heads/" + branch]).returncode == 0:
            git(shared, "worktree", "add", str(root), branch)
        else:
            git(shared, "worktree", "add", "-b", branch, str(root), base)
        with self.db.transaction() as conn:
            conn.execute("UPDATE tasks SET workspace=?,workspace_branch=?,workspace_base=? WHERE id=?",
                         (str(root), branch, base, task["id"]))
            self.db.event(conn, "task.workspace", {"path": str(root), "branch": branch, "base": base}, project["id"], task["id"])
        return root

    def prompt(self, task, project, root, kind):
        value = {"objective": task["description"],
                 "repair_rounds": {"used": task["rounds"], "limit": task["round_limit"], "hard_cap": HARD_CAP}}
        if kind == "repair":  # the retained thread already holds the project, context and workspace
            last = task["history"][-1]
            value["repair"] = {"round": task["rounds"], "failed_checks": last["checks"], "failures": last["failures"][:20],
                               "output_tail": last.get("output", "")[-2500:],
                               "instruction": ("Merge conflicts: resolve every conflict marker keeping both sides' intent, "
                                               "git add, and commit the merge." if "merge" in last["checks"] else
                                               "Fix the cause. Do not weaken, skip or delete tests to make them pass.")}
            return value
        value |= {"acceptance_criteria": task["acceptance"],
                  "project": {k: project[k] for k in ("name", "instructions") if project[k]},
                  "workspace": {"task_id": task["id"], "path": str(root), "branch": task["workspace_branch"],
                                "base": task["workspace_base"], "shared_checkout": project["path"]}}
        selected = self.db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task["id"],))
        if selected:
            value["task_context"] = json.loads(selected["payload"])
        if task["native_thread"]:
            value["continuation"] = ("This task already has work in the workspace. Inspect it and the git log first; "
                                     "do not repeat finished work or replay uncertain operations.")
        return {k: v for k, v in value.items() if v}

    async def turn(self, eid, server, thread_id, queue, prompt, route, task):
        """Run one native turn to completion and return its final message. Transient connection errors retry briefly."""
        images = [{"type": "localImage", "path": i["path"]} for i in prompt.get("task_context", {}).get("attachments", [])
                  if native_image(i)]
        for attempt in range(4):
            try:
                return await self.stream(eid, server, thread_id, queue, prompt, route, images, task)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if attempt == 3 or eid in self.cancelling or not (failure := classify(error)) or failure.kind != "connection":
                    raise
                await asyncio.sleep(min(60, delay(attempt + 1, "connection")))

    async def stream(self, eid, server, thread_id, queue, prompt, route, images, task):
        while not queue.empty():
            queue.get_nowait()
        usage, output, fresh = TurnUsage(), "", True
        started = await server.request("turn/start", {
            "threadId": thread_id, "clientUserMessageId": f"linko:{eid}:{new_id()}",
            "input": [{"type": "text", "text": json.dumps(prompt)}, *images], **route, "serviceTierForTurn": "default"})
        turn_id = started["turn"]["id"]
        self.db.run("UPDATE executions SET turn_id=?,status='running',input=? WHERE id=?", (turn_id, json.dumps(prompt), eid))
        self.event(task, eid, "worker.started", {"thread_id": thread_id, "turn_id": turn_id})
        while True:
            event = await queue.get()
            method, params = event["method"], event.get("params", {})
            if method == "transport/lost":
                raise CodexError("Worker connection lost", {"codexErrorInfo": "transportLost"})
            if (params.get("turnId") or (params.get("turn") or {}).get("id") or turn_id) != turn_id:
                continue
            with self.db.transaction() as conn:
                if method == "item/agentMessage/delta":
                    output, fresh = ("" if fresh else output) + params.get("delta", ""), False
                    conn.execute("UPDATE executions SET output=? WHERE id=?", (output[-1000000:], eid))
                if method == "thread/tokenUsage/updated":
                    conn.execute("UPDATE executions SET usage=? WHERE id=?", (json.dumps(usage.record(params.get("tokenUsage", {}))), eid))
                self.db.event(conn, method, params, task["project_id"], task["id"], eid)
            if method == "turn/completed":
                turn = params["turn"]
                if turn["status"] == "completed":
                    return params.get("reconciled_output", output)
                error = turn_error(self.db, eid, turn) or {}
                raise CodexError(error.get("message") or f"Worker turn {turn['status']}", error)

    async def question(self, eid, method, params):
        """A worker question waits for the owner; the task stays running and says why."""
        execution = self.db.one("SELECT * FROM executions WHERE id=?", (eid,))
        question_id = new_id()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO questions(id,execution_id,request,kind,created_at) VALUES(?,?,?,?,?)",
                         (question_id, eid, json.dumps(params), method, time.time()))
            conn.execute("UPDATE tasks SET blocker='Waiting for your answer' WHERE id=? AND lease_owner=?", (execution["task_id"], eid))
            self.db.event(conn, "worker.question", {"question_id": question_id, "request": params},
                          execution["project_id"], execution["task_id"], eid)
        while eid not in self.cancelling:
            question = self.db.one("SELECT * FROM questions WHERE id=?", (question_id,))
            if question["status"] == "answered":
                answer = json.loads(question["answer"])
                self.db.run("UPDATE tasks SET blocker='' WHERE id=? AND lease_owner=?", (execution["task_id"], eid))
                if method == "item/tool/requestUserInput":
                    return {"answers": {q["id"]: {"answers": [answer.get(q["id"], "")]} for q in params["questions"]}}
                return {"decision": "accept" if answer.get("approve") is True else "decline"}
            if question["status"] != "pending":
                raise ValueError("Question expired")
            await asyncio.sleep(0.3)
        raise ValueError("Task cancelled")

    # -- durable repair rounds -------------------------------------------------------------------------------------
    def next_round(self, task_id, result):
        """Record a failing check and decide: None to run the next repair round, else the plain reason to stop.

        The counter (rounds, round_limit, checkpoints) lives on the task row and is never reset. A repair that was
        charged but interrupted (open_round) is resumed without charging again.
        """
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            history = (json.loads(task["gate_history"]) + [{**{k: result[k] for k in ("ok", "checks", "failures", "count", "sig")},
                                                           "output": result["output"][-2500:], "round": task["rounds"]}])[-8:]
            rounds, limit, checkpoints, stop = task["rounds"], task["round_limit"], json.loads(task["checkpoints"]), None
            if rounds >= HARD_CAP:
                stop = f"Stopped at the hard cap of {HARD_CAP} repair rounds; still failing: {result['failures'][:1] or result['checks']}"
            elif rounds >= limit:
                decision, reason = evaluate(history)
                checkpoints.append({"round": rounds, "decision": decision, "reason": reason, "at": time.time(),
                                    "new_limit": limit + GRANT if decision == "continue" else limit})
                if decision == "stop":
                    stop = reason
                else:
                    limit += GRANT
            if stop is None:
                rounds += 1
            conn.execute("UPDATE tasks SET gate_history=?,rounds=?,round_limit=?,checkpoints=?,open_round=?,stage='work',updated_at=? WHERE id=?",
                         (json.dumps(history), rounds, limit, json.dumps(checkpoints), 0 if stop else 1, time.time(), task_id))
            self.db.event(conn, "task.round", {"rounds": rounds, "limit": limit, "stop": stop, "checks": result["checks"]},
                          task["project_id"], task_id)
        return stop

    def check(self, root, base):
        return unresolved(root) or self.gate(root, base)

    def passed(self, task_id, root):
        """Checks passed: commit whatever the worker left, record the pass in the history, move to the merge stage."""
        if git(root, "status", "--porcelain"):
            git(root, "add", "-A")
            git(root, *IDENTITY, "commit", "-q", "-m", "Task " + task_id[:8])
        with self.db.transaction() as conn:
            task = conn.execute("SELECT gate_history FROM tasks WHERE id=?", (task_id,)).fetchone()
            history = (json.loads(task["gate_history"]) + [{"ok": True}])[-8:]
            conn.execute("UPDATE tasks SET stage='merge',open_round=0,gate_history=? WHERE id=?", (json.dumps(history), task_id))

    # -- merge and deploy ------------------------------------------------------------------------------------------
    async def deliver(self, task, project, root, eid):
        """Sync main into the branch (the same worker resolves conflicts), merge into main, request the deploy.

        Returns {"reason": ..., "merge": ...} when finished, or {"outcome": failing-result} to go round again.
        """
        head = git(root, "rev-parse", "HEAD")
        with self.db.connect() as conn:
            wanted = enabled(conn, task["project_id"]) or bool(task["merge_requested"])
        if git(root, "rev-list", "--count", f"{task['workspace_base']}..HEAD") == "0":
            return {"reason": "", "merge": {"state": "none", "reason": "no changes"}}
        if not wanted:
            return {"reason": "", "merge": {"state": "skipped", "reason": f"Auto-deploy is off; branch {task['workspace_branch']} kept"}}
        shared = Path(self.registry.allowed_path(project["path"]))
        for _ in range(3):  # main can move while we sync; a few quick retries are not repair rounds
            branch = main_branch(shared)
            before = head
            sync = git(root, *IDENTITY, "merge", "--no-edit", branch, check=False)
            if sync.returncode:
                files = git(root, "diff", "--name-only", "--diff-filter=U").splitlines()
                if not files:
                    git(root, "merge", "--abort", check=False)
                    raise ValueError("Could not bring main into the task branch: " + (sync.stderr or sync.stdout).strip()[:300])
                return {"outcome": outcome([{"check": "merge", "failures": [f"merge: conflict in {p}" for p in files],
                                             "output": sync.stdout[-1500:]}])}
            head = git(root, "rev-parse", "HEAD")
            if head != before:  # main brought changes: the combined result must pass too
                result = await asyncio.to_thread(self.check, root, task["workspace_base"])
                if not result["ok"]:
                    return {"outcome": result}
            try:
                main_head, changed = await asyncio.to_thread(self.pipeline.merge, project["path"], head)
            except Conflict:
                continue
            requested = None
            if changed and self.pipeline.deployable(project["path"]):
                requested = self.pipeline.request(task["project_id"], "auto", task["id"], task["origin_conversation"],
                                                  "task " + task["id"][:8])
            return {"reason": "", "merge": {"state": "merged" if changed else "already in main", "main": main_head,
                                            "deploy": bool(requested)}}
        raise ValueError("main kept changing while merging; try again")

    # -- ending a run ----------------------------------------------------------------------------------------------
    def event(self, task, eid, kind, payload):
        self.db.run("INSERT INTO events(kind,project_id,task_id,execution_id,payload,created_at) VALUES(?,?,?,?,?,?)",
                    (kind, task["project_id"], task["id"], eid, json.dumps(payload), time.time()))

    def end(self, eid, state, reason, output=None, merge=None):
        """Finish the run and the task together: done or failed, with a plain reason. Idempotent."""
        with self.db.transaction() as conn:
            execution = conn.execute("SELECT * FROM executions WHERE id=?", (eid,)).fetchone()
            task = conn.execute("SELECT * FROM tasks WHERE id=? AND lease_owner=?", (execution["task_id"], eid)).fetchone() if execution else None
            if not execution or execution["finished_at"] is not None or not task:
                return
            now = time.time()
            conn.execute("""UPDATE executions SET status=?,output=COALESCE(?,output),error=?,finished_at=?,delivery='pending'
                WHERE id=?""", ("completed" if state == "done" else "failed", output, reason, now, eid))
            conn.execute("""UPDATE tasks SET state=?,blocker=?,lease_owner=NULL,retry_at=NULL,merge=COALESCE(?,merge),
                merge_requested=0,revision=revision+1,updated_at=? WHERE id=?""",
                         (state, reason, json.dumps(merge) if merge else None, now, task["id"]))
            conn.execute("UPDATE questions SET status='expired' WHERE execution_id=? AND status='pending'", (eid,))
            self.db.event(conn, "worker.finished", {"status": state, "error": reason}, task["project_id"], task["id"], eid)

    def release(self, eid, state_fields, status="waiting"):
        """Give the task back to the queue (queued) without finishing it as a result: no delivery to the manager."""
        with self.db.transaction() as conn:
            execution = conn.execute("SELECT * FROM executions WHERE id=?", (eid,)).fetchone()
            task = conn.execute("SELECT * FROM tasks WHERE id=? AND lease_owner=?", (execution["task_id"], eid)).fetchone() if execution else None
            if not execution or execution["finished_at"] is not None or not task:
                return
            sets = ",".join(f"{k}=?" for k in state_fields)
            conn.execute(f"UPDATE tasks SET state='queued',lease_owner=NULL,revision=revision+1,updated_at=?,{sets} WHERE id=?",
                         (time.time(), *state_fields.values(), task["id"]))
            conn.execute("UPDATE executions SET status=?,error=?,finished_at=? WHERE id=?",
                         (status, state_fields.get("blocker", ""), time.time(), eid))
            conn.execute("UPDATE questions SET status='expired' WHERE execution_id=? AND status='pending'", (eid,))
            self.db.event(conn, "task.requeued", {"reason": state_fields.get("blocker", "")}, task["project_id"], task["id"], eid)

    def requeue(self, eid, reason, retry_at=None, **extra):
        self.release(eid, {"blocker": reason, "retry_at": retry_at, **extra})

    def wait_usage(self, task_id, eid, failure, error):
        """Provider usage limit: wait (queued, retry time shown) with bounded backoff, else stop with a plain reason."""
        waits = self.tasks.get(task_id)["waits"]
        if waits >= MAX_USAGE_WAITS:
            return self.end(eid, "failed", f"The provider usage limit did not clear after {waits} waits; stopped. Last error: {plain(error)[:200]}")
        wait = (min(12 * 3600, max(60, failure.retry_at - time.time() + 30)) if failure.retry_at
                else min(3600, 300 * 2 ** waits))
        retry_at = time.time() + wait
        when = datetime.fromtimestamp(retry_at, timezone.utc).strftime("%H:%M UTC")
        self.requeue(eid, f"Waiting for the provider usage limit; retrying at {when}", retry_at, waits=waits + 1)

    # -- watchdog --------------------------------------------------------------------------------------------------
    def kill_orphan(self, eid):
        row = self.db.one("SELECT * FROM executions WHERE id=?", (eid,))
        if row and owned_alive(row):
            reap_orphan(row, force=True)

    def activity(self, eid):
        row = self.db.one("SELECT max(created_at) AS at FROM events WHERE execution_id=?", (eid,))
        started = self.db.one("SELECT started_at FROM executions WHERE id=?", (eid,))
        return max(row["at"] or 0, started["started_at"] if started else 0)

    async def watchdog(self):
        """Every minute: nothing may stay stuck silently. A dead or stalled worker is requeued once, then failed."""
        self.pipeline.expire()
        now = time.time()
        for task in self.db.all("SELECT * FROM tasks WHERE state='running'"):
            eid = task["lease_owner"]
            job = self.jobs.get(eid)
            if job and not job.done():
                waiting = self.db.one("SELECT 1 FROM questions WHERE execution_id=? AND status='pending'", (eid,))
                if waiting or now - self.activity(eid) < self.stall:
                    continue
                if server := self.servers.get(eid):
                    server.terminate()
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                why = "Worker stalled (no activity for %d minutes)" % (self.stall // 60)
            else:
                why = "Worker stopped unexpectedly"
            self.kill_orphan(eid)
            if eid is None:
                self.db.run("UPDATE tasks SET state='failed',blocker=?,updated_at=? WHERE id=? AND state='running'",
                            (why + " with no run record; stopped", now, task["id"]))
            elif task["requeues"] < 1:
                self.requeue(eid, why + "; requeued once", None, requeues=task["requeues"] + 1)
            else:
                self.end(eid, "failed", why + " a second time; stopped")
        with self.db.transaction() as conn:
            for task in conn.execute("""SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id
                    WHERE t.state='queued' AND p.status='removed'""").fetchall():
                self.tasks.fail_queued(conn, task, "Project was removed")

    async def close(self):
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)

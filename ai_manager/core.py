"""Task-execution core: one queue per project (one running task each, projects in parallel), durable repair rounds, a
watchdog. See README.md.

States: queued -> running -> done | failed. `held` is a flag on a queued task (skipped until released); order within
a project is the only dependency. A worker implements on an isolated branch, the application runs the permanent checks
(the fixed smoke suite plus the test files the branch added or changed), repairs happen in durable rounds (at most 5,
then the task stops and reports). A passing branch always merges into main; a saved project setup requests the shared deploy script,
and a project without one gets a Create deploy setup task (deploy_auto.py).
"""

import asyncio
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import fence, isolation
from .attachments import native_image
from .codex import WORKER_RULES, AppServer, CodexError, TurnUsage, process_start
from .db import new_id
from .deploy_auto import ACCEPTANCE, OBJECTIVE, SETUP_FILE, TITLE
from .fence import Busy, main_branch
from .listing import CARD_WINDOW, bounded_listing
from .model_settings import RouteBlocked, assignment_route
from .pipeline import IDENTITY, git, merge_tree
from .push_diagnosis import precheck
from .recovery import turn_error
from .runner import Runner, plain

REPAIR_ROUNDS = 5
# A To-Do can contain a 200-character title, a separator and 24000 characters of details.
MAX_OBJECTIVE_CHARS = 24202
SMOKE_SECONDS, SUITE_SECONDS, PROBE_SECONDS = 180, 600, 60
WORKER_INSTRUCTIONS = WORKER_RULES + "\nYou are a worker. The application runs the permanent checks and merges; you implement.\n"
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


def secret_check(root, base):
    """Key-shaped literals in the task's commits (base..HEAD) fail like a test, so GitHub push protection never sees them."""
    # Commits merged in from main are already upstream: only this task's own commits count.
    def ok(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True).returncode == 0

    on_main = ok("symbolic-ref", "-q", "--short", "HEAD") and git(root, "symbolic-ref", "--short", "HEAD") == "main"
    upstream = ["--not", "main"] if not on_main and ok("rev-parse", "-q", "--verify", "refs/heads/main") else []
    try:
        diff = subprocess.run(["git", "-C", str(root), "log", "-p", "-U0", "--no-color", "--no-ext-diff",
                               "--format=@@commit %H", f"{base}..HEAD", *upstream], capture_output=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    hits = precheck(diff[:5_000_000])
    if not hits:
        return None
    failures = [f"secrets: {h['path']}:{h['line']} looks like a {h['type']} (commit {h['commit']}); "
                "build key-shaped test values at runtime (rule 10)" for h in hits]
    return {"check": "secrets", "failures": failures,
            "output": "\n".join(failures) + "\nNever commit key-shaped literals, even fake ones in tests: "
                      "write e.g. 'ghp_' + 'x' * 36 so no literal matches. Amend or add a commit that removes them; "
                      "the check scans every commit in base..HEAD, so rewrite the branch history (e.g. squash) if needed."}


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
    """Fixed smoke suite plus the branch's own test files. Returns {ok, checks, failures, count, output}."""
    root = Path(root)
    env = {**os.environ, "PYTHONPATH": str(root), "AI_MANAGER_SECURE_COOKIE": "0"}
    failed = [secret_check(root, base)]
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
    return {"ok": not failed, "checks": [f["check"] for f in failed], "failures": failures[:30], "count": len(failures),
            "output": "\n".join(f["output"] for f in failed)[-3000:]}


def self_check_command(root, base):
    """Use the running manager's installation and interpreter, even for another project's worktree."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "worker_check.py"
    return shlex.join([sys.executable, str(script), "--root", str(Path(root).resolve()), "--base", base])


# ---- the queue --------------------------------------------------------------------------------------------------
class Tasks:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def record(row):
        row = dict(row)
        row["merge"] = json.loads(row["merge"]) if row["merge"] else None
        row["history"] = json.loads(row.pop("gate_history"))
        row["repair_limit"] = REPAIR_ROUNDS
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
        keys = ("id", "description", "acceptance", "status", "blocker", "lease_owner", "revision", "rounds",
                "workspace", "workspace_branch", "merge", "retry_at", "chain_root", "chain_parent")
        return {k: task.get(k) for k in keys} | {"worker_route": task.get("worker_route")}

    def page(self, project_id=None, *, limit=CARD_WINDOW, offset=0, query="", status=None, through=None, older=False):
        if status not in (None, 'queued', 'held', 'running', 'done', 'failed'):
            raise ValueError('Unknown task status')
        status_sql = "CASE WHEN held=1 AND state='queued' THEN 'held' ELSE state END"
        # List only card fields. Full objectives, criteria, gate logs, merge receipts
        # and workspace references remain available from get()/read_task/history.
        columns = f"""id,project_id,substr(description,1,300) AS description,
            length(description)>300 AS description_truncated,state,{status_sql} AS status,
            blocker,lease_owner,revision,rounds,created_at,updated_at,retry_at,
            (SELECT history_preview(output) FROM executions e WHERE e.task_id=tasks.id
                ORDER BY e.rowid DESC LIMIT 1) AS result_summary"""
        where = "instr(lower(description),lower(?))>0"
        params = [query]
        if status:
            where += f' AND ({status_sql})=?'
            params.append(status)
        page = bounded_listing(self.db, table='tasks', columns=columns,
                               terminal="state IN ('done','failed')", clock='updated_at',
                               scope=project_id, where=where, params=params, limit=limit, offset=offset,
                               through=through, older=older,
                               active_order="CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,queued_at,id")
        for row in page['rows']:
            row['repair_limit'] = REPAIR_ROUNDS
            row['title'] = row['description'].strip().split('\n', 1)[0][:100]
        return {'tasks': page.pop('rows'), **page}

    def list(self, project_id=None):
        return self.page(project_id)['tasks']

    def add(self, project_id, description, origin_conversation=None, request_key=None, acceptance="", selected=None,
            model_route=None, todos=None, reuse_objective=True, chain_root=None, chain_parent=None, on_create=None):
        if not description.strip() or len(description) > MAX_OBJECTIVE_CHARS:
            raise ValueError(f"Objective must be 1–{MAX_OBJECTIVE_CHARS} characters")
        if not isinstance(acceptance, str) or len(acceptance) > 12000:
            raise ValueError("Acceptance criteria must be at most 12000 characters")
        now, task_id = time.time(), new_id()
        with self.db.transaction() as conn:
            previous = conn.execute(
                """SELECT id,project_id FROM tasks WHERE request_key=? OR id=(
                    SELECT task_id FROM events WHERE kind='task.reused' AND json_extract(history_text(payload),'$.request_key')=?
                    ORDER BY seq DESC LIMIT 1)""", (request_key, request_key)).fetchone() if request_key else None
            if not previous and reuse_objective:
                previous = conn.execute(
                    """SELECT id,project_id FROM tasks WHERE project_id=? AND description=? AND origin_conversation IS ?
                    AND state IN ('queued','running') ORDER BY created_at DESC LIMIT 1""",
                    (project_id, description, origin_conversation)).fetchone()
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
                origin_conversation,request_key,chain_root,chain_parent) VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?)""",
                (task_id, project_id, description, acceptance, now, now, now, origin_conversation, request_key,
                 chain_root, chain_parent))
            self.db.event(conn, "task.created", {"objective": description}, project_id, task_id)
            if model_route:
                self.db.event(conn, "task.model_selected", model_route, project_id, task_id)
            if selected:
                self.db.event(conn, "task.context", selected, project_id, task_id)
            if todos:
                from .todos import Todos
                Todos(self.db).consume(conn, todos, project_id, task_id)
            if on_create:
                on_create(conn, task_id)  # may refuse (automatic follow-up limit): the task is then not created
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
                if len(text) > MAX_OBJECTIVE_CHARS or len(extra) > 12000:
                    raise ValueError("Added instructions exceed task limits; prior requirements remain intact")
                fields = {"description": text, "acceptance": extra, "held": 0}
                if state in {"done", "failed"}:  # counters stay: repair rounds are never reset
                    merge_retry = task["stage"] == "merge"
                    fields |= {"state": "queued", "queued_at": now, "blocker": "",
                               "stage": "merge" if merge_retry else "work", "requeues": 0,
                               "retry_at": None, "gate_history": task["gate_history"] if merge_retry else "[]"}
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE tasks SET {sets},revision=revision+1,updated_at=? WHERE id=?", (*fields.values(), now, task_id))
            self.db.event(conn, "task." + action, {}, task["project_id"], task_id)
        return self.get(task_id)

    def claim(self, ready=None):
        try:
            with fence.hold(self.db.path.parent, exclusive=False):
                return self._claim(ready)
        except Busy:
            return None  # A deploy holds the fence: no new work starts until it finishes.

    def _claim(self, ready=None):
        now, claimed = time.time(), None
        with self.db.transaction() as conn:
            for task in conn.execute("""SELECT t.*,p.path,p.status AS project_status FROM tasks t
                    JOIN projects p ON p.id=t.project_id WHERE t.state='queued' AND t.held=0
                    AND NOT EXISTS(SELECT 1 FROM tasks r WHERE r.state='running' AND r.project_id=t.project_id)
                    ORDER BY t.queued_at,t.rowid""").fetchall():
                if task["project_status"] == "removed":
                    self.fail_queued(conn, task, "Project was removed")
                    continue
                if task["project_status"] != "active":
                    continue  # paused or archived by the owner: skipped like a hold
                if task["retry_at"] and task["retry_at"] > now:
                    continue  # waiting for a usage limit or the network: skipped like a hold until its retry time
                if ready is not None and not ready(task["project_id"], (task["id"], task["revision"])):
                    continue
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
        """Owner asked to deploy a finished task whose branch never reached main: it goes round again at the merge stage,
        without another AI turn."""
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or not task["workspace_branch"]:
                raise ValueError("Task has no branch to merge")
            if (json.loads(task["merge"]) if task["merge"] else {}).get("state") == "merged":
                raise ValueError("Task is already merged")
            if task["state"] != "done":
                raise ValueError("Only a finished task is merged on request; a failed task needs continue first")
            conn.execute("UPDATE tasks SET state='queued',stage='merge',queued_at=?,requeues=0,blocker='',"
                         "revision=revision+1,updated_at=? WHERE id=?", (time.time(), time.time(), task_id))
            self.db.event(conn, "task.merge_requested", {}, task["project_id"], task_id)
        return self.get(task_id)

    def fail_queued(self, conn, task, reason):
        conn.execute("UPDATE tasks SET state='failed',blocker=?,revision=revision+1,updated_at=? WHERE id=?",
                     (reason, time.time(), task["id"]))
        self.db.event(conn, "task.failed", {"reason": reason}, task["project_id"], task["id"])


# ---- the worker -------------------------------------------------------------------------------------------------
class Core:
    def __init__(self, db, registry, settings, pipeline, server_factory=AppServer, gate=run_gate, stall=1800):
        self.db, self.registry, self.settings, self.pipeline = db, registry, settings, pipeline
        self.tasks, self.factory, self.gate, self.stall = Tasks(db), server_factory, gate, stall
        self.jobs, self.servers, self.cancelling = {}, {}, set()
        self.gates = {}  # execution id -> directories opened to the isolated worker user, closed when the run ends
        self.runner = Runner(self, stall)  # starts, resumes and unsticks work: ai_manager/runner.py

    # -- scheduling ------------------------------------------------------------------------------------------------
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
                self.runner.stop_orphan(execution_id)
            self.end(execution_id, "failed", "Cancelled by owner; partial work retained on its branch")
        finally:
            self.cancelling.discard(execution_id)

    # -- one run ---------------------------------------------------------------------------------------------------
    async def _run(self, task):
        eid, started = task["lease_owner"], time.monotonic()
        server = self.servers[eid] = self.factory(self.settings, native=True)
        try:
            self.isolate(task, eid, server)
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
            self.runner.failed(task, eid, error)
        finally:
            await server.close()
            if gate := self.gates.pop(eid, None):
                await asyncio.to_thread(gate[0].close, *gate[1])
            self.servers.pop(eid, None)
            self.db.run("UPDATE executions SET latency_ms=? WHERE id=? AND latency_ms IS NULL", ((time.monotonic() - started) * 1000, eid))

    def isolate(self, task, eid, server):
        """Run this worker as the unprivileged worker user when installed; otherwise say plainly that it is not."""
        found = isolation.detect()
        if found and hasattr(server, "isolate"):
            server.isolate(found, eid)
            self.event(task, eid, "worker.isolation", {"user": found.user})
        else:
            log.warning("task %s: %s", task["id"], isolation.WARNING)
            self.event(task, eid, "worker.isolation", {"warning": isolation.WARNING})

    async def open_workspace(self, eid, server, root, project):
        if not isinstance(found := getattr(server, "isolation", None), isolation.Isolation):
            return
        shared = Path(self.registry.allowed_path(project["path"]))
        paths = [root] if shared.resolve() == Path(root).resolve() else [root, shared]
        self.gates[eid] = (found, paths)
        await asyncio.to_thread(found.open, root, shared)

    async def work(self, task, eid, server):
        project = self.registry.get(task["project_id"])
        root = self.workspace(task, project)
        await self.open_workspace(eid, server, root, project)
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
            if stop := self.next_round(task["id"], done["outcome"]):  # checks failed after syncing main
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
                 "repair_rounds": {"used": task["rounds"], "limit": REPAIR_ROUNDS}}
        if task["workspace_base"]:
            value["self_check"] = {"command": self_check_command(root, task["workspace_base"]),
                                   "instruction": "Run it before finishing; it is the same check the application will run."}
        if kind == "repair":  # the retained thread already holds the project, context and workspace
            last = task["history"][-1]
            value["repair"] = {"round": task["rounds"], "failed_checks": last["checks"], "failures": last["failures"][:20],
                               "output_tail": last.get("output", "")[-2500:],
                               "instruction": ("Merge conflicts: resolve every conflict marker keeping both sides' intent, "
                                               "git add, and commit the merge." if "merge" in last["checks"] else
                                               "Fix the cause. Do not weaken, skip or delete tests to make them pass.")}
            value["repair"]["instruction"] += (
                " When the fix reveals a reusable pitfall, add or update its Known pitfalls entry "
                "(or fix it in code) in the same commit.")
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
        """Run one native turn to completion and return its final message. Errors go to the runner (wait or fail)."""
        images = [{"type": "localImage", "path": i["path"]} for i in prompt.get("task_context", {}).get("attachments", [])
                  if native_image(i)]
        if images and isinstance(found := getattr(server, "isolation", None), isolation.Isolation):
            await asyncio.to_thread(found.share, [i["path"] for i in images])
        return await self.stream(eid, server, thread_id, queue, prompt, route, images, task)

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

        The counter lives on the task row and is never reset: after REPAIR_ROUNDS repairs the task stops and reports.
        """
        with self.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            history = (json.loads(task["gate_history"]) + [{**{k: result[k] for k in ("ok", "checks", "failures", "count")},
                                                           "output": result["output"][-2500:], "round": task["rounds"]}])[-8:]
            rounds, stop = task["rounds"], None
            if rounds >= REPAIR_ROUNDS:
                stop = f"Stopped after {REPAIR_ROUNDS} repair rounds; still failing: {result['failures'][:1] or result['checks']}"
            else:
                rounds += 1
            conn.execute("UPDATE tasks SET gate_history=?,rounds=?,stage='work',updated_at=? WHERE id=?",
                         (json.dumps(history), rounds, time.time(), task_id))
            self.db.event(conn, "task.round", {"rounds": rounds, "limit": REPAIR_ROUNDS, "stop": stop, "checks": result["checks"]},
                          task["project_id"], task_id)
        return stop

    def repair_deploy(self, receipt):
        """The application's own deploy receipt for a task's merge failed on tests or health: send it back to that task as
        a repair round (shared counter, at most REPAIR_ROUNDS). Withdrawals and infrastructure failures are not repairable
        and only get reported. Returns True when the task was re-queued or stopped."""
        if (receipt.get("status") not in {"failed", "rolled_back"} or not receipt.get("repairable")
                or not receipt.get("task_id") or receipt.get("source") not in {"auto", "owner"}):
            return False
        try:
            task = self.tasks.get(receipt["task_id"])
        except ValueError:
            return False
        if task["state"] != "done" or not task["workspace_branch"]:
            return False
        if self.db.one("SELECT 1 FROM events WHERE task_id=? AND kind='task.deploy_repair' AND payload LIKE ?",
                       (task["id"], '%"' + receipt["id"] + '"%')):
            return False
        project = self.registry.get(task["project_id"])
        shared = Path(self.registry.allowed_path(project["path"]))
        reason = f"Deploy {receipt['status'].replace('_', ' ')} after merge: {receipt['reason']}"
        failure = {"ok": False, "checks": ["deploy"], "count": 1, "failures": [reason[:600]], "output": reason[-2500:]}
        base = None
        if root := task["workspace"]:
            try:  # the repair starts from the current main: the merged branch is brought up to it and diffed against it
                base = git(shared, "rev-parse", main_branch(shared))
                git(Path(root), *IDENTITY, "merge", "--no-edit", base)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                log.warning("task %s: could not bring main into its branch for a deploy repair: %s", task["id"], error)
                base = None
        stop = self.next_round(task["id"], failure)
        now = time.time()
        with self.db.transaction() as conn:
            self.db.event(conn, "task.deploy_repair", {"deploy": receipt["id"], "reason": reason[:600], "stop": stop},
                          task["project_id"], task["id"])
            if stop:
                stop = "Deploy failed and could not be fixed: " + stop
                eid = new_id()  # a finished run of its own so the normal result notice reaches the owner
                conn.execute("""INSERT INTO executions(id,task_id,project_id,agent_id,status,error,started_at,finished_at,
                    worktree,branch,delivery) VALUES(?,?,?,'execution','failed',?,?,?,?,?,'pending')""",
                             (eid, task["id"], task["project_id"], stop, now, now, task["workspace"], task["workspace_branch"]))
                conn.execute("UPDATE tasks SET state='failed',blocker=?,revision=revision+1,updated_at=? WHERE id=?",
                             (stop, now, task["id"]))
                self.db.event(conn, "worker.finished", {"status": "failed", "error": stop}, task["project_id"], task["id"], eid)
            else:
                conn.execute("UPDATE tasks SET state='queued',queued_at=?,blocker='',requeues=0,retry_at=NULL,held=0,"
                             "workspace_base=COALESCE(?,workspace_base),revision=revision+1,updated_at=? WHERE id=?",
                             (now, base, now, task["id"]))
        return True

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
            conn.execute("UPDATE tasks SET stage='merge',gate_history=? WHERE id=?", (json.dumps(history), task_id))

    # -- merge and deploy ------------------------------------------------------------------------------------------
    async def deliver(self, task, project, root, eid):
        """Check clean combinations with main, preserve both versions on conflicts, then request the deploy.

        Returns {"reason": ..., "merge": ...} when finished, or {"outcome": failing-result} to go round again.
        """
        head = git(root, "rev-parse", "HEAD")
        if git(root, "rev-list", "--count", f"{task['workspace_base']}..HEAD") == "0":
            return {"reason": "", "merge": {"state": "none", "reason": "no changes"}}
        shared = Path(self.registry.allowed_path(project["path"]))
        branch = git(shared, "rev-parse", main_branch(shared))
        before = head
        already_merged = git(root, "merge-base", "--is-ancestor", head, branch, check=False).returncode == 0
        _, conflicts = merge_tree(root, branch, head)
        # Keep the delivered task commit stable so a restart can recover its copy report.
        if not conflicts and not already_merged:
            sync = git(root, *IDENTITY, "merge", "--no-edit", branch, check=False)
            if sync.returncode:
                raise ValueError("Could not bring main into the task branch: " + (sync.stderr or sync.stdout).strip()[:300])
        head = git(root, "rev-parse", "HEAD")
        if head != before:  # main brought changes: the combined result must pass too
            result = await asyncio.to_thread(self.check, root, task["workspace_base"])
            if not result["ok"]:
                return {"outcome": result}
        main_head, changed, deployment = await asyncio.to_thread(
            self.pipeline.merge, project["path"], head,
            (task["project_id"], "auto", task["id"], task["origin_conversation"], "task " + task["id"][:8]))
        deployment = deployment or {}
        requested = bool(deployment.get('id'))
        text = 'deploy requested' if requested else deployment.get('reason', 'already in main')
        note = deployment.get('setup_file') or {}
        if note.get('state') == 'missing':
            text = 'no deploy setup; ' + self.queue_deploy_setup(task)
        elif note.get('state') == 'invalid':
            text = f"invalid {SETUP_FILE}: {note['reason']}; no deploy setup saved"
        elif note.get('state') == 'none':
            text = 'not deployable: ' + note['reason']
        elif note.get('state') == 'declined':
            text = 'no deploy setup; ' + note['reason']
        elif note.get('state') == 'seeded':
            text = note['reason'] + ('; deploy requested' if requested else '; ' + text)
        return {"reason": "", "merge": {"state": "merged" if changed else "already in main", "main": main_head,
                                        "deploy": requested, "deployment": text,
                                        "copies": deployment.get("copies", [])}}

    def queue_deploy_setup(self, task):
        """Queue one Create deploy setup task in the project unless one is open; returns a plain status. The task that
        creates the setup never queues another, so a worker that wrote no usable file cannot start a loop."""
        if task["description"].startswith(TITLE):
            return f"{TITLE} merged without a usable {SETUP_FILE}; no further task is queued"
        open_task = self.db.one("SELECT 1 FROM tasks WHERE project_id=? AND state IN ('queued','running') "
                                "AND substr(description,1,?)=?", (task["project_id"], len(TITLE), TITLE))
        if open_task:
            return TITLE + " already queued"
        self.tasks.add(task["project_id"], OBJECTIVE, acceptance=ACCEPTANCE)
        return TITLE + " queued"

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
                revision=revision+1,updated_at=? WHERE id=?""",
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

    async def close(self):
        for job in list(self.jobs.values()):
            job.cancel()
        await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        await self.pipeline.sync.close()
        await self.pipeline.backups.close()

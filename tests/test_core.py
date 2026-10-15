"""The task-execution core: sequential queue, durable repair rounds, merge, hold, watchdog, usage-limit wait.

Real Git repositories and the real check runner where it matters; the model is a scripted fake server.
"""
import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer

from ai_manager import core as core_module
from ai_manager import pipeline as pipeline_module
from ai_manager.codex import CodexError
from ai_manager.config import Settings
from ai_manager.core import HARD_CAP, Core, Tasks, evaluate, outcome, run_gate, unresolved
from ai_manager.db import Database
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def failing(count, sig="a", check="smoke"):
    return {"ok": False, "checks": [check], "failures": [f"{check}: tests/x.py::t{i} - boom" for i in range(count)],
            "count": count, "sig": sig, "output": "pytest output"}


PASS = {"ok": True, "checks": [], "failures": [], "count": 0, "sig": "", "output": ""}


class Lab:
    """Scripted model + scripted check results shared by every server the core creates."""

    def __init__(self):
        self.turns, self.actions, self.results, self.cwds = [], [], [], []
        self.release = None

    def factory(self, settings, native=False):
        return ScriptedServer(settings, self)

    def gate(self, root, base):
        return self.results.pop(0) if self.results else PASS

    async def default(self, root, prompt):
        name = f"change-{len(self.turns)}.txt"
        (Path(root) / name).write_text("work\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "worker change " + name)
        return "Done."


class ScriptedServer(FakeServer):
    def __init__(self, settings, lab):
        super().__init__(settings)
        self.lab, self.terminated = lab, False
        self.process = SimpleNamespace(pid=-1)

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.cwd = Path(cwd)
        self.lab.cwds.append(self.cwd)
        return thread_id or "thread-" + self.cwd.name

    def terminate(self):
        self.terminated = True

    async def request(self, method, params, timeout=30):
        if method != "turn/start":
            return {}
        prompt = json.loads(params["input"][0]["text"])
        self.lab.turns.append(prompt)
        if self.lab.release:
            await self.lab.release.wait()
        action = self.lab.actions.pop(0) if self.lab.actions else self.lab.default
        text = await action(self.cwd, prompt)
        queue = self.queues[params["threadId"]]
        turn_id = f"turn-{len(self.lab.turns)}"
        queue.put_nowait({"method": "item/agentMessage/delta", "params": {"turnId": turn_id, "delta": text}})
        queue.put_nowait({"method": "turn/completed", "params": {"turn": {"id": turn_id, "status": "completed"}}})
        return {"turn": {"id": turn_id}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    app = projects / "app"
    app.mkdir(parents=True)
    git(app, "init", "-q", "-b", "main")
    (app / "shared.txt").write_text("line one\nline two\nline three\n")
    git(app, "add", "-A")
    git(app, "commit", "-qm", "initial")
    settings = Settings(data_dir=tmp_path / "data", project_roots=(projects,))
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (projects,))
    project = registry.add(app)
    monkeypatch.setattr(pipeline_module, "ROOT", app.resolve())  # the manager's own project: deployable
    pipeline = Pipeline(db, registry)
    pipeline.set(project["id"], True)
    lab = Lab()
    core = Core(db, registry, settings, pipeline, lab.factory, gate=lab.gate)
    return SimpleNamespace(db=db, registry=registry, settings=settings, project=project, app=app, pipeline=pipeline,
                           lab=lab, core=core, tasks=core.tasks)


async def run_next(world):
    task = world.tasks.claim()
    assert task, "nothing to claim"
    world.core.launch(task)
    await asyncio.gather(*list(world.core.jobs.values()))
    return world.tasks.get(task["id"])


# ---- migration -------------------------------------------------------------------------------------------------
def test_migration_closes_old_nonfinal_tasks_and_keeps_every_row_and_branch(tmp_path):
    import sqlite3

    path = tmp_path / "manager.sqlite3"
    db = Database(path)
    workspace = tmp_path / "keep-me"
    workspace.mkdir()
    with db.transaction() as conn:
        conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('p','p','/p','a',1,1)")
    # Rebuild the pre-rebuild task table shape (lifecycle/legacy columns, old indexes) with one task per old state.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE tasks")
        conn.execute("""CREATE TABLE tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            description TEXT NOT NULL, acceptance TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
            blocker TEXT NOT NULL DEFAULT '', lease_owner TEXT, revision INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'waiting',
            origin_conversation TEXT, request_key TEXT, native_thread TEXT, legacy INTEGER NOT NULL DEFAULT 1,
            workspace TEXT, workspace_branch TEXT, workspace_base TEXT)""")
        conn.execute("CREATE UNIQUE INDEX project_worker ON tasks(project_id) WHERE lifecycle='running' AND workspace IS NULL")
        for index, lifecycle in enumerate(["completed", "failed", "cancelled", "queued", "running", "waiting",
                                           "paused", "pause_requested", "cancel_requested"]):
            conn.execute("INSERT INTO tasks(id,project_id,description,lifecycle,lease_owner,workspace,workspace_branch,created_at,updated_at)"
                         " VALUES(?,?,?,?,?,?,?,?,?)",
                         (lifecycle, "p", f"old {lifecycle}", lifecycle, "x-" + lifecycle if lifecycle in {"running", "waiting"} else None,
                          str(workspace), "linko/assignment-" + lifecycle, 10 + index, 10 + index))
        conn.execute("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES('x-running','running','p','execution','running',1)")
        conn.execute("INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES('x-manager',NULL,'p','manager','running',1)")
        conn.execute("DELETE FROM schema_version WHERE version=13")
    reopened = Database(path)
    rows = {r["id"]: r for r in reopened.all("SELECT * FROM tasks")}
    assert len(rows) == 9 and all(r["workspace"] == str(workspace) for r in rows.values())  # nothing deleted or moved
    assert {k: v["state"] for k, v in rows.items()} == {
        "completed": "done", "failed": "failed", "cancelled": "failed", "queued": "failed", "running": "failed",
        "waiting": "failed", "paused": "failed", "pause_requested": "failed", "cancel_requested": "failed"}
    assert "Cancelled" in rows["paused"]["blocker"] and rows["completed"]["blocker"] == ""
    assert all(r["lease_owner"] is None for r in rows.values()) and all(r["rounds"] == 0 for r in rows.values())
    # Their unfinished runs are closed so deploys and the queue are not blocked; the manager's own run is untouched.
    assert reopened.one("SELECT finished_at FROM executions WHERE id='x-running'")["finished_at"] is not None
    assert reopened.one("SELECT finished_at FROM executions WHERE id='x-manager'")["finished_at"] is None
    assert reopened.one("SELECT max(version) AS v FROM schema_version")["v"] == 13
    assert Tasks(reopened).claim() is None and Database(path).all("SELECT state FROM tasks WHERE state='queued'") == []
    assert reopened.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    # Idempotent: a later start must not close new work again.
    reopened.run("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,state) VALUES('new','p','n',1,1,1,'queued')")
    assert Database(path).one("SELECT state FROM tasks WHERE id='new'")["state"] == "queued"
    with sqlite3.connect(path) as conn:  # one running task at most, enforced by the database
        conn.execute("UPDATE tasks SET state='running' WHERE id='new'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,state) VALUES('b','p','n',1,1,1,'running')")


# ---- queue -----------------------------------------------------------------------------------------------------
@pytest.mark.smoke
def test_queue_is_ordered_hold_skips_release_restores_and_cancel_closes(world):
    tasks, project = world.tasks, world.project["id"]
    a, b, c = (tasks.add(project, name) for name in ("first", "second", "third"))
    held = tasks.control(a["id"], "hold", a["revision"])
    assert held["status"] == "held" and held["state"] == "queued"
    first = tasks.claim()
    assert first["id"] == b["id"]  # held tasks are skipped
    assert tasks.claim() is None  # one at a time
    world.db.run("UPDATE tasks SET state='done',lease_owner=NULL WHERE id=?", (b["id"],))
    cancelled = tasks.control(c["id"], "cancel", None)
    assert cancelled["status"] == "failed" and cancelled["blocker"] == "Cancelled by owner"
    assert tasks.claim() is None  # only the held task is left
    assert tasks.control(a["id"], "release")["status"] == "queued"
    assert tasks.claim()["id"] == a["id"]
    with pytest.raises(ValueError, match="Only a queued task"):
        tasks.control(a["id"], "hold")  # running
    with pytest.raises(ValueError, match="stale|changed"):
        tasks.control(b["id"], "continue", 999)


def test_continue_appends_requeues_at_the_end_and_never_resets_the_counter(world):
    tasks, project = world.tasks, world.project["id"]
    a, b = tasks.add(project, "alpha"), tasks.add(project, "beta")
    world.db.run("UPDATE tasks SET state='failed',blocker='x',rounds=5,round_limit=6,gate_history='[{\"ok\":false}]' WHERE id=?", (a["id"],))
    appended = tasks.control(b["id"], "continue", None, "also this")
    assert appended["state"] == "queued" and "also this" in appended["description"] and "beta" in appended["description"]
    again = tasks.control(a["id"], "continue", None, "more")
    assert again["status"] == "queued" and again["rounds"] == 5 and again["round_limit"] == 6 and again["history"] == []
    assert tasks.claim()["id"] == b["id"]  # the re-queued task goes behind the older queued one


# ---- worker flow: queue -> worker -> checks -> merge -> deploy request -------------------------------------------
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_worker_runs_in_isolated_branch_then_merges_and_requests_one_deploy(world):
    tasks, project = world.tasks, world.project["id"]
    first = tasks.add(project, "add a file", acceptance="file exists")
    second = tasks.add(project, "add another file")
    done = await run_next(world)
    assert done["id"] == first["id"] and done["state"] == "done" and done["blocker"] == ""
    assert done["workspace"] != str(world.app) and done["workspace_branch"] == "linko/assignment-" + first["id"]
    assert done["merge"]["state"] == "merged" and done["merge"]["deploy"] is True
    assert (world.app / "change-1.txt").exists()  # merged into main
    assert git(world.app, "rev-parse", "main") == done["merge"]["main"]
    assert git(world.app, "branch", "--list", done["workspace_branch"])  # the branch is kept
    request = json.loads((world.pipeline.root / "request.json").read_text())
    assert request["task_id"] == first["id"] and request["source"] == "auto"
    await world.core.tick()
    assert world.pipeline.pending() and not world.core.jobs  # no new work starts while a deploy waits
    assert world.tasks.get(second["id"])["state"] == "queued"
    (world.pipeline.root / "request.json").unlink()  # the deploy script finished
    nxt = await run_next(world)
    assert nxt["id"] == second["id"] and nxt["state"] == "done"
    assert (world.app / "change-2.txt").exists() and git(world.app, "status", "--porcelain") == ""
    execution = world.db.one("SELECT * FROM executions WHERE task_id=?", (first["id"],))
    assert execution["status"] == "completed" and execution["delivery"] == "pending"
    prompt = world.lab.turns[0]
    assert prompt["objective"] == "add a file" and prompt["acceptance_criteria"] == "file exists"
    assert prompt["repair_rounds"] == {"used": 0, "limit": 3, "hard_cap": 9}


@pytest.mark.asyncio
async def test_auto_deploy_off_keeps_the_branch_unmerged_and_requests_nothing(world):
    world.pipeline.set(world.project["id"], False)
    world.tasks.add(world.project["id"], "stay on the branch")
    before = git(world.app, "rev-parse", "main")
    done = await run_next(world)
    assert done["state"] == "done" and done["merge"]["state"] == "skipped" and "off" in done["merge"]["reason"]
    assert git(world.app, "rev-parse", "main") == before and not world.pipeline.pending()
    assert git(done["workspace"], "log", "--oneline", f"{before}..HEAD")  # the work is on its branch


@pytest.mark.asyncio
async def test_owner_deploy_now_merges_a_finished_unmerged_task_without_another_model_turn(world):
    world.pipeline.set(world.project["id"], False)
    task = world.tasks.add(world.project["id"], "finished but unmerged")
    await run_next(world)
    turns = len(world.lab.turns)
    queued = world.tasks.request_merge(task["id"])
    assert queued["state"] == "queued" and queued["stage"] == "merge"
    done = await run_next(world)
    assert done["merge"]["state"] == "merged" and len(world.lab.turns) == turns  # merge stage skips the AI turn
    assert (world.app / "change-1.txt").exists() and world.pipeline.pending()
    with pytest.raises(ValueError, match="already merged"):
        world.tasks.request_merge(task["id"])


@pytest.mark.asyncio
async def test_worker_that_gives_up_fails_with_its_plain_reason_and_nothing_merges(world):
    async def give_up(root, prompt):
        return "I could not reach the database.\nRESULT: FAILED - needs credentials"

    world.lab.actions.append(give_up)
    world.tasks.add(world.project["id"], "impossible")
    before = git(world.app, "rev-parse", "main")
    done = await run_next(world)
    assert done["state"] == "failed" and done["blocker"] == "Worker could not finish: needs credentials"
    assert not world.pipeline.pending() and git(world.app, "rev-parse", "main") == before


# ---- repair rounds ---------------------------------------------------------------------------------------------
def test_checkpoint_rules_progress_loop_and_first_look():
    first, same, fewer, changed = failing(4), failing(4), failing(2, "b"), failing(2, "c", "targeted")
    h = lambda *items: [{"ok": False, **{k: v for k, v in i.items() if k != "ok"}} for i in items]  # noqa: E731
    assert evaluate(h(first, fewer))[0] == "continue" and "fewer failures" in evaluate(h(first, fewer))[1]
    assert evaluate(h(fewer, changed))[0] == "continue" and "check changed" in evaluate(h(fewer, changed))[1]
    assert evaluate(h(first, failing(4, "other")))[0] == "continue"  # same count, no longer the same way
    verdict, reason = evaluate(h(first, same))
    assert verdict == "stop" and "same way" in reason and "No progress" in reason
    assert evaluate(h(failing(2), failing(5, "z")))[0] == "stop"  # more failures is not progress
    assert evaluate(h(first))[0] == "continue"  # nothing to compare yet
    assert outcome([])["ok"] and outcome([{"check": "smoke", "failures": ["a"], "output": "o"}])["count"] == 1


@pytest.mark.asyncio
async def test_progress_grants_three_more_twice_then_the_hard_cap_of_nine_stops(world):
    # Fewer failures every round -> progress at both checkpoints; still failing at round 9 -> hard cap.
    world.lab.results = [failing(30 - i, str(i)) for i in range(12)]
    world.tasks.add(world.project["id"], "never quite passes")
    done = await run_next(world)
    assert done["state"] == "failed" and "hard cap of 9" in done["blocker"]
    assert done["rounds"] == HARD_CAP == 9 and done["round_limit"] == 9
    assert [c["round"] for c in done["checkpoints"]] == [3, 6]
    assert all(c["decision"] == "continue" and "fewer failures" in c["reason"] for c in done["checkpoints"])
    assert len(world.lab.turns) == 10  # the implementation turn plus nine repair rounds
    assert not world.pipeline.pending() and git(world.app, "log", "--oneline", "-1").endswith("initial")  # never merged
    assert [t.get("repair", {}).get("round") for t in world.lab.turns[:4]] == [None, 1, 2, 3]
    assert world.lab.turns[1]["repair"]["failed_checks"] == ["smoke"]


@pytest.mark.asyncio
async def test_same_failure_twice_in_a_row_is_a_loop_and_stops_at_the_checkpoint(world):
    world.lab.results = [failing(3, "same") for _ in range(10)]
    world.tasks.add(world.project["id"], "stuck")
    done = await run_next(world)
    assert done["state"] == "failed" and done["blocker"].startswith("No progress: smoke failed the same way")
    assert done["rounds"] == 3 and done["round_limit"] == 3  # stopped at the 3rd-round checkpoint, no grant
    assert done["checkpoints"][0]["decision"] == "stop" and done["checkpoints"][0]["round"] == 3
    assert len(world.lab.turns) == 4


@pytest.mark.asyncio
async def test_recovery_after_repair_passes_and_merges_with_rounds_recorded(world):
    world.lab.results = [failing(2), PASS]
    world.tasks.add(world.project["id"], "needs one repair")
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1 and done["merge"]["state"] == "merged"
    assert done["checkpoints"] == [] and (world.app / "change-2.txt").exists()


@pytest.mark.asyncio
async def test_interrupted_repair_round_is_not_charged_twice_and_counter_survives_requeue(world):
    task = world.tasks.add(world.project["id"], "dies mid repair")
    world.lab.results = [failing(2, "p")]
    release = asyncio.Event()

    async def hang(root, prompt):
        if prompt.get("repair"):
            await release.wait()
        return await world.lab.default(root, prompt)

    world.lab.actions = [hang, hang]
    claimed = world.tasks.claim()
    world.core.launch(claimed)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if world.tasks.get(task["id"])["open_round"]:
            break
    assert world.tasks.get(task["id"])["rounds"] == 1 and world.tasks.get(task["id"])["open_round"] == 1
    job = world.core.jobs[claimed["lease_owner"]]
    job.cancel()  # the service stopped mid round
    await asyncio.gather(job, return_exceptions=True)
    await world.core.watchdog()
    again = world.tasks.get(task["id"])
    assert again["state"] == "queued" and again["requeues"] == 1 and again["rounds"] == 1
    release.set()
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1  # the resumed repair was not charged again
    assert world.lab.turns[-1]["repair"]["round"] == 1


@pytest.mark.asyncio
async def test_merge_conflict_is_resolved_by_the_same_worker_and_counts_a_round(world):
    task = world.tasks.add(world.project["id"], "edit shared.txt")

    async def edit(root, prompt):
        (Path(root) / "shared.txt").write_text("line one\nline two EDITED BY TASK\nline three\n")
        git(root, "commit", "-qam", "task edit")
        # Meanwhile main moves on the same line.
        (world.app / "shared.txt").write_text("line one\nline two EDITED ON MAIN\nline three\n")
        git(world.app, "commit", "-qam", "main edit")
        return "edited"

    async def resolve(root, prompt):
        assert prompt["repair"]["failed_checks"] == ["merge"] and "shared.txt" in prompt["repair"]["failures"][0]
        (Path(root) / "shared.txt").write_text("line one\nline two EDITED BY BOTH\nline three\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "resolve conflict")
        return "resolved"

    world.lab.actions = [edit, resolve]
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1 and done["merge"]["state"] == "merged"
    assert (world.app / "shared.txt").read_text() == "line one\nline two EDITED BY BOTH\nline three\n"
    assert world.tasks.get(task["id"])["stage"] == "merge" and len(world.lab.turns) == 2
    assert git(world.app, "status", "--porcelain") == ""  # main was never left mid-merge


@pytest.mark.asyncio
async def test_a_worker_that_leaves_the_conflict_unresolved_burns_rounds_and_fails_cleanly(world):
    world.tasks.add(world.project["id"], "unresolvable")

    async def edit(root, prompt):
        (Path(root) / "shared.txt").write_text("line one\nTASK\nline three\n")
        git(root, "commit", "-qam", "task edit")
        (world.app / "shared.txt").write_text("line one\nMAIN\nline three\n")
        git(world.app, "commit", "-qam", "main edit")
        return "edited"

    async def ignore(root, prompt):
        return "did nothing"

    world.lab.actions = [edit, *([ignore] * 12)]
    done = await run_next(world)
    assert done["state"] == "failed" and done["rounds"] <= HARD_CAP and "conflict" in done["blocker"]
    assert git(world.app, "status", "--porcelain") == "" and "MAIN" in (world.app / "shared.txt").read_text()


# ---- watchdog, usage limit, cancel -----------------------------------------------------------------------------
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_watchdog_requeues_a_dead_worker_once_then_fails_with_a_reason(world):
    task = world.tasks.add(world.project["id"], "worker will vanish")
    claimed = world.tasks.claim()  # running, but no live worker (service crashed)
    await world.core.watchdog()
    once = world.tasks.get(task["id"])
    assert once["state"] == "queued" and once["requeues"] == 1 and once["lease_owner"] is None
    assert "requeued once" in once["blocker"]
    old = world.db.one("SELECT * FROM executions WHERE id=?", (claimed["lease_owner"],))
    assert old["finished_at"] is not None  # the dead run is closed
    second = world.tasks.claim()
    assert second["id"] == task["id"] and second["lease_owner"] != claimed["lease_owner"]
    await world.core.watchdog()
    final = world.tasks.get(task["id"])
    assert final["state"] == "failed" and "a second time" in final["blocker"] and final["lease_owner"] is None
    assert world.db.one("SELECT 1 FROM tasks WHERE state='running'") is None


@pytest.mark.asyncio
async def test_watchdog_stops_a_stalled_worker_and_fails_queued_work_of_a_removed_project(world):
    world.lab.release = asyncio.Event()  # the model never answers
    core = Core(world.db, world.registry, world.settings, world.pipeline, world.lab.factory, gate=world.lab.gate, stall=0.2)
    task = world.tasks.add(world.project["id"], "silent forever")
    core.launch(world.tasks.claim())
    await asyncio.sleep(0.4)
    await core.watchdog()
    assert world.tasks.get(task["id"])["state"] == "queued" and not core.jobs
    other = world.tasks.add(world.project["id"], "orphaned")
    world.db.run("UPDATE projects SET status='removed' WHERE id=?", (world.project["id"],))
    await core.watchdog()
    assert world.tasks.get(other["id"])["state"] == "failed" and world.tasks.get(other["id"])["blocker"] == "Project was removed"
    world.lab.release.set()


@pytest.mark.asyncio
async def test_stale_deploy_request_expires_so_the_queue_cannot_hang(world):
    world.pipeline.request(world.project["id"], "auto", None, None, "stale")
    assert world.pipeline.pending() and not world.pipeline.expire()
    request = json.loads((world.pipeline.root / "request.json").read_text())
    request["requested_at"] = time.time() - 8 * 3600
    (world.pipeline.root / "request.json").write_text(json.dumps(request))
    await world.core.watchdog()
    assert not world.pipeline.pending()
    assert world.db.one("SELECT 1 FROM events WHERE kind='deployment.expired'")


@pytest.mark.asyncio
async def test_usage_limit_waits_with_bounded_backoff_in_order_then_retries(world):
    first, second = world.tasks.add(world.project["id"], "first"), world.tasks.add(world.project["id"], "second")

    async def limited(root, prompt):
        raise CodexError("limit", {"codexErrorInfo": "usageLimitExceeded", "retryAfter": 120})

    world.lab.actions = [limited]
    waiting = await run_next(world)
    assert waiting["id"] == first["id"] and waiting["state"] == "queued" and waiting["waits"] == 1
    assert 100 < waiting["retry_at"] - time.time() <= 160 and "usage limit" in waiting["blocker"]
    assert world.tasks.claim() is None  # strict order: the second task does not jump the waiting one
    assert world.db.one("SELECT delivery FROM executions WHERE task_id=?", (first["id"],))["delivery"] is None  # not a result
    world.db.run("UPDATE tasks SET retry_at=? WHERE id=?", (time.time() - 1, first["id"]))
    done = await run_next(world)
    assert done["id"] == first["id"] and done["state"] == "done"
    assert (await run_next(world))["id"] == second["id"]


@pytest.mark.asyncio
async def test_usage_limit_without_a_reset_time_backs_off_exponentially_then_gives_up(world):
    task = world.tasks.add(world.project["id"], "always limited")

    async def limited(root, prompt):
        raise CodexError("limit", {"codexErrorInfo": "rateLimitExceeded"})

    delays = []
    for attempt in range(core_module.MAX_USAGE_WAITS):
        world.lab.actions = [limited]
        world.db.run("UPDATE tasks SET retry_at=NULL WHERE id=?", (task["id"],))
        row = await run_next(world)
        assert row["state"] == "queued" and row["waits"] == attempt + 1
        delays.append(round(row["retry_at"] - time.time()))
    assert delays[0] in range(295, 301) and delays[1] in range(595, 601) and max(delays) <= 3600
    world.lab.actions = [limited]
    world.db.run("UPDATE tasks SET retry_at=NULL WHERE id=?", (task["id"],))
    final = await run_next(world)
    assert final["state"] == "failed" and "did not clear after 8 waits" in final["blocker"]


@pytest.mark.asyncio
async def test_transient_connection_errors_retry_the_turn_inside_the_run(world, monkeypatch):
    monkeypatch.setattr(core_module, "delay", lambda count, kind: 0)

    async def dropped(root, prompt):
        raise CodexError("lost", {"codexErrorInfo": "transportLost"})

    world.lab.actions = [dropped, dropped]
    world.tasks.add(world.project["id"], "flaky network")
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 0 and done["requeues"] == 0 and len(world.lab.turns) == 3
    world.lab.actions = [dropped] * 4
    world.tasks.add(world.project["id"], "dead network")
    failed = await run_next(world)
    assert failed["state"] == "failed" and "lost" in failed["blocker"]  # bounded: four attempts, then a plain reason


@pytest.mark.asyncio
async def test_cancel_stops_the_running_worker_and_keeps_its_branch(world):
    world.lab.release = asyncio.Event()
    task = world.tasks.add(world.project["id"], "to be cancelled")
    claimed = world.tasks.claim()
    world.core.launch(claimed)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if world.tasks.get(task["id"])["workspace"]:
            break
    result = await world.core.control(task["id"], "cancel", None)
    assert result["state"] == "failed" and result["lease_owner"] is None and "Cancelled by owner" in result["blocker"]
    assert not world.core.jobs and git(world.app, "branch", "--list", "linko/assignment-" + task["id"])
    assert world.db.one("SELECT status FROM executions WHERE id=?", (claimed["lease_owner"],))["status"] == "failed"
    world.lab.release.set()


# ---- the permanent checks themselves -----------------------------------------------------------------------------
def make_checked_repo(tmp_path):
    repo = tmp_path / "checked"
    (repo / "tests").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    (repo / "tests" / "test_smoke.py").write_text("import pytest\n\n@pytest.mark.smoke\ndef test_ok():\n    assert True\n")
    (repo / "tests" / "conftest.py").write_text("def pytest_configure(config):\n    config.addinivalue_line('markers', 'smoke: s')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def test_gate_runs_smoke_and_only_permanent_test_files_and_ignores_probes(tmp_path):
    repo, base = make_checked_repo(tmp_path)
    assert run_gate(repo, base)["ok"]
    (repo / "probe.py").write_text("raise SystemExit('a one-off probe that fails')\n")  # informational, never gates
    (repo / "scratch.sh").write_text("exit 1\n")
    assert run_gate(repo, base)["ok"]
    (repo / "tests" / "test_new.py").write_text("def test_added():\n    assert 1 == 2\n\ndef test_also():\n    assert False\n")
    result = run_gate(repo, base)
    assert not result["ok"] and result["checks"] == ["targeted"] and result["count"] == 2
    assert any("test_added" in f for f in result["failures"]) and "assert 1 == 2" in result["output"]
    (repo / "tests" / "test_new.py").write_text("def test_added():\n    assert True\n")
    assert run_gate(repo, base)["ok"]
    (repo / "tests" / "test_smoke.py").write_text("import pytest\n\n@pytest.mark.smoke\ndef test_ok():\n    assert False\n")
    broken = run_gate(repo, base)
    assert not broken["ok"] and broken["checks"] == ["smoke", "targeted"]  # the edited smoke file is also targeted
    assert outcome([{"check": "x", "failures": ["a 1"], "output": ""}])["sig"] == outcome([{"check": "x", "failures": ["a 2"], "output": ""}])["sig"]


def test_every_check_has_a_timeout_and_a_hung_suite_is_a_failure_not_a_hang(tmp_path, monkeypatch):
    repo, base = make_checked_repo(tmp_path)
    (repo / "tests" / "test_hang.py").write_text("import time\n\ndef test_hang():\n    time.sleep(60)\n")
    monkeypatch.setattr(core_module, "SUITE_SECONDS", 2)
    started = time.monotonic()
    result = run_gate(repo, base)
    assert time.monotonic() - started < 20
    assert not result["ok"] and result["checks"] == ["targeted"] and "timed out after 2s" in result["failures"][0]
    assert core_module.PROBE_SECONDS == 60


def test_gate_reports_unresolved_merge_conflicts_and_projects_without_tests_pass(tmp_path):
    repo, base = make_checked_repo(tmp_path)
    (repo / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "f")
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "f.txt").write_text("side\n")
    git(repo, "commit", "-qam", "side")
    git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n")
    git(repo, "commit", "-qam", "main")
    subprocess.run(["git", "-C", str(repo), "merge", "side"], capture_output=True)
    result = unresolved(repo)
    assert not result["ok"] and result["checks"] == ["merge"] and "f.txt" in result["failures"][0]
    bare = tmp_path / "plain"
    bare.mkdir()
    git(tmp_path, "init", "-q", "-b", "main", str(bare))
    (bare / "a.txt").write_text("x")
    git(bare, "add", "-A")
    git(bare, "commit", "-qm", "a")
    assert run_gate(bare, git(bare, "rev-parse", "HEAD"))["ok"]  # no tests directory: nothing to run


# ---- manager actions -------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manager_controls_hold_release_cancel_continue_and_deploy_now(world):
    from ai_manager.chat import Chat
    from ai_manager.orchestration import Scheduler

    chat = Chat(world.db, world.registry, FakeServer(world.settings))
    scheduler = Scheduler(world.db, world.registry, world.settings, world.core, chat)
    project = world.project["id"]
    conversation = chat.conversation(project)["id"]

    calls = iter(range(1000))

    async def act(action, **args):
        number = next(calls)
        return await scheduler.manager_action(project, {"tool": "manager_action", "mode": "auto", "callId": f"c{number}",
                                                        "manager_execution_id": f"m-{number}", "conversation_id": conversation,
                                                        "arguments": {"action": action, **args}})

    task = world.tasks.add(project, "queued work")
    assert (await act("hold", task_id=task["id"]))["status"] == "held"
    assert (await act("release", task_id=task["id"]))["status"] == "queued"
    assert (await act("continue", task_id=task["id"], objective="plus this"))["description"].endswith("plus this")
    assert (await act("cancel", task_id=task["id"]))["status"] == "failed"
    assert (await act("continue", task_id=task["id"]))["status"] == "queued"
    for action in ("hold", "cancel", "continue", "delegate"):  # a worker-result notice may not start or change work
        with pytest.raises(ValueError, match="explicit owner"):
            await scheduler.manager_action(project, {"tool": "manager_action", "mode": "auto", "internal": True, "callId": "x",
                                                     "manager_execution_id": "m-i", "conversation_id": conversation,
                                                     "arguments": {"action": action, "task_id": task["id"], "objective": "o"}})
    with pytest.raises(ValueError, match="read-only"):
        await scheduler.manager_action(project, {"tool": "manager_action", "mode": "conversation", "callId": "y",
                                                 "manager_execution_id": "m-c", "conversation_id": conversation,
                                                 "arguments": {"action": "hold", "task_id": task["id"]}})
    listed = await act("list_tasks")
    assert listed["assignments"][0]["status"] == "queued" and listed["assignments"][0]["rounds"] == 0
    read = await act("read_task", task_id=task["id"])
    assert read["checkpoints"] == [] and "checks" in read
    world.pipeline.set(project, False)
    done = await run_next(world)
    assert done["state"] == "done" and done["merge"]["state"] == "skipped"
    assert (await act("deploy", task_id=done["id"]))["merge"] == "requested"

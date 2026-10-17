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
from ai_manager import runner as runner_module
from ai_manager.codex import CodexError
from ai_manager.config import Settings
from ai_manager.core import REPAIR_ROUNDS, Core, Tasks, outcome, run_gate, unresolved
from ai_manager.db import Database
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def failing(count, sig="a", check="smoke"):
    return {"ok": False, "checks": [check], "failures": [f"{check}: tests/x.py::t{i} - boom" for i in range(count)],
            "count": count, "output": "pytest output"}


PASS = {"ok": True, "checks": [], "failures": [], "count": 0, "output": ""}


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
    pipeline = Pipeline(db, registry)
    lab = Lab()
    core = Core(db, registry, settings, pipeline, lab.factory, gate=lab.gate)

    def deployable(flag):
        pipeline.save_setup(project['id'], {'deploy_command': ['true'], 'backup_command': ['true'],
                                             'health_command': ['true'], 'rollback_command': ['true']} if flag else None)

    deployable(True)

    return SimpleNamespace(db=db, registry=registry, settings=settings, project=project, app=app, pipeline=pipeline,
                           lab=lab, core=core, tasks=core.tasks, deployable=deployable)


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
    assert reopened.one("SELECT max(version) AS v FROM schema_version")["v"] == 17
    assert Tasks(reopened).claim() is None and Database(path).all("SELECT state FROM tasks WHERE state='queued'") == []
    assert reopened.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    # Idempotent: a later start must not close new work again.
    reopened.run("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,state) VALUES('new','p','n',1,1,1,'queued')")
    assert Database(path).one("SELECT state FROM tasks WHERE id='new'")["state"] == "queued"
    with sqlite3.connect(path) as conn:  # one running task at most, enforced by the database
        conn.execute("UPDATE tasks SET state='running' WHERE id='new'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,state) VALUES('b','p','n',1,1,1,'running')")


def test_unused_columns_are_dropped_and_their_neighbours_keep_their_data(tmp_path):
    path = tmp_path / "manager.sqlite3"
    db = Database(path)
    with db.transaction() as conn:
        conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('p','p','/p','a',1,1)")
        for name, definition in (("round_limit", "INTEGER NOT NULL DEFAULT 3"), ("open_round", "INTEGER NOT NULL DEFAULT 0"),
                                 ("checkpoints", "TEXT NOT NULL DEFAULT '[]'"), ("merge_requested", "INTEGER NOT NULL DEFAULT 0"),
                                 ("waits", "INTEGER NOT NULL DEFAULT 0")):
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        conn.execute("ALTER TABLE todos ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
        conn.execute("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,rounds) VALUES('t','p','d',1,1,1,2)")
        conn.execute("INSERT INTO todos(id,project_id,title,created_at,updated_at) VALUES('o','p','keep',1,1)")
        conn.execute("DELETE FROM schema_version WHERE version=15")
    reopened = Database(path)
    gone = {"round_limit", "open_round", "checkpoints", "merge_requested", "waits"}
    assert not gone & {r["name"] for r in reopened.all("PRAGMA table_info(tasks)")}
    assert "revision" not in {r["name"] for r in reopened.all("PRAGMA table_info(todos)")}
    assert reopened.one("SELECT rounds FROM tasks WHERE id='t'")["rounds"] == 2 and reopened.one("SELECT title FROM todos")["title"] == "keep"
    Database(path)  # idempotent


def test_old_auto_deploy_values_are_dropped_and_old_unmerged_work_is_not_merged_retroactively(tmp_path):
    path = tmp_path / "manager.sqlite3"
    db = Database(path)
    skipped = json.dumps({"state": "skipped", "reason": "Auto-deploy is off; branch kept"})
    with db.transaction() as conn:
        conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES('p','p','/p','a',1,1)")
        conn.execute("INSERT INTO tasks(id,project_id,description,created_at,updated_at,queued_at,state,merge) "
                     "VALUES('old','p','old work',1,1,1,'done',?)", (skipped,))
        for key, value in (("auto_deploy:p", {"enabled": False}), ("auto_deploy_seed", {"projects": ["p"]}), ("keep", 1)):
            conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(value)))
        conn.execute("DELETE FROM schema_version WHERE version=14")
    reopened = Database(path)
    assert [r["key"] for r in reopened.all("SELECT key FROM settings WHERE key LIKE 'auto_deploy%'")] == []
    assert reopened.setting("keep") == 1  # unrelated settings are untouched
    old = Tasks(reopened).get("old")
    assert old["state"] == "done" and old["merge"]["state"] == "skipped" and reopened.one("SELECT 1 FROM tasks WHERE state='queued'") is None


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
    world.db.run("UPDATE tasks SET state='failed',blocker='x',rounds=3,gate_history='[{\"ok\":false}]' WHERE id=?", (a["id"],))
    appended = tasks.control(b["id"], "continue", None, "also this")
    assert appended["state"] == "queued" and "also this" in appended["description"] and "beta" in appended["description"]
    again = tasks.control(a["id"], "continue", None, "more")
    assert again["status"] == "queued" and again["rounds"] == 3 and again["history"] == []
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
    await world.core.runner.tick()
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
    assert prompt["repair_rounds"] == {"used": 0, "limit": 5}


@pytest.mark.asyncio
async def test_passing_work_always_merges_and_a_project_without_setup_reports_merges_only(world):
    world.deployable(False)
    world.tasks.add(world.project["id"], "merge only")
    done = await run_next(world)
    assert done["state"] == "done" and done["merge"]["state"] == "merged" and done["merge"]["deploy"] is False
    assert (world.app / "change-1.txt").exists() and not world.pipeline.pending()
    assert done['merge']['deployment'] == world.pipeline.summary(world.project['id']) == 'no deploy setup; merges only'
    assert world.pipeline.request(world.project['id'])['status'] == 'no deploy setup'
    assert "auto_deploy" not in json.dumps([dict(r) for r in world.db.all("SELECT key FROM settings")])


@pytest.mark.asyncio
async def test_owner_deploy_now_merges_a_finished_unmerged_task_without_another_model_turn(world):
    world.deployable(False)
    task = world.tasks.add(world.project["id"], "finished but unmerged")
    before = git(world.app, "rev-parse", "main")
    await run_next(world)
    git(world.app, "reset", "-q", "--hard", before)  # an older task whose branch never reached main
    world.db.run("UPDATE tasks SET merge=? WHERE id=?", (json.dumps({"state": "skipped"}), task["id"]))
    world.deployable(True)
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
def test_outcome_summarises_failing_checks():
    assert outcome([])["ok"]
    result = outcome([{"check": "smoke", "failures": ["a"], "output": "o"}])
    assert not result["ok"] and result["count"] == 1 and result["checks"] == ["smoke"]


@pytest.mark.asyncio
@pytest.mark.parametrize("results", [[failing(30 - i, str(i)) for i in range(12)], [failing(3, "same") for _ in range(12)]],
                         ids=["fewer failures every round", "the same failure every round"])
async def test_five_repair_rounds_then_the_task_stops_whatever_the_progress(world, results):
    world.lab.results = results
    task = world.tasks.add(world.project["id"], "never quite passes")
    done = await run_next(world)
    assert done["state"] == "failed" and "Stopped after 5 repair rounds" in done["blocker"]
    assert done["rounds"] == REPAIR_ROUNDS == 5 and done["repair_limit"] == 5
    assert len(world.lab.turns) == 6  # the implementation turn plus five repair rounds
    assert [t.get("repair", {}).get("round") for t in world.lab.turns] == [None, 1, 2, 3, 4, 5]
    assert world.lab.turns[1]["repair"]["failed_checks"] == ["smoke"]
    assert not world.pipeline.pending() and git(world.app, "log", "--oneline", "-1").endswith("initial")  # never merged
    # The counter is durable: continuing the task does not buy new rounds.
    world.lab.results = [failing(1, "again")]
    world.tasks.control(task["id"], "continue", None, "try once more")
    again = await run_next(world)
    assert again["state"] == "failed" and again["rounds"] == 5 and "Stopped after 5 repair rounds" in again["blocker"]
    assert len(world.lab.turns) == 7


@pytest.mark.asyncio
async def test_recovery_after_repair_passes_and_merges_with_rounds_recorded(world):
    world.lab.results = [failing(2), PASS]
    world.tasks.add(world.project["id"], "needs one repair")
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 1 and done["merge"]["state"] == "merged"
    assert (world.app / "change-2.txt").exists()


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
        if world.tasks.get(task["id"])["rounds"]:
            break
    assert world.tasks.get(task["id"])["rounds"] == 1
    job = world.core.jobs[claimed["lease_owner"]]
    job.cancel()  # the service stopped mid round
    await asyncio.gather(job, return_exceptions=True)
    await world.core.runner.watchdog()
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
    assert done["state"] == "failed" and done["rounds"] <= REPAIR_ROUNDS and "conflict" in done["blocker"]
    assert git(world.app, "status", "--porcelain") == "" and "MAIN" in (world.app / "shared.txt").read_text()


# ---- watchdog, usage limit, cancel -----------------------------------------------------------------------------
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_watchdog_requeues_a_dead_worker_once_then_fails_with_a_reason(world):
    task = world.tasks.add(world.project["id"], "worker will vanish")
    claimed = world.tasks.claim()  # running, but no live worker (service crashed)
    await world.core.runner.watchdog()
    once = world.tasks.get(task["id"])
    assert once["state"] == "queued" and once["requeues"] == 1 and once["lease_owner"] is None
    assert "requeued once" in once["blocker"]
    old = world.db.one("SELECT * FROM executions WHERE id=?", (claimed["lease_owner"],))
    assert old["finished_at"] is not None  # the dead run is closed
    second = world.tasks.claim()
    assert second["id"] == task["id"] and second["lease_owner"] != claimed["lease_owner"]
    await world.core.runner.watchdog()
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
    await core.runner.watchdog()
    assert world.tasks.get(task["id"])["state"] == "queued" and not core.jobs
    other = world.tasks.add(world.project["id"], "orphaned")
    world.db.run("UPDATE projects SET status='removed' WHERE id=?", (world.project["id"],))
    await core.runner.watchdog()
    assert world.tasks.get(other["id"])["state"] == "failed" and world.tasks.get(other["id"])["blocker"] == "Project was removed"
    world.lab.release.set()


@pytest.mark.asyncio
async def test_a_pending_deploy_holds_new_work_only_for_a_bounded_time(world):
    task = world.tasks.add(world.project["id"], "waits for the deploy")
    world.pipeline.request(world.project["id"], "auto", None, None, "nobody consumes this")
    await world.core.runner.tick()
    assert world.pipeline.pending() and not world.core.jobs and world.tasks.get(task["id"])["state"] == "queued"
    assert not world.pipeline.expire()  # a young request keeps holding the queue
    request = json.loads((world.pipeline.root / "request.json").read_text())
    request["requested_at"] = time.time() - pipeline_module.DEPLOY_WAIT - 60
    (world.pipeline.root / "request.json").write_text(json.dumps(request))
    await world.core.runner.watchdog()
    assert not world.pipeline.pending()
    assert world.db.one("SELECT 1 FROM events WHERE kind='deployment.expired'")
    done = await run_next(world)  # the queue is running again without any deploy having happened
    assert done["id"] == task["id"] and done["state"] == "done"


def test_a_deploy_past_its_backup_is_never_withdrawn(world):
    world.pipeline.request(world.project["id"], "auto", None, None, "restarting")
    request = json.loads((world.pipeline.root / "request.json").read_text())
    request["requested_at"] = time.time() - 3 * pipeline_module.DEPLOY_WAIT
    (world.pipeline.root / "request.json").write_text(json.dumps(request))
    (world.pipeline.root / "state.json").write_text(json.dumps({"phase": "restarting"}))
    assert not world.pipeline.expire() and world.pipeline.pending()  # it finishes or rolls back by itself


def limited_by(details):
    async def limited(root, prompt):
        raise CodexError("limit", details)
    return limited


@pytest.mark.asyncio
async def test_usage_limit_waits_five_minutes_in_order_and_never_fails_the_task(world):
    world.deployable(False)  # no deploy request between the runs
    first, second = world.tasks.add(world.project["id"], "first"), world.tasks.add(world.project["id"], "second")
    # A long limit (the provider says "in 10 hours") is no reason to wait longer than 5 minutes: it may reset early.
    long_limit = {"codexErrorInfo": "usageLimitExceeded", "retryAfter": 36000, "resetsAt": time.time() + 36000}
    for attempt in range(25):
        world.lab.actions = [limited_by(long_limit)]
        world.db.run("UPDATE tasks SET retry_at=NULL WHERE id=?", (first["id"],))
        waiting = await run_next(world)
        assert waiting["id"] == first["id"] and waiting["state"] == "queued" and "usage limit" in waiting["blocker"]
        assert 290 < waiting["retry_at"] - time.time() <= 300
        assert "waits" not in waiting and waiting["rounds"] == 0 and waiting["requeues"] == 0
    assert world.db.one("SELECT delivery FROM executions WHERE task_id=?", (first["id"],))["delivery"] is None  # not a result
    ran = await run_next(world)  # a task waiting for its retry time is skipped like a hold: the next one runs
    assert ran["id"] == second["id"] and ran["state"] == "done"
    assert world.tasks.claim() is None
    world.db.run("UPDATE tasks SET retry_at=? WHERE id=?", (time.time() - 1, first["id"]))  # the limit reset early
    done = await run_next(world)
    assert done["id"] == first["id"] and done["state"] == "done" and done["retry_at"] is None


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_connection_loss_retries_every_five_minutes_forever_and_resumes_the_same_thread(world):
    dropped = limited_by({"codexErrorInfo": "transportLost"})
    task = world.tasks.add(world.project["id"], "flaky network")
    world.lab.actions = [dropped]
    waiting = await run_next(world)
    assert waiting["state"] == "queued" and "connection" in waiting["blocker"] and 290 < waiting["retry_at"] - time.time() <= 300
    assert waiting["rounds"] == 0 and waiting["requeues"] == 0
    assert world.tasks.claim() is None  # not eligible before its retry time
    for _ in range(25):  # far past any former wait cap
        world.db.run("UPDATE tasks SET retry_at=? WHERE id=?", (time.time() - 1, task["id"]))
        world.lab.actions = [dropped]
        row = await run_next(world)
        assert row["state"] == "queued" and 290 < row["retry_at"] - time.time() <= 300
    world.db.run("UPDATE tasks SET retry_at=? WHERE id=?", (time.time() - 1, task["id"]))
    done = await run_next(world)
    assert done["state"] == "done" and done["rounds"] == 0 and done["native_thread"] == waiting["native_thread"]


@pytest.mark.asyncio
async def test_a_restart_forgets_the_retry_time_and_a_real_error_still_fails(world):
    task = world.tasks.add(world.project["id"], "limited")
    world.lab.actions = [limited_by({"codexErrorInfo": "usageLimitExceeded", "resetsAt": time.time() + 5400})]
    waiting = await run_next(world)
    assert waiting["state"] == "queued" and waiting["retry_at"] < time.time() + 301
    core = Core(world.db, world.registry, world.settings, world.pipeline, world.lab.factory, gate=world.lab.gate)
    await core.runner.tick()  # first tick after a service restart: limits may have reset meanwhile
    await asyncio.gather(*list(core.jobs.values()))
    assert world.tasks.get(task["id"])["state"] == "done"
    bad = world.tasks.add(world.project["id"], "unauthorized")
    world.lab.actions = [limited_by({"codexErrorInfo": "unauthorized", "message": "login required"})]
    failed = await run_next(world)
    assert failed["id"] == bad["id"] and failed["state"] == "failed" and failed["retry_at"] is None


# ---- idle-queue stall --------------------------------------------------------------------------------------------
async def idle_for_a_while(world, monkeypatch):
    monkeypatch.setattr(runner_module, "IDLE_STALL_SECONDS", 0)
    await world.core.runner.tick()
    await world.core.runner.watchdog()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_watchdog_reports_an_idle_queue_that_has_eligible_work_it_cannot_start(world, monkeypatch):
    from ai_manager import fence

    task = world.tasks.add(world.project["id"], "should be running")
    with fence.hold(world.db.path.parent):  # something keeps the live checkout locked while the queue sits idle
        await idle_for_a_while(world, monkeypatch)
        stalled = world.tasks.get(task["id"])
        assert stalled["state"] == "queued" and "Queue idle" in stalled["blocker"] and "live checkout" in stalled["blocker"]
        assert len(world.db.all("SELECT 1 FROM events WHERE kind='queue.stalled'")) == 1
        await world.core.runner.watchdog()  # reported once per stall, not every minute
        assert len(world.db.all("SELECT 1 FROM events WHERE kind='queue.stalled'")) == 1
    await world.core.runner.tick()  # the lock is gone: the task starts and the stall is over
    await asyncio.gather(*list(world.core.jobs.values()))
    done = world.tasks.get(task["id"])
    assert done["state"] == "done" and world.core.runner.idle_since is None


@pytest.mark.asyncio
async def test_held_paused_retrying_running_and_deploy_waiting_work_is_not_a_stall(world, monkeypatch):
    project = world.project["id"]
    held, waiting = world.tasks.add(project, "held"), world.tasks.add(project, "retrying")
    world.tasks.control(held["id"], "hold")
    world.db.run("UPDATE tasks SET retry_at=? WHERE id=?", (time.time() + 300, waiting["id"]))
    await idle_for_a_while(world, monkeypatch)
    world.db.run("UPDATE tasks SET retry_at=NULL WHERE id=?", (waiting["id"],))
    world.db.run("UPDATE projects SET status='paused' WHERE id=?", (project,))
    await idle_for_a_while(world, monkeypatch)
    world.db.run("UPDATE projects SET status='active' WHERE id=?", (project,))
    world.pipeline.request(project, "auto", None, None, "a deploy is pending")
    await idle_for_a_while(world, monkeypatch)
    assert world.core.runner.idle_since is None
    (world.pipeline.root / "request.json").unlink()
    world.lab.release = asyncio.Event()
    await world.core.runner.tick()  # one task is now running; the other waits its turn
    await world.core.runner.watchdog()
    assert world.core.runner.idle_since is None and not world.db.one("SELECT 1 FROM events WHERE kind='queue.stalled'")
    world.lab.release.set()
    await asyncio.gather(*list(world.core.jobs.values()))


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_restart_mid_task_resumes_without_penalty_and_keeps_the_round_counter(world):
    task = world.tasks.add(world.project["id"], "survives a restart")
    world.lab.release = asyncio.Event()
    world.core.launch(world.tasks.claim())
    await asyncio.sleep(0.05)
    world.db.run("UPDATE tasks SET rounds=2 WHERE id=?", (task["id"],))
    thread = world.tasks.get(task["id"])["native_thread"]
    await world.core.close()  # the service stops with the worker mid-turn
    assert world.tasks.get(task["id"])["state"] == "running"
    world.lab.release.set()
    core = Core(world.db, world.registry, world.settings, world.pipeline, world.lab.factory, gate=world.lab.gate)
    await core.runner.tick()  # no chat, no owner message: the tick alone resumes it
    await asyncio.gather(*list(core.jobs.values()))
    row = world.tasks.get(task["id"])
    assert row["state"] == "done" and row["requeues"] == 0 and row["rounds"] == 2 and row["native_thread"] == thread
    assert "continuation" in world.lab.turns[-1]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_tick_starts_the_oldest_eligible_task_one_at_a_time_and_skips_held(world):
    world.deployable(False)  # a pending deploy request would (rightly) hold the queue
    held, oldest, newest = (world.tasks.add(world.project["id"], name) for name in ("held", "oldest", "newest"))
    world.tasks.control(held["id"], "hold")
    world.lab.release = asyncio.Event()
    await world.core.runner.tick()
    await world.core.runner.tick()
    assert world.tasks.get(oldest["id"])["state"] == "running" and len(world.core.jobs) == 1
    assert world.tasks.get(newest["id"])["state"] == "queued" and world.tasks.get(held["id"])["status"] == "held"
    world.lab.release.set()
    await asyncio.gather(*list(world.core.jobs.values()))
    await world.core.runner.tick()
    await asyncio.gather(*list(world.core.jobs.values()))
    assert [world.tasks.get(t["id"])["state"] for t in (oldest, newest, held)] == ["done", "done", "queued"]
    assert [t["objective"] for t in world.lab.turns] == ["oldest", "newest"]


def test_adding_the_same_work_twice_is_deduplicated(world):
    first = world.tasks.add(world.project["id"], "do it once", "chat-1", request_key="k1")
    assert world.tasks.add(world.project["id"], "do it once", "chat-1", request_key="k1")["id"] == first["id"]
    assert world.tasks.add(world.project["id"], "do it once", "chat-1")["id"] == first["id"]
    assert world.db.one("SELECT count(*) AS n FROM tasks WHERE state='queued'")["n"] == 1


@pytest.mark.asyncio
async def test_paused_project_is_skipped_and_a_stale_deploy_request_does_not_block_forever(world):
    other = world.tasks.add(world.project["id"], "waits for the project")
    world.db.run("UPDATE projects SET status='paused' WHERE id=?", (world.project["id"],))
    await world.core.runner.tick()
    assert world.tasks.get(other["id"])["state"] == "queued" and not world.core.jobs
    world.db.run("UPDATE projects SET status='active' WHERE id=?", (world.project["id"],))
    await world.core.runner.tick()
    await asyncio.gather(*list(world.core.jobs.values()))
    assert world.tasks.get(other["id"])["state"] == "done"


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
        return await scheduler.manager_action(project, {"tool": "manager_action", "callId": f"c{number}",
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
            await scheduler.manager_action(project, {"tool": "manager_action", "internal": True, "callId": "x",
                                                     "manager_execution_id": "m-i", "conversation_id": conversation,
                                                     "arguments": {"action": action, "task_id": task["id"], "objective": "o"}})
    listed = await act("list_tasks")
    assert listed["assignments"][0]["status"] == "queued" and listed["assignments"][0]["rounds"] == 0
    read = await act("read_task", task_id=task["id"])
    assert "checks" in read and "checkpoints" not in read
    before = git(world.app, "rev-parse", "main")
    done = await run_next(world)
    assert done["state"] == "done" and done["merge"]["state"] == "merged"
    with pytest.raises(ValueError, match="already merged"):
        await act("deploy", task_id=done["id"])
    git(world.app, "reset", "-q", "--hard", before)  # an older task whose branch never reached main
    world.db.run("UPDATE tasks SET merge=? WHERE id=?", (json.dumps({"state": "skipped"}), done["id"]))
    assert (await act("deploy", task_id=done["id"]))["merge"] == "requested"


@pytest.mark.parametrize('receipt', ['missing', 'completed', 'failed'])
def test_abandoned_lease_recovery_retains_stage_and_bounds_retries(world, receipt):
    task = world.tasks.add(world.project['id'], 'retained work')
    claimed = world.tasks.claim()
    eid = claimed['lease_owner']
    world.db.run("UPDATE tasks SET stage='merge',rounds=2 WHERE id=?", (task['id'],))
    if receipt == 'missing':
        world.db.run('DELETE FROM executions WHERE id=?', (eid,))
    else:
        world.db.run('UPDATE executions SET status=?,finished_at=? WHERE id=?', (receipt, time.time(), eid))
    world.core.runner.reconcile_leases()
    recovered = world.tasks.get(task['id'])
    assert recovered['state'] == 'queued' and recovered['lease_owner'] is None
    assert recovered['stage'] == 'merge' and recovered['rounds'] == 2 and recovered['requeues'] == 1
    assert world.db.one("SELECT 1 FROM events WHERE kind='task.lease_recovered' AND task_id=?", (task['id'],))
    next_run = world.tasks.claim()
    world.db.run("UPDATE executions SET status='failed',finished_at=? WHERE id=?", (time.time(), next_run['lease_owner']))
    world.core.runner.reconcile_leases()
    final = world.tasks.get(task['id'])
    assert final['state'] == 'failed' and final['lease_owner'] is None
    assert final['rounds'] == 2 and len(world.tasks.list()) == 1


def test_terminal_receipt_cannot_release_a_still_owned_runner_or_process(world, monkeypatch):
    task = world.tasks.add(world.project['id'], 'finishing worker')
    claimed = world.tasks.claim()
    eid = claimed['lease_owner']
    world.db.run("UPDATE executions SET status='completed',finished_at=? WHERE id=?", (time.time(), eid))
    world.core.jobs[eid] = SimpleNamespace(done=lambda: False)
    world.core.runner.reconcile_leases()
    assert world.tasks.get(task['id'])['lease_owner'] == eid
    world.core.jobs.clear()
    world.core.servers[eid] = object()
    world.core.runner.reconcile_leases()
    assert world.tasks.get(task['id'])['lease_owner'] == eid
    world.core.servers.clear()
    monkeypatch.setattr(runner_module, 'owned_alive', lambda row: True)
    world.core.runner.reconcile_leases()
    assert world.tasks.get(task['id'])['lease_owner'] == eid
    monkeypatch.setattr(runner_module, 'owned_alive', lambda row: False)
    world.core.runner.reconcile_leases()
    assert world.tasks.get(task['id'])['lease_owner'] is None


def test_deployment_fence_preserves_concurrently_created_queued_work(world):
    from concurrent.futures import ThreadPoolExecutor

    from ai_manager import fence

    with fence.hold(world.settings.data_dir, exclusive=True):
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = list(pool.map(lambda n: world.tasks.add(world.project['id'], f'Concurrent {n}'), range(4)))
        assert len(tasks) == 4 and world.tasks.claim() is None
        assert not world.db.all('SELECT * FROM executions')
        assert all(task['state'] == 'queued' for task in world.tasks.list())
    claimed = world.tasks.claim()
    assert claimed and claimed['id'] in {task['id'] for task in tasks}
    assert len(world.tasks.list()) == 4 and world.tasks.claim() is None

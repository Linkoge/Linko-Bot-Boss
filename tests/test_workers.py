import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from fake_codex import FakeServer

from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers
from ai_manager.workspace import Workspace, safe_file


@pytest.fixture
def worker_env(tmp_path):
    root = tmp_path / "projects"
    source = root / "sample"
    source.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "README.md").write_text("# Original committed content\n")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    settings = Settings(data_dir=tmp_path / "data", project_roots=(root,))
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    project = registry.add(source)
    return settings, db, registry, project, source


async def test_real_workspace_and_worker_claim_verification(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Read context", "Report verified repository context")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    with pytest.raises(ValueError):
        await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    execution = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
    assert execution["thread_id"] and execution["turn_id"]
    assert execution["status"] == "verifying"
    assert Tasks(db).get(task["id"])["status"] == "verifying"
    workspace = Path(execution["worktree"])
    assert workspace != source and (workspace / ".git").is_dir()
    (workspace / "README.md").write_text("Isolated change")
    assert (source / "README.md").read_text().startswith("# Original")
    await workers.close()


class InterruptedServer(FakeServer):
    async def request(self, method, params, timeout=30):
        if method == "turn/start":
            self.active_thread = params["threadId"]
            return {"turn": {"id": "held-turn"}}
        if method == "turn/interrupt":
            self.queues[self.active_thread].put_nowait(
                {
                    "method": "turn/completed",
                    "params": {"threadId": self.active_thread, "turn": {"id": "held-turn", "status": "interrupted"}},
                }
            )
        return {}


async def test_interrupt_and_lost_transport_are_distinct(worker_env):
    settings, db, registry, project, source = worker_env
    tasks = Tasks(db)
    workers = Workers(db, registry, settings, InterruptedServer)
    first = tasks.add(project["id"], "Safe interruption", "Pause confirmed")
    job = await workers.start(first["id"])
    for _ in range(100):
        if db.one("SELECT turn_id FROM executions WHERE id=?", (job["execution_id"],))["turn_id"]:
            break
        await asyncio.sleep(0.01)
    await workers.interrupt(job["execution_id"])
    await workers.jobs[job["execution_id"]]
    assert tasks.get(first["id"])["status"] == "paused"
    second = tasks.add(project["id"], "Unknown outcome", "Reconcile")
    job = await workers.start(second["id"])
    for _ in range(100):
        if db.one("SELECT turn_id FROM executions WHERE id=?", (job["execution_id"],))["turn_id"]:
            break
        await asyncio.sleep(0.01)
    server = workers.servers[job["execution_id"]]
    server.queues[server.active_thread].put_nowait({"method": "transport/lost", "params": {}})
    await workers.jobs[job["execution_id"]]
    assert tasks.get(second["id"])["status"] == "blocked"
    assert "reconcile" in tasks.get(second["id"])["blocker"]
    await workers.close()


def test_workspace_rejects_secrets_traversal_symlinks_and_stale_write(worker_env):
    settings, db, registry, project, source = worker_env
    workspace = Workspace(settings.data_dir, "fixture")
    workspace.prepare(source)
    for path in ("../manager.sqlite3", "/etc/passwd", ".git/config", ".env", ".env.production", "nested/.ssh/config"):
        with pytest.raises(ValueError):
            safe_file(workspace.root, path)
    (workspace.root / "escape").symlink_to(db.path)
    with pytest.raises(ValueError):
        workspace.read_file("escape")
    read = workspace.read_file("README.md")
    workspace.write_file("README.md", "changed", read["sha256"])
    with pytest.raises(ValueError, match="changed"):
        workspace.write_file("README.md", "stale", read["sha256"])


@pytest.mark.host_runtime
async def test_controlled_test_has_os_boundary(worker_env):
    settings, db, registry, project, source = worker_env
    workspace = Workspace(settings.data_dir, "fixture")
    workspace.prepare(source)
    result = await workspace.isolated_test(
        [
            "python3",
            "-c",
            "import os,socket; assert not os.path.exists('/home/codex'); s=socket.socket(); assert s.connect_ex(('1.1.1.1',443)) != 0; print('BOUNDARY_VERIFIED')",
        ]
    )
    assert result["exit_code"] == 0 and "BOUNDARY_VERIFIED" in result["output"]


def test_worker_restart_does_not_replay_unknown_outcomes(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Interrupted", "Reconcile before retry")
    claimed = Tasks(db).claim("unknown-worker")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES('lost',?,?,?,'running',?)",
            (task["id"], project["id"], project["agent_id"], time.time()),
        )
    Workers(db, registry, settings, FakeServer).recover()
    assert Tasks(db).get(claimed["id"])["status"] == "blocked"
    assert db.one("SELECT status FROM executions WHERE id='lost'")["status"] == "blocked"
    assert Tasks(db).claim("new-worker") is None

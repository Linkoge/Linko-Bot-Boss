"""Queue -> worker -> checks -> merge -> deploy script -> health, against the disposable runtime of test_deploy_script.

The task core runs in-process on a second database file in the runtime's data directory (same checkout fence and deploy
request files as the service); the service and scripts/deploy.py are the real ones, running a private copy of this code.
"""
import asyncio
import json
import sqlite3
import time

import pytest
from test_core import PASS, Lab
from test_core import git as git_
from test_deploy_script import deploy_script, git
from test_deploy_script import runtime as runtime_fixture

from ai_manager import pipeline as pipeline_module
from ai_manager.config import Settings
from ai_manager.core import Core
from ai_manager.db import Database
from ai_manager.pipeline import Pipeline
from ai_manager.registry import Registry

runtime = runtime_fixture
BREAK_HEALTH = ('"scheduler_running": bool(scheduler.job and not scheduler.job.done()),', '"scheduler_running": False,')


class Flow:
    def __init__(self, runtime, monkeypatch, gate=None):
        self.runtime = runtime
        settings = Settings(data_dir=runtime.data, project_roots=(runtime.root,))
        settings.prepare()
        self.db = Database(runtime.data / "core.sqlite3")  # not the service's database; same directory
        registry = Registry(self.db, (runtime.root,))
        self.project = registry.add(runtime.repo)
        monkeypatch.setattr(pipeline_module, "ROOT", runtime.repo.resolve())
        self.pipeline = Pipeline(self.db, registry)
        self.pipeline.set(self.project["id"], True)
        # scripts/deploy.py reads the service database for the owner's Auto-deploy setting and the project status.
        with sqlite3.connect(runtime.data / "manager.sqlite3") as conn:
            conn.execute("INSERT INTO projects(id,name,path,agent_id,created_at,updated_at) VALUES(?,?,?,?,1,1)",
                         (self.project["id"], "live", str(runtime.repo), "agent"))
            conn.execute("INSERT INTO settings VALUES(?,?)", ("auto_deploy:" + self.project["id"], json.dumps({"enabled": True})))
        self.lab = Lab()
        self.core = Core(self.db, registry, settings, self.pipeline, self.lab.factory, gate=gate or self.lab.gate)

    async def run_task(self, objective, edit):
        async def action(root, prompt):
            edit(root)
            git_(root, "add", "-A")
            git_(root, "commit", "-qm", objective)
            return "done"

        self.lab.actions.append(action)
        task = self.core.tasks.add(self.project["id"], objective)
        claimed = self.core.tasks.claim()
        assert claimed["id"] == task["id"]
        self.core.launch(claimed)
        await asyncio.gather(*list(self.core.jobs.values()))
        return self.core.tasks.get(task["id"])


@pytest.mark.asyncio
async def test_full_flow_task_to_merge_to_deploy_to_healthy_service(runtime, monkeypatch):
    from ai_manager.core import run_gate  # the real checks: the private copy's own smoke suite, in the task worktree
    runtime.start()
    runtime.sql("INSERT INTO settings VALUES('survivor','1')")
    flow = Flow(runtime, monkeypatch, gate=run_gate)
    started = time.monotonic()
    task = await flow.run_task("document the pipeline", lambda root: (root / "README.md").write_text(
        (root / "README.md").read_text() + "\nDeployed by the task queue.\n"))
    assert task["state"] == "done" and task["merge"]["state"] == "merged" and task["rounds"] == 0, task["blocker"]
    assert time.monotonic() - started < 120
    merged = git(runtime.repo, "rev-parse", "main")
    assert merged == task["merge"]["main"] != runtime.base
    assert "Deployed by the task queue." in (runtime.repo / "README.md").read_text()
    request = deploy_script.read(runtime.data / "deploy" / "request.json")
    assert request["task_id"] == task["id"] and request["source"] == "auto"
    result = runtime.run()  # the single deploy script: idle wait, backup, restart, health check
    assert result["status"] == "success" and result["commit"] == merged and result["previous"] == runtime.base
    assert runtime.running() == merged
    assert runtime.sql("SELECT value FROM settings WHERE key='survivor'") == [("1",)]
    assert not flow.pipeline.pending()


@pytest.mark.asyncio
async def test_health_failure_after_a_merge_rolls_back_code_and_database(runtime, monkeypatch):
    runtime.start()
    flow = Flow(runtime, monkeypatch)
    flow.lab.results = [PASS]
    task = await flow.run_task("change that breaks the health check", lambda root: (root / "ai_manager/web.py").write_text(
        (root / "ai_manager/web.py").read_text().replace(*BREAK_HEALTH)))
    assert task["state"] == "done" and task["merge"]["state"] == "merged"
    bad = git(runtime.repo, "rev-parse", "main")
    runtime.sql("INSERT INTO settings VALUES('before-bad-deploy','kept')")
    runtime.config["health_seconds"] = 6
    result = runtime.run()
    assert result["status"] == "rolled_back" and result["commit"] == bad and result["previous"] == runtime.base
    assert git(runtime.repo, "rev-parse", "HEAD") == runtime.base == git(runtime.repo, "rev-parse", "main")
    assert runtime.running() == runtime.base
    assert runtime.sql("SELECT value FROM settings WHERE key='before-bad-deploy'") == [("kept",)]
    assert git(runtime.repo, "rev-parse", "linko-failed-" + result["id"]) == bad  # the failed work stays reachable
    assert git(runtime.repo, "branch", "--list", task["workspace_branch"])  # and so does the task branch

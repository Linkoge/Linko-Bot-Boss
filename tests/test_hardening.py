import asyncio
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_policies import claimed_capabilities, wait_approval
from test_web import client as client
from test_web import sign_in
from test_workers import worker_env as worker_env

from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.policies import Policies
from ai_manager.registry import git
from ai_manager.tasks import Tasks
from ai_manager.web import create_app
from ai_manager.workers import Workers
from ai_manager.workspace import Workspace


async def test_manager_document_retrieval_is_scoped_and_policy_enforced(worker_env):
    settings, db, registry, project, source = worker_env
    scheduler = Scheduler(
        db,
        registry,
        settings,
        Workers(db, registry, settings),
        Verifier(db, registry, settings),
        Planner(db, registry, settings),
    )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,project_id,agent_id,status,started_at) VALUES('read-context',?,'manager','running',?)",
            (project["id"], time.time()),
        )
    params = {
        "tool": "manager_action",
        "arguments": {"action": "read_document", "path": "README.md"},
        "manager_execution_id": "read-context",
        "callId": "read-doc",
    }
    result = await scheduler.manager_action(project["id"], params)
    assert result["content"] == (source / "README.md").read_text() and result["sha256"]
    for path in ("../outside", ".git/config", ".env"):
        params["arguments"]["path"] = path
        with pytest.raises(ValueError):
            await scheduler.manager_action(project["id"], params)
    params["arguments"]["path"] = "README.md"
    Policies(db).set(project["id"], "read_files", "DENY")
    assert (await scheduler.manager_action(project["id"], params))["content"] == result["content"]
    params["arguments"]["project_id"] = "another-project"
    with pytest.raises(ValueError, match="All Projects"):
        await scheduler.manager_action(project["id"], params)


def test_unfinished_role_turn_recovery_preserves_output_without_replay(worker_env):
    settings, db, registry, project, source = worker_env
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,project_id,agent_id,status,started_at,output,thread_id,turn_id) VALUES('orphan-plan',?,'planning','running',?,'partial proposal','saved-thread','saved-turn')",
            (project["id"], time.time()),
        )
    scheduler = Scheduler(
        db,
        registry,
        settings,
        Workers(db, registry, settings),
        Verifier(db, registry, settings),
        Planner(db, registry, settings),
    )
    scheduler.recover()
    row = db.one("SELECT * FROM executions WHERE id='orphan-plan'")
    assert row["status"] == "blocked" and row["finished_at"] and row["output"] == "partial proposal"
    assert row["thread_id"] == "saved-thread" and not Tasks(db).list()
    scheduler.recover()
    assert len(db.all("SELECT seq FROM events WHERE kind='orchestration.recovery_required'")) == 1


async def test_actual_worker_process_crash_keeps_state_and_does_not_replay(worker_env, tmp_path):
    settings, db, registry, project, source = worker_env
    script = tmp_path / "fake-codex"
    script.write_text("""#!/usr/bin/python3
import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 method=m['method']
 if method=='account/read': r={'account':{'type':'chatgpt'}}
 elif method=='model/list': r={'data':[{'model':'fixture','isDefault':True,'defaultReasoningEffort':'low','supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'medium'}]}],'nextCursor':None}
 elif method in ('thread/start','thread/resume'): r={'thread':{'id':'persisted-fixture-thread'}}
 elif method=='turn/start': r={'turn':{'id':'held-fixture-turn'}}
 else: r={}
 print(json.dumps({'id':m['id'],'result':r}),flush=True)
""")
    script.chmod(0o700)
    task = Tasks(db).add(project["id"], "Crash fixture", "Must block without replay")
    workers = Workers(db, registry, settings, lambda _: AppServer(replace(settings, codex_bin=str(script))))
    job = await workers.start(task["id"])
    for _ in range(200):
        execution = db.one("SELECT * FROM executions WHERE id=?", (job["execution_id"],))
        if execution["turn_id"]:
            break
        await asyncio.sleep(0.01)
    assert execution["thread_id"] == "persisted-fixture-thread"
    assert execution["pid"] and execution["process_start"]
    workers.servers[job["execution_id"]].process.kill()
    await asyncio.wait_for(workers.jobs[job["execution_id"]], 10)
    assert Tasks(db).get(task["id"])["status"] == "blocked"
    restarted = Workers(Database(settings.db_path), registry, settings)
    restarted.recover()
    assert Tasks(db).claim("new-process") is None
    assert (Path(execution["worktree"]) / "README.md").read_text() == (source / "README.md").read_text()


async def test_two_concurrent_projects_have_isolated_clones_and_global_cap(worker_env):
    settings, db, registry, project, source = worker_env
    projects = [project]
    for name in ("second", "third"):
        target = source.parent / name
        subprocess.run(["git", "clone", "-q", str(source), str(target)], check=True)
        projects.append(registry.add(target))
    tasks = [Tasks(db).add(p["id"], "Concurrent fixture", "Isolation preserved") for p in projects]
    workers = Workers(db, registry, settings, FakeServer)
    first, second = await workers.start(tasks[0]["id"]), await workers.start(tasks[1]["id"])
    with pytest.raises(ValueError, match="limit"):
        await workers.start(tasks[2]["id"])
    await asyncio.gather(*list(workers.jobs.values()))
    clones = [
        db.one("SELECT worktree FROM executions WHERE id=?", (j["execution_id"],))["worktree"] for j in (first, second)
    ]
    assert clones[0] != clones[1]
    Path(clones[0], "README.md").write_text("Only first workspace")
    assert Path(clones[1], "README.md").read_text().startswith("# Original")


async def test_catastrophic_approval_expiration_never_executes(worker_env):
    settings, db, registry, project, source = worker_env
    policies, capabilities, workspace, task = claimed_capabilities(worker_env)
    before = workspace.read_file("README.md")
    job = asyncio.create_task(
        policies.authorize(
            project["id"],
            task["id"],
            "capability-fixture",
            "destructive_operations",
            {"argv": ["rm", "-rf", "/workspace"]},
            "expired-call",
            "Delete repository",
        )
    )
    approval = await wait_approval(db)
    with db.transaction() as conn:
        conn.execute("UPDATE approvals SET expires_at=? WHERE id=?", (time.time() - 1, approval["id"]))
    with pytest.raises(ValueError, match="expired"):
        await job
    assert workspace.read_file("README.md")["content"] == before["content"]


def test_single_manager_lock_and_secure_cookies(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", project_roots=(), secure_cookie=True)
    first = create_app(settings, FakeServer(settings))
    with TestClient(first, base_url="https://testserver") as browser:
        token = (settings.data_dir / "setup-token").read_text()
        assert (
            browser.post(
                "/api/setup", json={"username": "owner", "password": "long-and-private-password", "token": token}
            ).status_code
            == 200
        )
        response = browser.post("/api/login", json={"username": "owner", "password": "long-and-private-password"})
        assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
        second = create_app(settings, FakeServer(settings))
        with pytest.raises(RuntimeError, match="Another manager"):
            with TestClient(second):
                pass
        assert browser.get("/api/portfolio").status_code == 200


def test_storage_guard_and_human_memory_queue(worker_env, monkeypatch):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Durable task summary", "Evidence required")
    exported = settings.data_dir / "memory" / project["id"] / "TASKS.md"
    assert "Durable task summary" in exported.read_text() and "[pending]" in exported.read_text()
    Tasks(db).transition(task["id"], "paused", task["revision"])
    assert "[paused]" in exported.read_text()
    assert "Current handoff" in (exported.parent / "HANDOFF.md").read_text()
    monkeypatch.setattr(
        "ai_manager.workspace.shutil.disk_usage", lambda _: SimpleNamespace(total=1000, used=990, free=10)
    )
    workspace = Workspace(settings.data_dir, "no-space")
    with pytest.raises(ValueError, match="storage"):
        workspace.prepare(source)
    assert not workspace.root.exists()


def test_verified_patch_contains_new_files_and_applies_to_original_baseline(client):
    browser, app, settings = client
    sign_in(browser, app, settings)
    source = settings.project_roots[0] / "one"
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "README.md").write_text("Original baseline\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    project = app.state.registry.add(source)
    tasks = Tasks(app.state.db)
    task = tasks.add(project["id"], "Patch fixture", "Patch applies cleanly")
    running = tasks.claim("patch-fixture")
    workspace = Workspace(settings.data_dir, "patch-fixture")
    workspace.prepare(source)
    (workspace.root / "README.md").write_text("Updated baseline\n")
    (workspace.root / "new_module.py").write_text("value = 42\n")
    with app.state.db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,finished_at,worktree,base_commit) VALUES('patch-fixture',?,?,'fixture','verifying',?,?,?,?)",
            (
                task["id"],
                project["id"],
                time.time(),
                time.time(),
                str(workspace.root),
                git(source, "rev-parse", "HEAD"),
            ),
        )
    verifying = tasks.transition(task["id"], "verifying", running["revision"])
    tasks.transition(
        task["id"], "completed", verifying["revision"], evidence={"fixture": "independent patch check below"}
    )
    response = browser.get("/api/executions/patch-fixture/patch")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    assert "new_module.py" in response.text and "Updated baseline" in response.text
    result = subprocess.run(
        ["git", "-C", str(source), "apply", "--check", "-"], input=response.content, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()
    assert not (source / "new_module.py").exists() and (source / "README.md").read_text() == "Original baseline\n"

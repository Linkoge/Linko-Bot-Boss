import asyncio
import json
import time

import pytest
from test_workers import worker_env as worker_env

from ai_manager.capabilities import Capabilities
from ai_manager.policies import Policies
from ai_manager.tasks import Tasks
from ai_manager.workspace import Workspace


def claimed_capabilities(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Controlled edit", "Independent evidence")
    Tasks(db).claim("capability-fixture")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at) VALUES('capability-fixture',?,?,'fixture','running',?)",
            (task["id"], project["id"], time.time()),
        )
    workspace = Workspace(settings.data_dir, "capability-fixture")
    workspace.prepare(source)
    return (
        Policies(db),
        Capabilities(db, settings, workspace, project["id"], task["id"], "capability-fixture"),
        workspace,
        task,
    )


async def wait_approval(db):
    for _ in range(100):
        row = db.one("SELECT * FROM approvals WHERE status='pending'")
        if row:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError("Approval was not persisted")


async def test_exact_catastrophic_approval_preserves_source(worker_env):
    settings, db, registry, project, source = worker_env
    policies, capabilities, workspace, task = claimed_capabilities(worker_env)
    args = {"argv": ["rm", "-rf", "/workspace"]}
    job = asyncio.create_task(
        policies.authorize(
            project["id"],
            task["id"],
            "capability-fixture",
            "destructive_operations",
            args,
            "exact-call",
            "Delete entire repository",
        )
    )
    approval = await wait_approval(db)
    assert workspace.read_file("README.md")["content"] == (source / "README.md").read_text()
    assert Tasks(db).get(task["id"])["status"] == "awaiting_approval"
    assert json.loads(approval["arguments"]) == args
    policies.decide(approval["id"], True)
    assert await job == approval["operation_key"]
    assert db.one("SELECT status FROM approvals")["status"] == "consumed"
    with pytest.raises(ValueError):
        policies.decide(approval["id"], True)
    assert db.one("SELECT kind FROM notifications")["kind"] == "approval"


async def test_policy_change_invalidates_catastrophic_approval(worker_env):
    settings, db, registry, project, source = worker_env
    policies, capabilities, workspace, task = claimed_capabilities(worker_env)
    original = workspace.read_file("README.md")
    job = asyncio.create_task(
        policies.authorize(
            project["id"],
            task["id"],
            "capability-fixture",
            "destructive_operations",
            {"argv": ["rm", "-rf", "/workspace"]},
            "stale-call",
            "Delete entire repository",
        )
    )
    approval = await wait_approval(db)
    policies.set(project["id"], "destructive_operations", "ALLOW")
    with pytest.raises(ValueError, match="invalidated"):
        await job
    assert policies.effective(project["id"], "destructive_operations")["mode"] == "ASK"
    assert workspace.read_file("README.md")["content"] == original["content"]
    with pytest.raises(ValueError):
        policies.decide(approval["id"], True)


def test_inheritance_and_unavailable_actions_require_risk_acceptance(worker_env):
    settings, db, registry, project, source = worker_env
    policies = Policies(db)
    policies.set(None, "edit_files", "DENY")
    assert policies.effective(project["id"], "edit_files")["mode"] == "ALLOW"
    policies.set(project["id"], "edit_files", "ALLOW")
    assert policies.effective(project["id"], "edit_files")["source"] == "global:autonomous"
    policies.set(project["id"], "edit_files", None)
    assert policies.effective(project["id"], "edit_files")["mode"] == "ALLOW"
    with pytest.raises(ValueError, match="risk"):
        policies.set(None, "paid_services", "ALLOW")
    policies.set(None, "paid_services", "ALLOW", risk_accepted=True)
    assert not policies.effective(None, "paid_services")["available"]


async def test_uncertain_operation_never_replays_and_commit_is_isolated(worker_env):
    settings, db, registry, project, source = worker_env
    policies, capabilities, workspace, task = claimed_capabilities(worker_env)
    policies.set(project["id"], "edit_files", "ALLOW")
    policies.set(project["id"], "create_commits", "ALLOW")
    original = workspace.read_file("README.md")
    await capabilities.perform(
        {
            "operation": "write",
            "path": "README.md",
            "content": "Committed scoped change",
            "expected_hash": original["sha256"],
        },
        "write-call",
    )
    commit = await capabilities.perform({"operation": "commit", "message": "Scoped checkpoint"}, "commit-call")
    assert commit["commit"] and (source / "README.md").read_text() == original["content"]
    assert db.one("SELECT count(*) AS n FROM operations WHERE state='completed'")["n"] == 2

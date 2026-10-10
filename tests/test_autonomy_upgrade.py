import asyncio
import json
import subprocess

import pytest
from fake_codex import FakeServer
from test_orchestration import ReviewServer
from test_policies import claimed_capabilities, wait_approval
from test_web import client as client
from test_web import sign_in
from test_workers import worker_env as worker_env

from ai_manager.db import Database
from ai_manager.integration import Integrator
from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.policies import DEFAULTS, Policies
from ai_manager.tasks import Tasks
from ai_manager.verification import VerificationCommands
from ai_manager.verification_strategies import inspect
from ai_manager.workers import Workers
from ai_manager.workspace import Workspace


def activate(policies, profile, project=None):
    proposal = policies.proposal(profile, project)
    return policies.activate(profile, project, proposal["proposal_hash"])


def test_profiles_preserve_legacy_overrides_and_stale_preview(worker_env):
    settings, db, registry, project, source = worker_env
    policies = Policies(db)
    assert policies.profile() == "legacy"
    assert policies.effective(project["id"], "edit_files")["mode"] == "ASK"
    preview = policies.proposal("high_autonomy")
    assert policies.profile() == "legacy"  # Preview never activates.
    policies.set(project["id"], "delete_files", "DENY")
    with pytest.raises(ValueError, match="proposal changed"):
        policies.activate("high_autonomy", None, preview["proposal_hash"])
    policies.set(None, "create_commits", "ASK")
    activate(policies, "high_autonomy")
    assert policies.effective(project["id"], "edit_files")["mode"] == "ALLOW"
    assert policies.effective(project["id"], "delete_files")["mode"] == "DENY"
    assert policies.effective(project["id"], "create_commits")["mode"] == "ASK"
    policies.set(project["id"], "create_commits", "ALLOW")
    assert policies.effective(project["id"], "create_commits")["mode"] == "ALLOW"
    activate(policies, "conservative", project["id"])
    assert policies.effective(project["id"], "edit_files")["mode"] == "ASK"
    assert policies.effective(project["id"], "launch_workers")["mode"] == "ASK"
    activate(policies, "maximum_autonomy", project["id"])
    assert not policies.effective(project["id"], "change_network")["available"]
    # Profile selection and history survive fresh Database objects.
    assert Policies(Database(settings.db_path)).profile(project["id"]) == "maximum_autonomy"
    assert policies.history()[0]["kind"] == "policy.profile_activated"
    assert set(DEFAULTS) >= {
        "create_files",
        "launch_workers",
        "resume_sessions",
        "create_branches",
        "destructive_operations",
    }


async def test_conservative_launch_approval_and_deny_before_execution(worker_env):
    settings, db, registry, project, source = worker_env
    policies = Policies(db)
    activate(policies, "conservative", project["id"])
    task = Tasks(db).add(project["id"], "Inspect", "Evidence")
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    grant = await wait_approval(db)
    assert grant["action"] == "launch_workers"
    assert not db.one("SELECT thread_id FROM executions")["thread_id"]
    assert not (settings.data_dir / "workspaces" / job["execution_id"]).exists()
    policies.decide(grant["id"], False)
    await workers.jobs[job["execution_id"]]
    assert Tasks(db).get(task["id"])["status"] == "blocked"
    new = Tasks(db).add(project["id"], "Forbidden", "Not launched")
    policies.set(project["id"], "launch_workers", "DENY")
    with pytest.raises(ValueError, match="denies"):
        await workers.start(new["id"])
    assert len(db.all("SELECT * FROM executions")) == 1
    await workers.close()


async def test_create_is_distinct_from_edit_and_workers_cannot_administer_policy(worker_env):
    settings, db, registry, project, source = worker_env
    policies, capability, workspace, task = claimed_capabilities(worker_env)
    policies.set(project["id"], "edit_files", "ALLOW")
    policies.set(project["id"], "create_files", "DENY")
    with pytest.raises(ValueError, match="denies create_files"):
        await capability.perform(
            {"operation": "write", "path": "new.md", "expected_hash": "missing", "content": "forbidden"}, "create"
        )
    assert not (workspace.root / "new.md").exists()
    for operation in ("activate_profile", "set_policy", "deploy", "shell", "push"):
        with pytest.raises(ValueError, match="Unknown capability"):
            await capability.perform({"operation": operation}, operation)
    with pytest.raises(ValueError):
        await capability.perform({"operation": "read", "path": "../../manager.sqlite3"}, "escape")


def test_discovery_approval_config_identity_revocation_and_scoped_batch(worker_env):
    settings, db, registry, project, source = worker_env
    (source / "package.json").write_text(json.dumps({"scripts": {"test": "node test.js", "build": "vite build"}}))
    (source / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts="-q"\n')
    (source / "Makefile").write_text("test:\n\tpython3 -m unittest\n")
    (source / "Cargo.toml").write_text('[package]\nname="sample"\n')
    (source / "go.mod").write_text("module example.com/fixture\n")
    commands = VerificationCommands(db, registry)
    discovered = commands.discover(project["id"])
    assert not commands.approved(project["id"])
    argvs = [item["argv"] for item in discovered["candidates"]]
    assert ["npm", "test"] in argvs and ["make", "test"] in argvs
    assert ["go", "test", "./..."] in argvs and ["cargo", "test", "--offline"] in argvs
    selected = [item for item in discovered["candidates"] if item["argv"] == ["npm", "test"]][0]
    commands.decide(project["id"], [{"id": selected["id"], "status": "approved"}])
    assert commands.approved(project["id"])[0]["argv"] == ["npm", "test"]
    assert VerificationCommands(Database(settings.db_path), registry).approved(project["id"])
    assert commands.discover(project["id"])["candidates"]  # Unchanged discovery retains decisions.
    (source / "package.json").write_text('{"scripts":{"test":"node changed.js"}}')
    with pytest.raises(ValueError, match="renewed owner approval"):
        commands.approved(project["id"])
    assert any(c["status"] == "stale" for c in commands.snapshot(project["id"])["candidates"])
    commands.decide(project["id"], [{"id": selected["id"], "status": "revoked"}])
    assert not commands.approved(project["id"])
    # Cross-project IDs and incomplete batches cannot partially authorize commands.
    second_path = source.parent / "other"
    second_path.mkdir()
    second = registry.add(second_path)
    with pytest.raises(ValueError, match="scoped"):
        commands.decide(second["id"], [{"id": selected["id"], "status": "approved"}])
    candidate = commands.discover(project["id"])["candidates"][-1]
    with pytest.raises(ValueError):
        commands.decide(
            project["id"], [{"id": candidate["id"], "status": "rejected"}, {"id": "missing", "status": "approved"}]
        )
    assert db.one("SELECT status FROM verification_commands WHERE id=?", (candidate["id"],))["status"] != "rejected"


def test_discovery_preserves_old_owner_commands_and_rejects_symlink_config(worker_env):
    settings, db, registry, project, source = worker_env
    argv = ["python3", "-m", "unittest", "discover", "-v"]
    db.set_setting(f"verification_commands:{project['id']}", [argv])
    commands = VerificationCommands(db, registry)
    commands.discover(project["id"])
    assert commands.approved(project["id"])[0]["argv"] == argv
    (source / "pyproject.toml").symlink_to(settings.db_path)
    with pytest.raises(ValueError, match="Symlink"):
        commands.discover(project["id"])


@pytest.mark.parametrize(
    "kind,body,spec",
    [
        ("documentation", "# Guide\n## Usage\nFollow steps.\n", {"files": ["README.md"], "sections": ["Usage"]}),
        (
            "research",
            "# Findings\nEvidence from https://example.com/source.\nLimitations: small sample.\n",
            {"files": ["README.md"]},
        ),
        (
            "investigation",
            "# Investigation\nObserved failure.\n",
            {"files": ["README.md"], "evidence_file": "evidence.json"},
        ),
        (
            "infrastructure",
            "# State\nIsolated fixture service observation.\n",
            {"files": ["README.md"], "evidence_file": "evidence.json"},
        ),
    ],
)
async def test_task_specific_evidence_and_independent_review(worker_env, kind, body, spec):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(
        project["id"], "Assess fixture", "README supports acceptance", kind=kind, verification_spec=spec
    )
    workers = Workers(db, registry, settings, FakeServer)
    if kind == "infrastructure":
        VerificationCommands(db, registry).register(
            project["id"], [["python3", "-c", "print('fixture health passed')"]]
        )
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    workspace = Workspace(settings.data_dir, job["execution_id"])
    old = workspace.read_file("README.md")
    workspace.write_file("README.md", body, old["sha256"])
    if kind in {"investigation", "infrastructure"}:
        evidence = {
            "observations": ["fixture state"],
            "health_checks": ["actual command stdout"],
            "rollback": "restore fixture",
            "reproduction": "fixture command",
            "root_cause_or_uncertainty": "known fixture failure",
            "proposed_fix": "bounded fix",
        }
        workspace.write_file("evidence.json", json.dumps(evidence), "missing")
    assert await Verifier(db, registry, settings, ReviewServer).verify(task["id"])
    verified = Tasks(db).get(task["id"])
    assert verified["status"] == "completed" and verified["verification_status"] == "verified"
    recorded = json.loads(db.one("SELECT evidence FROM verification_results WHERE passed=1")["evidence"])
    assert recorded["strategy"]["kind"] == kind and recorded["content_snapshot"]
    await workers.close()


async def test_documentation_unrelated_change_and_research_claims_fail(worker_env):
    settings, db, registry, project, source = worker_env
    workspace = Workspace(settings.data_dir, "strategy-failures")
    workspace.prepare(source)
    base = subprocess.check_output(["git", "-C", str(workspace.root), "rev-parse", "HEAD"], text=True).strip()
    task = {"kind": "documentation", "verification_spec": json.dumps({"files": ["README.md"]})}
    workspace.write_file("unexpected.py", "changed", "missing")
    with pytest.raises(ValueError, match="unrelated"):
        inspect(task, workspace, base, [])
    task["kind"] = "research"
    with pytest.raises(ValueError, match="sources"):
        inspect(task, workspace, base, [])
    task["kind"] = "infrastructure"
    task["verification_spec"] = json.dumps({"files": ["README.md"], "evidence_file": "evidence.json"})
    workspace.write_file(
        "evidence.json",
        json.dumps({"observations": "claim", "health_checks": "claim", "rollback": "restore"}),
        "missing",
    )
    with pytest.raises(ValueError, match="independently observed"):
        inspect(task, workspace, base, [])


async def test_missing_configuration_recovers_without_worker_replay_or_duplicate(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Inspect README", "Original content")
    workers = Workers(db, registry, settings, FakeServer)
    verifier = Verifier(db, registry, settings, ReviewServer)
    scheduler = Scheduler(db, registry, settings, workers, verifier, Planner(db, registry, settings))
    db.set_setting("scheduler_enabled", True)
    await asyncio.gather(scheduler.tick(), scheduler.tick())
    jobs = list(workers.jobs.values())
    await asyncio.gather(*jobs)
    assert not await verifier.verify(task["id"])
    assert Tasks(db).get(task["id"])["blocker_code"] == "missing_verification"
    VerificationCommands(db, registry).register(project["id"], [["python3", "-c", "print('pass')"]])
    await scheduler.tick()
    await scheduler.tick()
    await asyncio.gather(*list(scheduler.reviews.values()))
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert len(db.all("SELECT * FROM executions WHERE worktree IS NOT NULL")) == 1
    assert db.one("SELECT payload FROM events WHERE kind='task.blocker_resolved'")
    assert db.one(
        "SELECT payload FROM events WHERE kind='task.transition' AND json_extract(payload,'$.blocker_code')='missing_verification'"
    )
    await scheduler.close()
    await workers.close()


async def test_uncertain_execution_is_never_automatically_recovered(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(project["id"], "Uncertain", "Reconcile")
    task = Tasks(db).transition(
        task["id"], "blocked", task["revision"], blocker="Transport lost", blocker_code="launch_policy"
    )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO executions(id,task_id,project_id,agent_id,status,thread_id,started_at,finished_at) VALUES('uncertain',?,?,'worker','blocked','persisted',1,2)",
            (task["id"], project["id"]),
        )
    workers = Workers(db, registry, settings, FakeServer)
    scheduler = Scheduler(
        db, registry, settings, workers, Verifier(db, registry, settings), Planner(db, registry, settings)
    )
    db.set_setting("scheduler_enabled", True)
    await scheduler.tick()
    assert Tasks(db).get(task["id"]) == task
    assert not workers.jobs
    await scheduler.close()


async def test_verification_sandbox_readonly_network_home_resources_and_timeout(worker_env):
    settings, db, registry, project, source = worker_env
    workspace = Workspace(settings.data_dir, "sandbox-v2")
    workspace.prepare(source)
    result = await workspace.isolated_test(
        [
            "python3",
            "-c",
            "import os,socket; assert not os.path.exists('/home/codex'); assert socket.socket().connect_ex(('1.1.1.1',443))!=0; print('isolated')",
        ]
    )
    assert result["exit_code"] == 0 and result["resources"]["cgroup_tasks_max"] == 128
    result = await workspace.isolated_test(["python3", "-c", "open('README.md','w').write('evil')"])
    assert result["exit_code"] != 0 and workspace.read_file("README.md")["content"].startswith("# Original")
    with pytest.raises(TimeoutError):
        await workspace.isolated_test(["python3", "-c", "import time; time.sleep(10)"], timeout=0.3)
    with pytest.raises(ValueError):
        await workspace.isolated_test(["python3"], cwd="../../")


async def test_verified_commit_and_merge_require_project_policy_and_content_identity(worker_env):
    settings, db, registry, project, source = worker_env
    task = Tasks(db).add(
        project["id"],
        "Update README",
        "README inspected",
        kind="documentation",
        verification_spec={"files": ["README.md"]},
    )
    policies = Policies(db)
    activate(policies, "high_autonomy", project["id"])
    target_branch = subprocess.check_output(["git", "-C", str(source), "branch", "--show-current"], text=True).strip()
    db.set_setting(
        f"integration_policy:{project['id']}", {"auto_commit": True, "auto_merge": True, "target_branch": target_branch}
    )
    workers = Workers(db, registry, settings, FakeServer)
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    workspace = Workspace(settings.data_dir, job["execution_id"])
    before = workspace.read_file("README.md")
    workspace.write_file("README.md", "# Original\nVerified local integration.\n", before["sha256"])
    assert await Verifier(db, registry, settings, ReviewServer).verify(task["id"])
    result = db.one("SELECT * FROM integration_results")
    assert result["status"] == "completed" and "Verified local integration" in (source / "README.md").read_text()
    await Integrator(db, registry, settings).integrate(Tasks(db).get(task["id"]))
    assert len(db.all("SELECT * FROM integration_results")) == 1
    await workers.close()


def test_authenticated_profiles_commands_and_integration_routes(client):
    browser, app, settings = client
    for path in ("/api/autonomy", "/api/autonomy/proposal?profile=high_autonomy", "/api/verification"):
        assert browser.get(path).status_code == 401
    sign_in(browser, app, settings)
    headers = {"X-CSRF-Token": browser.headers["X-CSRF-Token"]}
    project = browser.post("/api/projects", json={"path": str(settings.project_roots[0] / "one")}).json()
    pid = project["id"]
    preview = browser.get("/api/autonomy/proposal?profile=high_autonomy").json()
    body = {"profile": "high_autonomy", "proposal_hash": preview["proposal_hash"]}
    assert browser.post("/api/autonomy/activate", json=body, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert browser.get("/api/autonomy").json()["active_profile"] == "legacy"
    assert browser.post("/api/autonomy/activate", json=body, headers=headers).status_code == 200
    assert browser.get("/api/autonomy").json()["active_profile"] == "high_autonomy"
    discovery = browser.post(f"/api/projects/{pid}/verification/discover", headers=headers)
    assert discovery.status_code == 200
    assert (
        browser.put(f"/api/projects/{pid}/integration", json={"auto_merge": True}, headers=headers).status_code == 409
    )

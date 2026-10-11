"""Disposable repositories exercise the global policy and real OS command runner."""

import asyncio
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from fake_codex import FakeServer
from test_policies import claimed_capabilities, wait_approval
from test_workers import worker_env as worker_env

from ai_manager.orchestration import Planner, Scheduler, Verifier
from ai_manager.policies import ROUTINE, Policies
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers
from ai_manager.workspace import Workspace


class AutonomousWorker(FakeServer):
    async def request(self, method, params, timeout=30):
        if method == "turn/start":
            handler = self.tool_handlers[params["threadId"]]
            task = json.loads(params["input"][0]["text"])["task"]

            async def invoke(args, call):
                return await handler({"tool": "manager_workspace", "arguments": args, "callId": call})

            if "Hello" in task["description"]:
                await invoke(
                    {"operation": "write", "path": "hello.txt", "content": "Hello World\n", "expected_hash": "missing"},
                    "hello",
                )
            else:
                old = await invoke({"operation": "read", "path": "calculator.py"}, "read")
                await invoke(
                    {
                        "operation": "write",
                        "path": "calculator.py",
                        "content": "def add(a, b):\n    return a + b\n",
                        "expected_hash": old["sha256"],
                    },
                    "fix",
                )
                # Exercise ordinary Git plus shell/build/investigation through the actual broker.
                result = await invoke(
                    {"operation": "command", "argv": ["git", "checkout", "-b", "fixture-fix"]}, "branch"
                )
                assert result[0]["exit_code"] == 0
                result = await invoke(
                    {"operation": "test", "argv": ["python", "-m", "unittest", "discover", "-v"]}, "tests"
                )
                assert result[0]["exit_code"] == 0
        return await super().request(method, params, timeout)


class IndependentReview(FakeServer):
    async def request(self, method, params, timeout=30):
        if method != "turn/start":
            return {}
        context = json.loads(params["input"][0]["text"])
        hello = "Hello" in context["task"]["description"]
        path = "hello.txt" if hello else "calculator.py"
        result = await self.tool_handlers[params["threadId"]](
            {"tool": "manager_workspace", "arguments": {"operation": "read", "path": path}, "callId": "review"}
        )
        assert (result["content"] == "Hello World\n") if hello else ("return a + b" in result["content"])
        if context["task"]["kind"] == "code":
            assert context["test_evidence"]
        assert all(r["exit_code"] == 0 for r in context["test_evidence"])
        output = json.dumps(
            {"verified": True, "reason": "Independently inspected acceptance and passing checks", "files": [path]}
        )
        queue = self.queues[params["threadId"]]
        queue.put_nowait(
            {"method": "item/agentMessage/delta", "params": {"threadId": params["threadId"], "delta": output}}
        )
        queue.put_nowait(
            {
                "method": "turn/completed",
                "params": {"threadId": params["threadId"], "turn": {"id": "independent-review", "status": "completed"}},
            }
        )
        return {"turn": {"id": "independent-review"}}


def commit(source):
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
            "Fixture",
        ],
        check=True,
    )


@pytest.mark.host_runtime
@pytest.mark.parametrize("kind", ["code", "documentation"])
async def test_hello_world_and_new_project_need_no_registration_or_approval(worker_env, kind):
    settings, db, registry, first, source = worker_env
    # Stale restrictions from an existing installation must not need manual cleanup.
    db.set_setting("autonomy_profile:global", "conservative")
    Policies(db).set(first["id"], "create_files", "DENY")
    second_source = source.parent / "new-project"
    subprocess.run(["git", "clone", "-q", str(source), str(second_source)], check=True)
    second = registry.add(second_source)
    paused = Tasks(db).add(first["id"], "Intentionally paused", "Preserve history")
    Tasks(db).transition(paused["id"], "paused", paused["revision"], blocker="Owner paused")
    before = Tasks(db).get(paused["id"])
    for project in (first, second):
        assert all(Policies(db).effective(project["id"], action)["mode"] == "ALLOW" for action in ROUTINE)
        task = Tasks(db).add(
            project["id"],
            "Create Hello World",
            "hello.txt contains Hello World",
            kind=kind,
            verification_spec={"files": ["hello.txt"], "allowed_paths": ["hello.txt"]},
        )
        workers = Workers(db, registry, settings, AutonomousWorker)
        scheduler = Scheduler(
            db,
            registry,
            settings,
            workers,
            Verifier(db, registry, settings, IndependentReview),
            Planner(db, registry, settings),
        )
        db.set_setting("scheduler_enabled", True)
        await scheduler.tick()
        await asyncio.gather(*list(workers.jobs.values()))
        await scheduler.tick()
        await asyncio.gather(*list(scheduler.reviews.values()))
        assert Tasks(db).get(task["id"])["status"] == "completed"
        assert not (Path(project["path"]) / "hello.txt").exists()
        assert not db.all("SELECT * FROM approvals")
        assert not db.setting("verification_commands:" + project["id"])
        await scheduler.close()
        await workers.close()
    assert Tasks(db).get(paused["id"]) == before
    assert len(Tasks(db).list()) == 3


@pytest.mark.host_runtime
async def test_coding_execution_tests_and_independent_verification_without_setup(worker_env):
    settings, db, registry, project, source = worker_env
    (source / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (source / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2, 3), 5)\n"
    )
    commit(source)
    task = Tasks(db).add(project["id"], "Fix addition", "add(2, 3) is 5", verification_spec={"integration": "merge"})
    workers = Workers(db, registry, settings, AutonomousWorker)
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    assert Tasks(db).get(task["id"])["status"] == "verifying"
    assert await Verifier(db, registry, settings, IndependentReview).verify(task["id"])
    assert Tasks(db).get(task["id"])["status"] == "completed"
    assert "return a + b" in (source / "calculator.py").read_text()
    assert not db.all("SELECT * FROM approvals")
    evidence = json.loads(db.one("SELECT evidence FROM verification_results WHERE passed=1")["evidence"])
    assert any("unittest" in record["argv"] for record in evidence["tests"])
    assert all(record["input_files_unchanged"] for record in evidence["tests"])
    operations = db.all("SELECT * FROM operations")
    assert {op["action"] for op in operations} >= {"edit_files", "execute_commands", "run_tests"}
    assert all(op["state"] == "completed" for op in operations)
    command = next(json.loads(op["result"])[0] for op in operations if op["action"] == "execute_commands")
    assert Path(command["recovery_checkpoint"], "calculator.py").exists()
    assert len(Tasks(db).list()) == 1
    await workers.close()


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "/workspace"],
        ["bash", "-c", "rm -rf /"],
        ["psql", "-c", "DROP DATABASE production"],
        ["terraform", "destroy"],
    ],
)
async def test_catastrophic_command_waits_for_confirmation_before_execution(worker_env, argv):
    settings, db, registry, project, source = worker_env
    policies, capability, workspace, task = claimed_capabilities(worker_env)
    policies.set(project["id"], "destructive_operations", "ALLOW")
    job = asyncio.create_task(capability.perform({"operation": "command", "argv": argv}, "catastrophic"))
    grant = await wait_approval(db)
    assert grant["action"] == "destructive_operations"
    assert json.loads(grant["arguments"])["commands"][0]["argv"] == argv
    assert not db.all("SELECT * FROM operations")
    assert workspace.read_file("README.md")["content"] == (source / "README.md").read_text()
    policies.decide(grant["id"], False)
    with pytest.raises(ValueError, match="rejected"):
        await job
    assert not db.all("SELECT * FROM operations")


@pytest.mark.host_runtime
async def test_install_and_commands_have_no_host_data_and_keep_recovery_copy(worker_env):
    settings, db, registry, project, source = worker_env
    _, capability, workspace, _ = claimed_capabilities(worker_env)
    argv = [
        "python3",
        "-c",
        "from pathlib import Path; assert not Path('/home/codex').exists(); "
        "assert not Path('/run/user').exists(); Path('installed.txt').write_text('dependency fixture')",
    ]
    result = await capability.perform({"operation": "install", "argv": argv}, "install")
    assert result[0]["exit_code"] == 0 and result[0]["network"]
    assert (workspace.root / "installed.txt").exists()
    assert not (source / "installed.txt").exists()
    assert not Path(result[0]["recovery_checkpoint"], "installed.txt").exists()
    assert not db.all("SELECT * FROM approvals")


@pytest.mark.host_runtime
async def test_command_git_redirection_recovers_without_owner_intervention(worker_env):
    settings, db, registry, project, source = worker_env
    _, capability, workspace, _ = claimed_capabilities(worker_env)
    result = await capability.perform(
        {"operation": "command", "argv": ["git", "config", "core.worktree", "/home/codex/projects"]}, "redirect"
    )
    assert result[0]["exit_code"] == 1 and result[0]["git_metadata_recovered"]
    assert workspace.read_file("README.md")["content"] == (source / "README.md").read_text()
    assert not db.all("SELECT * FROM approvals")
    assert db.one("SELECT state FROM operations")["state"] == "completed"
    assert await capability.perform({"operation": "read", "path": "README.md"}, "after-recovery")


@pytest.mark.host_runtime
async def test_dependency_install_can_bootstrap_venv_and_install_a_package(worker_env):
    settings, db, registry, project, source = worker_env
    _, capability, workspace, _ = claimed_capabilities(worker_env)
    wheel = workspace.root / "tiny_pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tiny_pkg.py", "VALUE = 42\n")
        archive.writestr("tiny_pkg-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: tiny-pkg\nVersion: 1.0\n")
        archive.writestr(
            "tiny_pkg-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            "tiny_pkg-1.0.dist-info/RECORD",
            "tiny_pkg.py,,\ntiny_pkg-1.0.dist-info/METADATA,,\ntiny_pkg-1.0.dist-info/WHEEL,,\ntiny_pkg-1.0.dist-info/RECORD,,\n",
        )
    result = await capability.perform(
        {
            "operation": "install",
            "argv": [
                "bash",
                "-c",
                "python3 -m venv .venv && python -m pip install --no-index tiny_pkg-1.0-py3-none-any.whl "
                "&& python -c 'import tiny_pkg; assert tiny_pkg.VALUE == 42'",
            ],
        },
        "package-install",
    )
    assert result[0]["exit_code"] == 0, result[0]["output"]
    assert result[0]["network"]
    assert (workspace.root / ".venv/bin/python").exists()
    assert not (source / ".venv").exists()
    assert not db.all("SELECT * FROM approvals")


@pytest.mark.host_runtime
async def test_plain_code_without_suite_still_fails_independent_syntax_check(worker_env):
    from test_orchestration import ReviewServer

    settings, db, registry, project, source = worker_env
    workers = Workers(db, registry, settings, FakeServer)
    task = Tasks(db).add(project["id"], "Add plain Python file", "Python file is valid")
    job = await workers.start(task["id"])
    await workers.jobs[job["execution_id"]]
    workspace = Workspace(settings.data_dir, job["execution_id"])
    workspace.write_file("broken.py", "def broken(\n", "missing")
    assert not await Verifier(db, registry, settings, ReviewServer).verify(task["id"])
    assert "tests failed" in Tasks(db).get(task["id"])["blocker"]
    assert not db.all("SELECT * FROM approvals")
    await workers.close()


@pytest.mark.host_runtime
async def test_routine_command_mentioning_destroy_does_not_prompt(worker_env):
    _, db, _, _, _ = worker_env
    _, capability, _, _ = claimed_capabilities(worker_env)
    result = await capability.perform(
        {"operation": "command", "argv": ["python", "-c", "print('destroy')"]}, "ordinary"
    )
    assert result[0]["exit_code"] == 0
    assert not db.all("SELECT * FROM approvals")

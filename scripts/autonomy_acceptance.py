"""Real, disposable E2E authorized by the owner's isolated-fixture acceptance request.

Uses the unchanged legacy ASK policy. The fixture owner approves ONLY the fixed
calculator edit and its discovered unittest argv. No production policy activation,
source changes, host configuration, external pushes, deployments or API billing.
Evidence stays in a new private /tmp directory, retained for review; no replay.
"""

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
COMMAND = ["python3", "-m", "unittest", "discover", "-v"]
ORIGINAL = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"


def main():
    root = Path(tempfile.mkdtemp(prefix="linko-autonomy-e2e-"))
    root.chmod(0o700)
    source, data = root / "projects" / "fixture", root / "data"
    source.mkdir(parents=True)
    (source / "calculator.py").write_text(ORIGINAL)
    (source / "test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n        self.assertEqual(add(-2, 3), 1)\n"
    )
    (source / "README.md").write_text("# Calculator fixture\nVerification: `python3 -m unittest discover -v`.\n")
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "Acceptance fixture"],
    ):
        subprocess.run(argv, cwd=source, check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "AI_MANAGER_DATA": str(data),
        "AI_MANAGER_PROJECT_ROOTS": str(root / "projects"),
        "AI_MANAGER_SECURE_COOKIE": "0",
        "AI_MANAGER_CODEX": "/home/codex/.local/bin/codex",
    }
    log = (root / "service.log").open("w")
    evidence, process = {"fixture": str(root), "production_touched": False, "profile": "legacy", "steps": []}, None

    def start():
        p = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ai_manager.web:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO,
            env=env,
            stdout=log,
            stderr=log,
        )
        for _ in range(100):
            try:
                if httpx.get(url + "/api/auth-status").status_code == 200:
                    return p
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        p.terminate()
        p.wait(timeout=10)
        raise RuntimeError("Fixture service failed to start")

    def snapshot():
        with sqlite3.connect(data / "manager.sqlite3") as conn:
            conn.row_factory = sqlite3.Row
            return {
                table: [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
                for table in (
                    "tasks",
                    "executions",
                    "verification_commands",
                    "verification_results",
                    "approvals",
                    "operations",
                    "checkpoints",
                    "memory",
                )
            }

    def save():
        if (data / "manager.sqlite3").exists():
            evidence["records"] = snapshot()
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))

    try:
        process = start()
        with httpx.Client(base_url=url, timeout=30) as browser:
            credentials = {"username": "fixture-owner", "password": secrets.token_urlsafe(24)}
            r = browser.post("/api/setup", json={**credentials, "token": (data / "setup-token").read_text()})
            r.raise_for_status()
            r = browser.post("/api/login", json=credentials)
            r.raise_for_status()
            browser.headers["X-CSRF-Token"] = r.json()["csrf"]
            pid = browser.get("/api/portfolio").json()["projects"][0]["id"]
            assert browser.get("/api/autonomy").json()["active_profile"] == "legacy"
            discovered = browser.post(f"/api/projects/{pid}/verification/discover").json()
            candidate = next(c for c in discovered["candidates"] if c["argv"] == COMMAND)
            browser.post(
                f"/api/projects/{pid}/verification/decisions",
                json={"decisions": [{"id": candidate["id"], "status": "approved"}]},
            ).raise_for_status()
            browser.put(
                "/api/scheduler", json={"enabled": True, "project_id": pid, "limit": 1, "max_workers": 2}
            ).raise_for_status()
            request = (
                "Create exactly one code task: repair calculator.py addition in the isolated fixture. Read calculator.py and test_calculator.py first. Change only calculator.py to EXACT content: "
                + json.dumps(FIXED)
                + ". Invoke the manager_workspace write even if ASK and wait for its scoped approval. Do not change tests, create commits or delete files. Run the approved unittest command. Acceptance: add(2,3)==5 and add(-2,3)==1, test_calculator.py unchanged. Verification: approved unittest discovery and independent read-only inspection."
            )
            response = browser.post("/api/chat", json={"project_id": pid, "content": request, "mode": "execute"})
            response.raise_for_status()
            evidence["steps"].append({"natural_language_request": request, "receipt": response.json()})
            print("Fixture:", root, "chat accepted:", response.json()["execution_id"], flush=True)
            deadline, last, granted = time.monotonic() + 650, None, False
            while time.monotonic() < deadline:
                portfolio = browser.get("/api/portfolio").json()
                tasks = portfolio["tasks"]
                manager = next((e for e in portfolio["executions"] if e["id"] == response.json()["execution_id"]), None)
                if manager and manager["status"] in {"failed", "blocked"} and not tasks:
                    raise RuntimeError("Natural language planning failed: " + manager["error"])
                if tasks:
                    assert len(tasks) == 1
                    task = tasks[0]
                    if task["status"] != last:
                        print("Task:", task["id"], task["status"], task["blocker"], flush=True)
                        last = task["status"]
                        evidence["steps"].append({"task_id": task["id"], "status": last})
                        save()
                    for approval in portfolio["approvals"]:
                        args = json.loads(approval["arguments"])
                        assert (
                            not granted
                            and approval["action"] == "edit_files"
                            and args["path"] == "calculator.py"
                            and args["content"] == FIXED
                        )
                        assert args["expected_hash"] == __import__("hashlib").sha256(ORIGINAL.encode()).hexdigest()
                        assert (source / "calculator.py").read_text() == ORIGINAL
                        browser.post(
                            f"/api/approvals/{approval['id']}/decision", json={"approve": True}
                        ).raise_for_status()
                        granted = True
                        evidence["steps"].append({"exact_fixture_edit_approved": approval["id"]})
                    if last in {"completed", "blocked", "failed"}:
                        assert last == "completed", task
                        break
                time.sleep(0.3)
            else:
                raise TimeoutError("Real fixture acceptance exceeded its bounded time budget")
            assert granted
            records = snapshot()
            worker = next(e for e in records["executions"] if e["worktree"])
            assert worker["pid"] and worker["thread_id"] and worker["turn_id"]
            assert (Path(worker["worktree"]) / "calculator.py").read_text() == FIXED
            assert (source / "calculator.py").read_text() == ORIGINAL
            successful = next(v for v in records["verification_results"] if v["passed"])
            verified = json.loads(successful["evidence"])
            assert verified["tests"][0]["argv"] == COMMAND and verified["tests"][0]["exit_code"] == 0
            assert verified["review"]["verified"] and records["checkpoints"]
            assert any("Verified completion" in m["content"] for m in records["memory"] if m["name"] == "STATE.md")
            # Exercise DENY through the real worker broker with the same assigned fixture.
            sys.path.insert(0, str(REPO))
            import asyncio

            from ai_manager.capabilities import Capabilities
            from ai_manager.config import Settings
            from ai_manager.db import Database
            from ai_manager.tasks import Tasks
            from ai_manager.workspace import Workspace

            isolated_db = Database(data / "manager.sqlite3")
            denied = Tasks(isolated_db).add(pid, "Deny unauthorized deployment", "Operation must be blocked")
            Tasks(isolated_db).claim("denial-fixture")
            capability = Capabilities(
                isolated_db,
                Settings(data_dir=data, project_roots=(root / "projects",)),
                Workspace(data, worker["id"]),
                pid,
                denied["id"],
                "denial-fixture",
            )
            try:
                asyncio.run(capability.perform({"operation": "deploy"}, "forbidden-deploy"))
            except ValueError as error:
                evidence["unauthorized_operation"] = {"blocked": True, "reason": str(error)}
            else:
                raise AssertionError("Unauthorized deployment was not blocked")
            with isolated_db.transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET status='blocked',blocker='Unsupported deployment denied',lease_owner=NULL,lease_until=NULL WHERE id=?",
                    (denied["id"],),
                )
            before = snapshot()
            old_pid = process.pid
            process.terminate()
            process.wait(timeout=30)
            process = start()
            after = snapshot()
            assert before["tasks"] == after["tasks"] and before["executions"] == after["executions"]
            assert before["verification_commands"] == after["verification_commands"]
            evidence.update(
                {
                    "completed": True,
                    "source_preserved": True,
                    "restart": {"before_pid": old_pid, "after_pid": process.pid, "records_preserved": True},
                    "model": worker["model"],
                    "effort": worker["effort"],
                    "task_id": task["id"],
                }
            )
            print(
                "REAL PIPELINE PASSED: task, worker, exact ASK, approved isolated tests, independent review, memory, denied operation and restart",
                flush=True,
            )
    finally:
        save()
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=30)
        log.close()
        print("Evidence:", root / "evidence.json", flush=True)


if __name__ == "__main__":
    main()

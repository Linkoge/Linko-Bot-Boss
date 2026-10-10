"""Real browser → chat delegation → scheduler → Codex → exact ASK → verification.

Uses only /tmp/linko-reliability-acceptance and existing production policy defaults.
Records an exact owner decision only when an external approval marker is provided.
The operator must obtain explicit owner approval before creating that marker. It may be
provided at fixture-owner-approved.json, with the exact documented command and edit.
Without it, writes remain ASK and verification remains unconfigured.
"""

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright
from reliability_browser import snapshot

ROOT = Path("/tmp/linko-reliability-acceptance")
REPO = Path(__file__).resolve().parents[1]
COMMANDS = [["python3", "-m", "unittest", "discover", "-v"]]
EDIT = {
    "operation": "write",
    "path": "calculator.py",
    "expected_hash": "e1a894022d1a082987b87adecb623438c9e386d86b2b621cff4a5fe7fdf7edc8",
    "content": "def add(a, b):\n    return a + b\n",
}


def main():
    data = ROOT / "acceptance-data"
    assert not data.exists(), "Never replay an existing acceptance run"
    data.mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "AI_MANAGER_DATA": str(data),
        "AI_MANAGER_PROJECT_ROOTS": str(ROOT / "project"),
        "AI_MANAGER_CODEX": "/home/codex/.local/bin/codex",
        "AI_MANAGER_SECURE_COOKIE": "0",
        "PYTHONUNBUFFERED": "1",
    }
    log = (ROOT / "acceptance.log").open("w")

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
        raise RuntimeError("Fixture service failed to start")

    process = start()
    records = []
    approved_marker = ROOT / "fixture-owner-approved.json"
    evidence_path = ROOT / "acceptance-evidence.json"

    metadata = {}

    def save(extra=None):
        metadata.update(extra or {})
        with sqlite3.connect(data / "manager.sqlite3") as conn:
            conn.row_factory = sqlite3.Row
            evidence = {
                **snapshot(data),
                "approvals": [dict(r) for r in conn.execute("SELECT * FROM approvals")],
                "verification_results": [dict(r) for r in conn.execute("SELECT * FROM verification_results")],
                "events": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT seq,kind,task_id,execution_id,payload FROM events WHERE kind NOT LIKE '%delta%' ORDER BY seq"
                    )
                ],
                "http_records": records,
                **metadata,
            }
        evidence_path.write_text(json.dumps(evidence, indent=2))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.locator("#username").fill("fixture-owner")
            page.locator("#password").fill(secrets.token_urlsafe(24))
            page.locator("#setup-token").fill((data / "setup-token").read_text())
            page.locator("#login-button").click()
            page.locator("#app").wait_for(state="visible")
            page.locator("#projects .project-button").first.wait_for()
            page.locator("#projects .project-button").first.click()
            project_id = page.locator("#projects .selected").get_attribute("data-project")

            def api(method, path, body=None):
                result = page.evaluate(
                    """async ({method,path,body})=>{const auth=await (await fetch('/api/auth-status')).json();const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':auth.csrf},...(body?{body:JSON.stringify(body)}:{})});return {http:response.status,body:await response.json()};}""",
                    {"method": method, "path": path, "body": body},
                )
                records.append({"method": method, "path": path, "body": body, "response": result})
                return result

            configured = False

            def configure_if_approved():
                nonlocal configured
                if not approved_marker.exists():
                    return False
                owner = json.loads(approved_marker.read_text())
                assert owner["commands"] == COMMANDS and owner["edit"] == EDIT and owner["owner_approved"] is True
                if not configured:
                    assert api("PUT", f"/api/projects/{project_id}/verification", {"commands": COMMANDS})["http"] == 200
                    configured = True
                return True

            configure_if_approved()
            assert (
                api("PUT", "/api/scheduler", {"enabled": True, "project_id": project_id, "limit": 1, "max_workers": 2})[
                    "http"
                ]
                == 200
            )
            goal = 'Create exactly ONE task: fix the isolated calculator addition. Inspect calculator.py and test_calculator.py first. Change only calculator.py, using manager_workspace write with its current expected hash and EXACT content "def add(a, b):\\n    return a + b\\n". Tests must remain unchanged. Invoke the write capability even if it requires owner approval; wait for its exact ASK decision. Run the registered verification command, then report evidence. No commits, deletion, external services or unrelated work. Acceptance: add(2,3)==5 and add(-2,3)==1, unchanged tests. Verification: documented python3 -m unittest discover -v and independent read-only review.'
            page.locator("#chat-mode").select_option("execute")
            page.locator("#message-input").fill(goal)
            with page.expect_response(lambda r: r.url.endswith("/api/chat")) as response:
                page.locator("#send").click()
            r = response.value
            records.append(
                {
                    "method": "POST",
                    "path": "/api/chat",
                    "body": r.request.post_data_json,
                    "response": {"http": r.status, "body": r.json()},
                }
            )
            assert r.status == 200
            print("Owner browser command accepted:", r.json()["execution_id"], flush=True)
            deadline = time.monotonic() + 720
            reported = False
            while time.monotonic() < deadline:
                with sqlite3.connect(data / "manager.sqlite3") as conn:
                    conn.row_factory = sqlite3.Row
                    task = conn.execute("SELECT * FROM tasks ORDER BY created_at LIMIT 1").fetchone()
                    approval = conn.execute(
                        "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at LIMIT 1"
                    ).fetchone()
                if approval:
                    args = json.loads(approval["arguments"])
                    assert approval["action"] == "edit_files" and args == EDIT, args
                    if not reported:
                        print(
                            "REAL ASK BLOCKER:",
                            approval["id"],
                            "task:",
                            approval["task_id"],
                            "execution:",
                            approval["execution_id"],
                            flush=True,
                        )
                        page.locator('[data-view="approvals"]').click()
                        page.screenshot(path=str(ROOT / "acceptance-approval.png"), full_page=True)
                        save(
                            {
                                "current_external_blocker": "Owner approval required by edit_files=ASK for exact calculator.py edit"
                            }
                        )
                        reported = True
                    if configure_if_approved():
                        assert (
                            api("POST", f"/api/approvals/{approval['id']}/decision", {"approve": True})["http"] == 200
                        )
                        approved_marker.rename(ROOT / "fixture-owner-approval-consumed.json")
                        print("Exact owner-authorized edit approval recorded through HTTP", flush=True)
                if task and task["status"] in {"completed", "blocked", "failed"}:
                    print("Task terminal status:", task["id"], task["status"], task["blocker"], flush=True)
                    break
                time.sleep(0.3)
            else:
                raise RuntimeError(
                    "Acceptance reached its time budget with unresolved owner approval; inspect persisted records before continuing"
                )
            save({"page_errors": errors})
            assert task and task["status"] == "completed", dict(task) if task else "No task"
            page.reload()
            page.locator('[data-view="tasks"]').click()
            row = page.locator(".task-row").filter(has_text=task["id"][:8])
            assert row.locator(".badge.completed").inner_text() == "completed"
            page.screenshot(path=str(ROOT / "acceptance-completed.png"), full_page=True)
            before = snapshot(data)
            old_pid = process.pid
            process.terminate()
            process.wait(timeout=30)
            process = start()
            page.reload()
            page.locator('[data-view="tasks"]').click()
            row = page.locator(".task-row").filter(has_text=task["id"][:8])
            assert row.locator(".badge.completed").inner_text() == "completed"
            after = snapshot(data)
            assert before["tasks"] == after["tasks"] and before["executions"] == after["executions"]
            assert (ROOT / "project" / "calculator.py").read_text() == "def add(a, b):\n    return a - b\n"
            save(
                {
                    "page_errors": errors,
                    "completed": True,
                    "restart": {"old_pid": old_pid, "new_pid": process.pid, "records_preserved": True},
                    "source_preserved": True,
                }
            )
            print(
                "FULL PIPELINE PASSED including independent verification, dashboard and restart persistence", flush=True
            )
            assert not errors, errors
            browser.close()
    finally:
        save()
        process.terminate()
        process.wait(timeout=30)
        log.close()
        print("Evidence:", evidence_path, flush=True)


if __name__ == "__main__":
    main()

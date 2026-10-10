"""Capture real browser/API controls in an isolated fixture, with real Codex reads.

Usage: python scripts/reliability_browser.py [--baseline]
The baseline repository is extracted separately at /tmp/linko-reliability-baseline.
No production tasks, policies, or credentials are changed.
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

REPO = Path(__file__).resolve().parents[1]
ROOT = Path("/tmp/linko-reliability-acceptance")


def snapshot(data):
    with sqlite3.connect(data / "manager.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        return {
            name: [dict(row) for row in conn.execute(sql)]
            for name, sql in {
                "tasks": "SELECT id,status,blocker,revision,lease_owner FROM tasks",
                "executions": "SELECT id,task_id,status,pid,process_start,thread_id,turn_id,error,finished_at,worktree FROM executions",
                "scheduler": "SELECT key,value FROM settings WHERE key LIKE 'scheduler%'",
            }.items()
        }


def main():
    baseline = "--baseline" in sys.argv
    launch_failure = "--launch-failure" in sys.argv
    resume_case = "--resume" in sys.argv
    code = Path("/tmp/linko-reliability-baseline") if baseline else REPO
    label = (
        "baseline"
        if baseline
        else "fixed-failure"
        if launch_failure
        else "fixed-resume-confirmed"
        if resume_case
        else "fixed-v2"
    )
    data = ROOT / (label + "-data")
    assert not data.exists(), "Use a fresh isolated data directory; do not replay existing runs"
    data.mkdir(mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "AI_MANAGER_DATA": str(data),
        "AI_MANAGER_PROJECT_ROOTS": str(ROOT / "project"),
        "AI_MANAGER_CODEX": "/tmp/linko-intentionally-missing-codex"
        if launch_failure
        else "/home/codex/.local/bin/codex",
        "AI_MANAGER_SECURE_COOKIE": "0",
        "PYTHONUNBUFFERED": "1",
    }
    records, errors = [], []
    log = (ROOT / (label + ".log")).open("w")
    process = subprocess.Popen(
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
        cwd=code,
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/api/auth-status").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
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
                return page.evaluate(
                    """async ({method,path,body})=>{const auth=await (await fetch('/api/auth-status')).json();const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':auth.csrf},...(body?{body:JSON.stringify(body)}:{})});return {http:response.status,body:await response.json()};}""",
                    {"method": method, "path": path, "body": body},
                )

            def capture(name, method, path, body=None):
                before = snapshot(data)
                response = api(method, path, body)
                record = {
                    "action": name,
                    "request": {"method": method, "path": path, "body": body},
                    "response": response,
                    "before": before,
                    "after": snapshot(data),
                }
                records.append(record)
                return response["body"]

            if resume_case:
                capture(
                    "Require exact read approval in this isolated interruption fixture",
                    "PUT",
                    f"/api/policies/read_files?project_id={project_id}",
                    {"mode": "ASK"},
                )
            read = capture(
                "create harmless read task",
                "POST",
                "/api/tasks",
                {
                    "project_id": project_id,
                    "description": "Read calculator.py and README.md, report their contents. Do not edit, commit, test or delete files. This is an isolated read-only control test.",
                    "acceptance": "Report existing calculator implementation; no changes.",
                },
            )
            task_id = read["id"]
            page.reload()
            page.locator('[data-view="tasks"]').click()
            row = page.locator(".task-row").filter(has_text=task_id[:8])
            before = snapshot(data)
            with page.expect_response(lambda r: r.url.endswith(f"/api/tasks/{task_id}/start")) as response:
                row.get_by_role("button", name="Start worker", exact=True).click()
            r = response.value
            page.wait_for_function(
                "document.getElementById('notice').textContent.includes('Worker started')"
                if baseline
                else "document.getElementById('notice').textContent.startsWith('accepted:')"
            )
            records.append(
                {
                    "action": "browser Start worker",
                    "request": {"method": r.request.method, "path": f"/api/tasks/{task_id}/start"},
                    "response": {"http": r.status, "body": r.json()},
                    "before": before,
                    "after": snapshot(data),
                    "visible": page.locator("#notice").inner_text(),
                }
            )
            capture("already-running Start", "POST", f"/api/tasks/{task_id}/start")
            capture("already-running Resume", "POST", f"/api/tasks/{task_id}/resume", {"revision": 0})
            if resume_case:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    execution = snapshot(data)["executions"][0]
                    active = api("GET", "/api/portfolio")["body"]["tasks"][0]
                    if execution["turn_id"] and active["status"] == "awaiting_approval":
                        break
                    time.sleep(0.05)
                assert execution["turn_id"] and not execution["finished_at"] and active["status"] == "awaiting_approval"
                capture("Interrupt real active turn", "POST", f"/api/executions/{execution['id']}/interrupt")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    paused = api("GET", "/api/portfolio")["body"]["tasks"][0]
                    if paused["status"] == "paused" and paused["readiness"]["can_resume"]:
                        break
                    time.sleep(0.1)
                assert paused["status"] == "paused" and paused["readiness"]["can_resume"], paused
                capture(
                    "Restore inherited read policy after confirmed pause",
                    "PUT",
                    f"/api/policies/read_files?project_id={project_id}",
                    {"mode": None},
                )
                page.reload()
                page.locator('[data-view="tasks"]').click()
                row = page.locator(".task-row").filter(has_text=task_id[:8])
                before = snapshot(data)
                with page.expect_response(lambda r: r.url.endswith("/resume")) as response:
                    row.get_by_role("button", name="Resume persisted worker", exact=True).click()
                r = response.value
                page.wait_for_function("document.getElementById('notice').textContent.startsWith('accepted:')")
                records.append(
                    {
                        "action": "browser Resume confirmed interrupted worker",
                        "request": {
                            "method": "POST",
                            "path": f"/api/tasks/{task_id}/resume",
                            "body": r.request.post_data_json,
                        },
                        "response": {"http": r.status, "body": r.json()},
                        "before": before,
                        "after": snapshot(data),
                        "visible": page.locator("#notice").inner_text(),
                    }
                )
                assert r.status == 200
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                task = api("GET", "/api/portfolio")["body"]["tasks"][0]
                if task["status"] in {"blocked", "failed"}:
                    break
                time.sleep(0.5)
            assert task["status"] == "blocked", task
            assert (
                "No such file" in task["blocker"]
                if launch_failure
                else "trusted project verification" in task["blocker"]
            ), task
            if resume_case:
                executions = snapshot(data)["executions"]
                assert (
                    len(executions) == 2
                    and executions[0]["thread_id"] == executions[1]["thread_id"]
                    and executions[0]["worktree"] == executions[1]["worktree"]
                )
                assert (
                    executions[0]["turn_id"] != executions[1]["turn_id"]
                    and executions[0]["pid"] != executions[1]["pid"]
                )
            print(
                label,
                "confirmed launch failure" if launch_failure else "real worker finished and verification blocked",
                task_id,
                flush=True,
            )
            page.reload()
            page.locator('[data-view="tasks"]').click()
            row = page.locator(".task-row").filter(has_text=task_id[:8])
            page.screenshot(path=str(ROOT / (label + "-blocked.png")), full_page=True)
            if launch_failure:
                capture(
                    "Resume rejects launch without thread",
                    "POST",
                    f"/api/tasks/{task_id}/resume",
                    {"revision": task["revision"]},
                )
                with page.expect_response(lambda r: r.url.endswith("/retry")) as response:
                    row.get_by_role("button", name="Retry launch", exact=True).click()
                r = response.value
                records.append(
                    {
                        "action": "browser Retry confirmed launch failure",
                        "response": {"http": r.status, "body": r.json()},
                        "after": snapshot(data),
                    }
                )
                assert r.status == 200
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    task = api("GET", "/api/portfolio")["body"]["tasks"][0]
                    if task["status"] == "blocked":
                        break
                    time.sleep(0.1)
                assert task["status"] == "blocked"
                page.reload()
                page.locator('[data-view="agents"]').click()
                page.get_by_text("Execution error:", exact=False).first.wait_for()
                page.screenshot(path=str(ROOT / (label + "-error.png")), full_page=True)
            elif baseline:
                before = snapshot(data)
                with page.expect_response(lambda r: r.url.endswith("/resume")) as response:
                    row.get_by_role("button", name="Resume persisted worker", exact=True).click()
                r = response.value
                records.append(
                    {
                        "action": "browser Resume verification-blocked task",
                        "request": {
                            "method": r.request.method,
                            "path": f"/api/tasks/{task_id}/resume",
                            "body": r.request.post_data_json,
                        },
                        "response": {"http": r.status, "body": r.json()},
                        "before": before,
                        "after": snapshot(data),
                        "visible": page.locator("#notice").inner_text(),
                    }
                )
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    task = api("GET", "/api/portfolio")["body"]["tasks"][0]
                    if task["status"] == "blocked":
                        break
                    time.sleep(0.5)
                assert task["status"] == "blocked"
                assert len(snapshot(data)["executions"]) == 2
            else:
                assert row.get_by_role("button", name="Resume persisted worker", exact=True).count() == 0
                before = snapshot(data)
                with page.expect_response(lambda r: r.url.endswith("/verify")) as response:
                    row.get_by_role("button", name="Retry verification", exact=True).click()
                r = response.value
                records.append(
                    {
                        "action": "browser Retry verification missing configuration",
                        "response": {"http": r.status, "body": r.json()},
                        "before": before,
                        "after": snapshot(data),
                        "visible": page.locator("#notice").inner_text(),
                    }
                )
                assert r.status == 409 and snapshot(data) == before
                capture(
                    "Resume rejects completed worker",
                    "POST",
                    f"/api/tasks/{task_id}/resume",
                    {"revision": task["revision"]},
                )
            page.reload()
            page.locator('[data-view="tasks"]').click()
            row = page.locator(".task-row").filter(has_text=task_id[:8])
            before = snapshot(data)
            with page.expect_response(lambda r: r.request.method == "PATCH") as response:
                row.get_by_role("button", name="Cancel task", exact=True).click()
            r = response.value
            if not baseline:
                page.wait_for_function("document.getElementById('notice').textContent.includes('is cancelled')")
            records.append(
                {
                    "action": "browser Cancel blocked task",
                    "before": before,
                    "request": {"method": "PATCH", "path": f"/api/tasks/{task_id}", "body": r.request.post_data_json},
                    "response": {"http": r.status, "body": r.json()},
                    "visible": page.locator("#notice").inner_text(),
                    "after": snapshot(data),
                }
            )
            assert r.json()["status"] == "cancelled"
            capture("cancelled Start rejection", "POST", f"/api/tasks/{task_id}/start")
            capture(
                "cancelled Resume rejection", "POST", f"/api/tasks/{task_id}/resume", {"revision": task["revision"]}
            )
            prerequisite = capture(
                "create paused prerequisite",
                "POST",
                "/api/tasks",
                {"project_id": project_id, "description": "Paused fixture prerequisite", "acceptance": "Waiting"},
            )
            capture(
                "pause prerequisite",
                "PATCH",
                f"/api/tasks/{prerequisite['id']}",
                {"revision": prerequisite["revision"], "status": "paused"},
            )
            dependent = capture(
                "create dependent",
                "POST",
                "/api/tasks",
                {
                    "project_id": project_id,
                    "description": "Blocked by fixture dependency",
                    "acceptance": "Wait for prerequisite",
                    "dependencies": [prerequisite["id"]],
                },
            )
            capture("dependency-blocked Start", "POST", f"/api/tasks/{dependent['id']}/start")
            capture(
                "Resume without persisted thread", "POST", f"/api/tasks/{prerequisite['id']}/resume", {"revision": 1}
            )
            if not baseline:
                page.reload()
                page.locator('[data-view="tasks"]').click()
                page.get_by_text("Unfinished dependencies:", exact=False).wait_for()
                assert "loop running" in page.locator("#execution-status").inner_text()
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=30)
        log.close()
        evidence = {
            "baseline": baseline,
            "manager_pid": process.pid,
            "records": records,
            "page_errors": errors,
            "final": snapshot(data),
        }
        (ROOT / (label + "-controls.json")).write_text(json.dumps(evidence, indent=2))
        print("Evidence:", ROOT / (label + "-controls.json"), flush=True)


if __name__ == "__main__":
    main()

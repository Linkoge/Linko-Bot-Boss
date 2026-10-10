"""Check viewport-visible task feedback against real isolated API rejections.

The finished execution row is display-only fixture data, never execution evidence.
No worker is dispatched, and no production state or credentials are used.
Run python -m scripts.task_feedback_browser. Use --baseline to reproduce the
missing feedback with checkpoint 3aeefc1.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from ai_manager.db import Database
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks


def main():
    baseline = "--baseline" in sys.argv
    records = []
    with tempfile.TemporaryDirectory(prefix="linko-task-feedback-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        data = root / "data"
        db = Database(data / "manager.sqlite3")
        registry = Registry(db, (project,))
        registry.discover()
        project_id = db.one("SELECT id FROM projects")["id"]
        tasks = Tasks(db)
        prerequisite = tasks.add(
            project_id, "Prerequisite awaiting owner verification", "Owner configures trusted verification"
        )
        tasks.transition(
            prerequisite["id"],
            "blocked",
            prerequisite["revision"],
            blocker="Owner must configure trusted project verification commands before completion",
        )
        display_workspace = data / "workspaces" / "display-only"
        (display_workspace / ".git").mkdir(parents=True)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO executions(id,task_id,project_id,agent_id,status,started_at,finished_at,worktree) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "fixture-display-only",
                    prerequisite["id"],
                    project_id,
                    "fixture-worker",
                    "verifying",
                    time.time(),
                    time.time(),
                    str(display_workspace),
                ),
            )
        for number in range(12):
            dependent = tasks.add(
                project_id,
                f"Dependent task {number + 1}: must wait for its prerequisite",
                "No execution before prerequisite completion",
                dependencies=[prerequisite["id"]],
            )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "AI_MANAGER_DATA": str(data),
            "AI_MANAGER_PROJECT_ROOTS": str(project),
            "AI_MANAGER_SECURE_COOKIE": "0",
        }
        with (root / "server.log").open("w") as log:
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
                    browser = playwright.chromium.launch(
                        executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
                    )
                    page = browser.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    if baseline:
                        for name in ("app.js", "style.css"):
                            asset = subprocess.check_output(
                                ["git", "show", f"3aeefc1:ai_manager/static/{name}"]
                            ).decode()
                            page.route(
                                f"**/static/{name}", lambda route, _request, asset=asset: route.fulfill(body=asset)
                            )
                    page.goto(url)
                    page.locator("#username").fill("feedback-fixture-owner")
                    page.locator("#password").fill(secrets.token_urlsafe(24))
                    page.locator("#setup-token").fill((data / "setup-token").read_text())
                    page.locator("#login-button").click()
                    page.locator("#app").wait_for(state="visible")
                    page.locator('[data-view="tasks"]').click()
                    for viewport in ({"width": 1440, "height": 1000}, {"width": 393, "height": 851}):
                        page.set_viewport_size(viewport)
                        row = page.locator(".task-row").filter(has_text=dependent["id"][:8])
                        with page.expect_response(lambda r: r.url.endswith(f"/{dependent['id']}/start")) as response:
                            row.get_by_role("button", name="Start worker", exact=True).click()
                        assert response.value.status == 409
                        target = page.locator("#notice") if baseline else row.locator(".task-feedback")
                        expect(target).to_contain_text("dependencies are unfinished")
                        box = target.bounding_box()
                        visible = bool(box and box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"])
                        records.append(
                            {
                                "viewport": viewport,
                                "action": "Start worker",
                                "http": response.value.status,
                                "response": response.value.json(),
                                "feedback": target.inner_text(),
                                "box": box,
                                "in_viewport": visible,
                            }
                        )
                        assert visible != baseline, records[-1]
                        assert db.one("SELECT status FROM tasks WHERE id=?", (dependent["id"],))["status"] == "pending"
                        if not baseline:
                            page.evaluate("refresh()")
                            assert "dependencies are unfinished" in row.locator(".task-feedback").inner_text()
                            row.get_by_role("button", name="View prerequisite", exact=False).click()
                            blocked = page.locator(".task-row").filter(has_text=prerequisite["id"][:8]).first
                            with page.expect_response(lambda r: r.url.endswith("/verify")) as response:
                                blocked.get_by_role("button", name="Retry verification", exact=True).click()
                            assert response.value.status == 409
                            feedback = blocked.locator(".task-feedback")
                            expect(feedback).to_contain_text("trusted project verification commands")
                            box = feedback.bounding_box()
                            assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"]
                            blocked.get_by_role("button", name="Configure verification", exact=True).click()
                            page.get_by_role("heading", name="Owner-approved verification commands").wait_for()
                            assert page.locator('[name="commands"]').input_value() == "[]"
                            page.locator("#editor-cancel").click()
                    if not baseline:
                        row = page.locator(".task-row").filter(has_text=dependent["id"][:8])
                        with page.expect_response(
                            lambda r: r.request.method == "PATCH" and r.url.endswith(dependent["id"])
                        ) as response:
                            row.get_by_role("button", name="Cancel task", exact=True).click()
                        assert response.value.status == 200
                        row.locator(".badge.cancelled").wait_for()
                        assert "accepted:" in row.locator(".task-feedback").inner_text()
                        assert (
                            db.one("SELECT status FROM tasks WHERE id=?", (dependent["id"],))["status"] == "cancelled"
                        )
                    assert not errors, errors
                    browser.close()
                print(json.dumps(records, indent=2))
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()

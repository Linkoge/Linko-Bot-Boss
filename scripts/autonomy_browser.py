"""Authenticated owner UI regressions in a disposable fixture; no Codex turns."""

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


def main():
    root = Path(tempfile.mkdtemp(prefix="linko-autonomy-browser-"))
    root.chmod(0o700)
    source = root / "projects" / "fixture"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Fixture\n")
    (source / "test_fixture.py").write_text(
        "import unittest\nclass Fixture(unittest.TestCase):\n    def test_true(self):\n        self.assertTrue(True)\n"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    data = root / "data"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "AI_MANAGER_DATA": str(data),
        "AI_MANAGER_PROJECT_ROOTS": str(root / "projects"),
        "AI_MANAGER_SECURE_COOKIE": "0",
    }
    with (root / "service.log").open("w") as log:
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
                browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator("#username").fill("fixture-owner")
                page.locator("#password").fill(secrets.token_urlsafe(24))
                page.locator("#setup-token").fill((data / "setup-token").read_text())
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                page.locator('[data-view="settings"]').click()
                assert page.locator(".navigation nav").count() == 2
                assert page.locator(".navigation").bounding_box()["height"] <= 96

                def api(path):
                    return page.evaluate("async path=>await (await fetch(path)).json()", path)

                assert api("/api/autonomy")["active_profile"] == "legacy"
                # Preview and decline; no profile change merely from opening the dialog.
                page.locator("#switch-profile").click()
                page.locator("#editor-form select[name=profile]").select_option("high_autonomy")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor-title")).to_have_text("Owner approval: consolidated policy changes")
                assert page.locator("#editor-fields textarea[name=summary]").get_attribute("readonly") is not None
                assert "edit_files ASK → ALLOW" in page.locator("textarea[name=summary]").input_value()
                page.locator("#editor-form select[name=decision]").select_option("no")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor")).not_to_be_visible()
                assert api("/api/autonomy")["active_profile"] == "legacy"
                # Explicit fixture-owner activation through the actual consolidated approval.
                page.locator("#switch-profile").click()
                page.locator("#editor-form select[name=profile]").select_option("high_autonomy")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor-title")).to_have_text("Owner approval: consolidated policy changes")
                page.locator("#editor-form select[name=decision]").select_option("yes")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor")).not_to_be_visible()
                assert api("/api/autonomy")["active_profile"] == "high_autonomy"
                assert api("/api/portfolio")["scheduler"]["enabled"] is False
                page.locator("#projects .project-button").first.click()
                page.locator("#discover-verification").click()
                expect(page.locator("#editor-title")).to_have_text("Reusable verification command approvals")
                page.locator("select[name=command_0]").select_option("approved")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor")).not_to_be_visible()
                pid = api("/api/portfolio")["projects"][0]["id"]
                assert any(c["status"] == "approved" for c in api(f"/api/projects/{pid}/verification")["candidates"])
                page.locator("#discover-verification").click()
                page.locator("select[name=command_0]").select_option("revoked")
                page.locator("#editor-form button.primary").click()
                expect(page.locator("#editor")).not_to_be_visible()
                assert not any(
                    c["status"] == "approved" for c in api(f"/api/projects/{pid}/verification")["candidates"]
                )
                page.locator("#policy-history").click()
                expect(page.locator("#editor-title")).to_have_text("Policy change history")
                assert "policy.profile_activated" in page.locator("textarea[name=history]").input_value()
                page.locator("#editor-cancel").click()
                page.screenshot(path=str(root / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 393, "height": 851})
                assert page.locator(".navigation").bounding_box()["height"] <= 96
                page.locator("#verification-overview").click()
                expect(page.locator("#editor-title")).to_have_text("Verification configurations and failures")
                assert page.locator("#editor").bounding_box()["width"] <= 393
                page.screenshot(path=str(root / "mobile.png"), full_page=True)
                assert not errors, errors
                (root / "evidence.json").write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "errors": errors,
                            "profile_preview_decline_and_activation": True,
                            "persistent_command_approval_and_revocation": True,
                            "two_row_navigation": True,
                            "mobile_dialog": True,
                        },
                        indent=2,
                    )
                )
                print("AUTONOMY BROWSER PASSED:", root, flush=True)
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=30)


if __name__ == "__main__":
    main()

"""Real browser check using an isolated database and a throwaway owner."""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix="ai-manager-browser-") as data:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "AI_MANAGER_DATA": data, "AI_MANAGER_SECURE_COOKIE": "0"}
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(50):
                try:
                    if httpx.get(url + "/api/auth-status").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Test server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator("#username").fill("temporary-owner")
                page.locator("#password").fill(secrets.token_urlsafe(24))
                page.locator("#setup-token").fill((Path(data) / "setup-token").read_text())
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                page.locator("#projects .project-button").first.wait_for()
                assert page.locator("#messages").inner_text().startswith("Ai")
                page.screenshot(path="/tmp/ai-manager-desktop.png", full_page=True)
                page.locator('[data-view="dashboard"]').click()
                assert page.locator(".project-card").count() >= 10
                page.screenshot(path="/tmp/ai-manager-overview.png", full_page=True)
                page.set_viewport_size({"width": 393, "height": 851})
                page.locator('[data-view="chat"]').click()
                page.locator("#projects .project-button").first.click()
                page.wait_for_timeout(300)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path="/tmp/ai-manager-mobile.png", full_page=True)
                page.locator('[data-view="memory"]').click()
                assert page.locator("#memory-list .panel").count() >= 5
                page.locator('[data-view="settings"]').click()
                page.locator("#load-policies").click()
                page.locator("#policy-list .route-row").first.wait_for()
                assert page.locator("#policy-list .route-row").count() == 15
                page.locator('[data-view="approvals"]').click()
                assert "Nothing waiting" in page.locator("#approval-list").inner_text()
                assert not errors, errors
                print(
                    "Browser verified: secure setup, login, ten-project overview, project switching, memory, desktop and Android viewport; no page errors or horizontal overflow"
                )
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

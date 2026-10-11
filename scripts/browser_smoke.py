"""Check the minimal UI against an isolated authenticated HTTP fixture."""

import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix="linko-browser-") as temporary:
        root = Path(temporary)
        projects = root / "projects"
        for name in ("Alpha", "Beta"):
            (projects / name).mkdir(parents=True)
        env = {
            **os.environ,
            "AI_MANAGER_DATA": str(root / "data"),
            "AI_MANAGER_PROJECT_ROOTS": str(projects),
            "AI_MANAGER_SECURE_COOKIE": "0",
        }
        program = """import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
for name in ('Alpha','Beta'):app.state.registry.add(s.project_roots[0]/name)
uvicorn.run(app,host='127.0.0.1',port=18879,log_level='error')
"""
        process = subprocess.Popen([os.sys.executable, "-c", program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get("http://127.0.0.1:18879/api/auth-status").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            token = (root / "data/setup-token").read_text()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:18879")
                page.locator("#username").fill("fixture")
                page.locator("#password").fill("fixture-private-password")
                page.locator("#setup-token").fill(token)
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                page.locator("#projects button").first.click()
                page.locator("#message-input").fill("Discuss the project.")
                page.locator("#send").click()
                page.get_by_text("Saved streamed answer", exact=True).wait_for()
                page.locator("#archive-chat").click()
                page.locator("#chat-confirm-submit").click()
                page.locator("#chat-confirm").wait_for(state="hidden")
                page.locator("[data-view=search]").click()
                page.locator("#history-results button").first.click()
                page.locator("#history-detail-messages").get_by_text("Saved streamed answer", exact=True).wait_for()
                page.locator("[data-view=settings]").click()
                page.locator("#load-models").click()
                page.locator("#model-routes form").first.wait_for()
                assert page.locator("#model-routes form").count() == 2
                page.locator("#rename-project").click()
                page.locator("#editor-form input[name=display_name]").fill("Renamed")
                page.locator("#editor-form button[type=submit],#editor-form button.primary").click()
                page.locator("#editor").wait_for(state="hidden")
                page.locator("[data-view=chat]").click()
                for width, height, label in [(1440, 1000, "desktop"), (393, 851, "mobile")]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=f"/tmp/linko-minimal-{label}.png", full_page=True)
                assert not errors, errors
                assert not page.locator("#configure-verification,#configure-scheduler,#chat-mode").count()
                browser.close()
            print(
                "Browser passed: login, chat, archive/search, rename, two model roles, desktop/mobile without JS errors or overflow."
            )
        finally:
            process.terminate()
            process.wait(timeout=20)


if __name__ == "__main__":
    main()

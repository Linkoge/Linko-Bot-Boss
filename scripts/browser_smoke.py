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

from ai_manager.policies import DEFAULTS


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
                baseline = "990ed95fc35724a699e103ee7a238aba97ca22c2"
                old_assets = {
                    path: subprocess.check_output(["git", "show", f"{baseline}:ai_manager/static/{name}"]).decode()
                    for path, name in [
                        ("/", "index.html"),
                        ("/static/app.js", "app.js"),
                        ("/static/style.css", "style.css"),
                    ]
                }

                def old_layout(route):
                    path = route.request.url.removeprefix(url)
                    if path in old_assets:
                        route.fulfill(
                            body=old_assets[path],
                            content_type="text/html"
                            if path == "/"
                            else "text/css"
                            if path.endswith(".css")
                            else "application/javascript",
                        )
                    else:
                        route.continue_()

                page.route("**/*", old_layout)
                page.goto(url)
                page.locator("#username").fill("temporary-owner")
                page.locator("#password").fill(secrets.token_urlsafe(24))
                page.locator("#setup-token").fill((Path(data) / "setup-token").read_text())
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                page.locator("#projects .project-button").first.wait_for()
                page.get_by_text("What should we move forward?", exact=True).wait_for()
                page.set_viewport_size({"width": 393, "height": 851})
                page.locator("#projects .project-button").first.click()
                page.get_by_text("What should we move forward?", exact=True).wait_for()
                old_chat_y = page.locator("#view-chat").bounding_box()["y"]
                old_messages_height = page.locator("#messages").bounding_box()["height"]
                page.screenshot(path="/tmp/linko-before-mobile.png", full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                old_desktop_y = page.locator("#view-chat").bounding_box()["y"]
                page.screenshot(path="/tmp/linko-before-desktop.png", full_page=True)
                page.unroute("**/*", old_layout)
                page.reload()
                page.locator("#app").wait_for(state="visible")
                page.get_by_text("What should we move forward?", exact=True).wait_for()
                assert page.locator(".navigation nav").count() == 2
                assert page.locator(".navigation").bounding_box()["height"] <= 96
                assert page.locator("#view-chat").bounding_box()["y"] <= 96
                print(
                    "Before/after desktop content start:", old_desktop_y, page.locator("#view-chat").bounding_box()["y"]
                )
                assert old_desktop_y - page.locator("#view-chat").bounding_box()["y"] >= 50
                assert (
                    page.locator("#chat-form").bounding_box()["y"] + page.locator("#chat-form").bounding_box()["height"]
                    <= 1000
                )
                page.screenshot(path="/tmp/ai-manager-desktop.png", full_page=True)
                page.locator('[data-view="dashboard"]').click()
                assert page.locator(".project-card").count() >= 10
                page.screenshot(path="/tmp/ai-manager-overview.png", full_page=True)
                page.set_viewport_size({"width": 393, "height": 851})
                page.locator('[data-view="chat"]').click()
                page.locator("#projects .project-button").first.click()
                page.get_by_text("What should we move forward?", exact=True).wait_for()
                first_id = page.locator("#projects .selected").get_attribute("data-project")
                page.locator("#message-input").fill("Draft for first project")
                detail_requests = []
                page.on(
                    "request",
                    lambda request: detail_requests.append(request.url) if "/api/projects/" in request.url else None,
                )
                page.locator("#projects .project-button").nth(1).click()
                page.locator("#message-input").fill("Second project draft")
                page.locator(f'[data-project="{first_id}"].project-button').click()
                assert page.locator("#message-input").input_value() == "Draft for first project"
                assert not detail_requests, detail_requests
                assert page.locator(".navigation").bounding_box()["height"] <= 96
                print("Mobile navigation height:", page.locator(".navigation").bounding_box()["height"])
                print("Mobile conversation area:", page.locator("#messages").bounding_box()["height"])
                print("Before/after mobile content start:", old_chat_y, page.locator("#view-chat").bounding_box()["y"])
                print(
                    "Before/after conversation height:",
                    old_messages_height,
                    page.locator("#messages").bounding_box()["height"],
                )
                assert old_chat_y - page.locator("#view-chat").bounding_box()["y"] >= 180
                assert page.locator("#messages").bounding_box()["height"] - old_messages_height >= 200
                assert page.locator("#messages").bounding_box()["height"] >= 500
                assert page.locator("#send").bounding_box()["y"] + page.locator("#send").bounding_box()["height"] <= 851
                page.locator("#project-tabs").evaluate("(el)=>el.scrollLeft=el.scrollWidth")
                assert (
                    page.locator("#project-menu-button").bounding_box()["x"]
                    + page.locator("#project-menu-button").bounding_box()["width"]
                    <= 393
                )
                page.locator("#project-menu-button").click()
                assert page.locator("#project-menu-button").get_attribute("aria-expanded") == "true"
                labels = page.locator('#project-menu [role="menuitemradio"]').all_text_contents()
                assert "global context" in labels[0]
                assert labels[1:] == sorted(labels[1:], key=str.casefold)
                assert page.locator('#project-menu [aria-checked="true"]').count() == 1
                page.keyboard.press("Home")
                page.keyboard.press("ArrowDown")
                page.keyboard.press("End")
                page.keyboard.press("Escape")
                assert page.locator("#project-menu").is_hidden()
                assert page.locator("#project-menu-button").evaluate("(el)=>el===document.activeElement")
                page.locator("#project-menu-button").click()
                page.locator('#project-menu [role="menuitemradio"]').first.click()
                assert page.locator("#all-projects").get_attribute("aria-pressed") == "true"
                assert page.locator("#message-input").input_value() == ""
                page.locator(f'[data-project="{first_id}"].project-button').click()
                page.locator("#project-menu-button").click()
                page.get_by_role("menuitem", name="Rename selected project…").click()
                page.locator('[name="display_name"]').fill("Bot Manager")
                page.locator("#editor-form .primary").click()
                page.locator("#editor").wait_for(state="hidden")
                page.locator(f'[data-project="{first_id}"].project-button').get_by_text(
                    "Bot Manager", exact=True
                ).wait_for()
                assert page.locator("#message-input").input_value() == "Draft for first project"
                page.reload()
                page.locator("#app").wait_for(state="visible")
                page.locator(f'[data-project="{first_id}"].project-button').get_by_text(
                    "Bot Manager", exact=True
                ).wait_for()
                page.locator(f'[data-project="{first_id}"].project-button').click()
                page.set_viewport_size({"width": 393, "height": 420})
                assert page.locator("#send").bounding_box()["y"] + page.locator("#send").bounding_box()["height"] <= 420
                page.set_viewport_size({"width": 393, "height": 851})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path="/tmp/ai-manager-mobile.png", full_page=True)
                page.locator('[data-view="memory"]').click()
                page.locator("#memory-list .panel").nth(4).wait_for()
                assert page.locator("#memory-list .panel").count() >= 5
                page.locator('[data-view="dashboard"]').click()
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                assert page.locator(".navigation").bounding_box()["y"] == 0
                page.locator('[data-view="agents"]').click()
                assert "No execution history" in page.locator("#agent-list").inner_text()
                page.locator('[data-view="settings"]').click()
                page.locator("#load-policies").click()
                page.locator("#policy-list .route-row").first.wait_for()
                assert page.locator("#policy-list .route-row").count() == len(DEFAULTS)
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

"""Exercise archive/clear/search with disposable chat history and desktop/phone Chromium."""

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

from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.registry import Registry


def seed(db, context, count=3):
    conversation = db.one("SELECT id FROM conversations WHERE context=?", (context,))
    with db.transaction() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (
                    secrets.token_hex(16),
                    conversation["id"],
                    "user" if i % 2 == 0 else "assistant",
                    f"Needle history message {i}",
                    "completed",
                    None,
                    time.time() + i / 10000,
                ),
            )


def main():
    with tempfile.TemporaryDirectory(prefix="linko-chat-history-") as data:
        root = Path(data) / "projects"
        root.mkdir()
        (root / "fixture").mkdir()
        settings = Settings(data_dir=Path(data) / "data", project_roots=(root,), secure_cookie=False)
        settings.prepare()
        db = Database(settings.db_path)
        Registry(db, (root,)).add(root / "fixture")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "AI_MANAGER_DATA": str(settings.data_dir),
            "AI_MANAGER_PROJECT_ROOTS": str(root),
            "AI_MANAGER_SECURE_COOKIE": "0",
            "AI_MANAGER_ALLOWED_HOSTS": "127.0.0.1,localhost",
        }
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
            for _ in range(60):
                try:
                    if httpx.get(url + "/api/auth-status").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Fixture server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator("#username").fill("fixture-owner")
                page.locator("#password").fill(secrets.token_urlsafe(24))
                page.locator("#setup-token").fill((settings.data_dir / "setup-token").read_text())
                page.locator("#login-button").click()
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#clear-chat")).to_be_disabled()
                assert (
                    page.locator('[data-view="settings"]').evaluate("(el)=>el.nextElementSibling.dataset.view")
                    == "search"
                )
                seed(db, "global", count=425)
                page.reload()
                expect(page.locator("#archive-chat")).to_be_enabled()
                page.locator("#message-input").fill("Keep my unsent draft")
                page.locator("#archive-chat").click()
                expect(page.locator("#chat-confirm")).to_be_visible()
                expect(page.locator("#chat-confirm-cancel")).to_be_focused()
                page.locator("#chat-confirm-cancel").click()
                assert db.one("SELECT count(*) AS n FROM messages")["n"] == 425
                page.locator("#archive-chat").click()
                page.locator("#chat-archive-name").fill("Sprint notes")
                page.locator("#chat-confirm-submit").click()
                expect(page.locator("#chat-confirm")).not_to_be_visible()
                expect(page.locator("#messages .welcome")).to_be_visible()
                expect(page.locator("#chat-notice")).to_contain_text("Sprint notes")
                assert page.locator("#message-input").input_value() == "Keep my unsent draft"
                archived = db.one("SELECT conversation_id FROM chat_archives")["conversation_id"]
                assert db.one("SELECT count(*) AS n FROM messages WHERE conversation_id=?", (archived,))["n"] == 425
                page.locator('[data-view="search"]').click()
                page.locator("#history-search").fill("Needle")
                expect(page.locator("#history-results button")).to_have_count(1)
                page.locator("#history-results button").click()
                expect(page.locator("#history-detail-messages .message")).to_have_count(200)
                expect(page.locator("#history-detail-messages")).to_contain_text("Needle history message 424")
                page.locator("#history-older").click()
                expect(page.locator("#history-detail-messages .message")).to_have_count(400)
                page.locator("#history-older").click()
                expect(page.locator("#history-detail-messages .message")).to_have_count(425)
                expect(page.locator("#history-older")).not_to_be_visible()
                page.locator("#history-search").fill("history message 0")
                expect(page.locator("#history-results button")).to_have_count(1)
                page.locator("#history-results button").click()
                expect(page.locator("#history-detail-messages .message")).to_have_count(1)
                expect(page.locator("#history-full")).to_be_visible()
                page.locator("#history-full").click()
                expect(page.locator("#history-detail-messages .message")).to_have_count(200)
                page.locator("#history-search").fill("Sprint")
                expect(page.locator("#history-results button")).to_have_count(1)
                page.locator("#projects .project-button").click()
                expect(page.locator("#history-results")).not_to_contain_text("Sprint notes")
                page.locator("#all-projects").click()
                expect(page.locator("#history-results")).to_contain_text("Sprint notes")
                page.locator('[data-view="chat"]').click()
                seed(db, "global")
                page.reload()
                expect(page.locator("#clear-chat")).to_be_enabled()
                page.locator("#clear-chat").click()
                expect(page.locator("#chat-confirm-description")).to_contain_text("cannot be undone")
                expect(page.locator("#chat-archive-label")).not_to_be_visible()
                page.keyboard.press("Escape")
                assert db.one("SELECT count(*) AS n FROM messages")["n"] == 428
                page.locator("#clear-chat").click()
                page.locator("#chat-confirm-submit").click()
                expect(page.locator("#messages .welcome")).to_be_visible()
                expect(page.locator("#chat-notice")).to_contain_text("Chat cleared")
                assert db.one("SELECT count(*) AS n FROM messages")["n"] == 425
                page.reload()
                expect(page.locator("#app")).to_be_visible()
                page.locator('[data-view="search"]').click()
                expect(page.locator("#history-results")).to_contain_text("Sprint notes")
                for width, height in [(1440, 1000), (393, 851), (320, 640), (393, 420)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.locator('[data-view="chat"]').click()
                    page.evaluate("window.scrollTo(0,0)")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    toolbar = page.locator(".chat-toolbar").bounding_box()
                    controls = page.locator(".chat-actions").bounding_box()
                    assert controls["width"] <= 160
                    assert controls["x"] + controls["width"] <= width
                    assert controls["y"] == toolbar["y"]
                    send = page.locator("#send").bounding_box()
                    assert send["y"] + send["height"] <= height
                    assert page.locator(".navigation").bounding_box()["height"] <= 96
                    if (width, height) in [(1440, 1000), (393, 851)]:
                        page.screenshot(path=f"/tmp/linko-chat-history-{width}.png", full_page=True)
                assert not errors, errors
                browser.close()
                print(
                    "Browser passed: confirmations, archive/search/pagination, clear, context isolation, persistence, drafts, desktop/phone layout"
                )
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

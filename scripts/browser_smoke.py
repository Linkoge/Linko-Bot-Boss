"""Check the minimal UI against an isolated authenticated HTTP fixture."""

import os
import socket
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
        for name in ("Alpha", "Beta", "Recovery Fixture"):
            (projects / name).mkdir(parents=True)
        env = {
            **os.environ,
            "AI_MANAGER_DATA": str(root / "data"),
            "AI_MANAGER_PROJECT_ROOTS": str(projects),
            "AI_MANAGER_SECURE_COOKIE": "0",
        }
        program = """import sys
sys.path.insert(0,'tests')
import uvicorn, time
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
for name in ('Alpha','Beta','Recovery Fixture'):app.state.registry.add(s.project_roots[0]/name)
recovery=app.state.db.one("SELECT id FROM projects WHERE name='Recovery Fixture'")
recovery_chat=app.state.chat.conversation(recovery['id'])
app.state.scheduler.tasks.add(recovery['id'],'Retain partial development during outage',recovery_chat['id'])
recovery_task=app.state.scheduler.tasks.claim(2)
with app.state.db.transaction() as conn:
    conn.execute("UPDATE tasks SET lifecycle='waiting',blocker='Waiting for connection' WHERE id=?",(recovery_task['id'],))
    conn.execute("UPDATE executions SET status='recovering',retry_at=?,retry_count=1,recovery_reason='connection' WHERE id=?",(time.time()+3600,recovery_task['lease_owner']))
    conn.execute("INSERT INTO executions(id,agent_id,status,started_at,conversation_id,retry_at,recovery_reason) VALUES('recovery-reply','manager','recovering',?,?,?,'usage')",(time.time(),recovery_chat['id'],time.time()+3600))
    conn.execute("INSERT INTO messages VALUES('recovery-message',?,'assistant','Partial response is preserved','waiting','recovery-reply',?)",(recovery_chat['id'],time.time()))
beta=app.state.db.one("SELECT id FROM projects WHERE name='Beta'")
conversation=app.state.chat.conversation(beta['id'])
with app.state.db.transaction() as conn:
    for i in range(425):
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',
            (f'fixture-{i}',conversation['id'],'user' if i%2==0 else 'assistant',
             f'Fixture message {i}\\nSecond line {i}','completed',None,1700000000+i))
uvicorn.run(app,host='127.0.0.1',port=18879,log_level='error')
"""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        program = program.replace("port=18879", f"port={port}")
        process = subprocess.Popen([os.sys.executable, "-c", program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base_url + "/api/auth-status").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            token = (root / "data/setup-token").read_text()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.context.grant_permissions(["clipboard-read", "clipboard-write"])
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base_url)
                page.locator("#username").fill("fixture")
                page.locator("#password").fill("fixture-private-password")
                page.locator("#setup-token").fill(token)
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                page.locator("#projects button").filter(has_text="Alpha").click()
                assert page.locator("#copy-all").is_disabled()
                assert page.locator("#copy-five").is_disabled()
                page.locator("#message-input").fill("Discuss the project.")
                page.locator("#send").click()
                page.get_by_text("Saved streamed answer", exact=True).wait_for()
                page.wait_for_function("() => !state.execution")
                for control in ("#copy-all", "#copy-five"):
                    page.locator(control).click()
                    page.wait_for_function(
                        "async () => (await navigator.clipboard.readText()) === "
                        "'You:\\nDiscuss the project.\\n\\nAI Manager:\\nSaved streamed answer'"
                    )
                page.locator("#archive-chat").click()
                page.locator("#chat-archive-name").fill("Fixture archive")
                page.locator("#chat-confirm-submit").click()
                page.locator("#chat-confirm").wait_for(state="hidden")
                page.locator("[data-view=search]").click()
                page.locator("#history-results button").filter(has_text="Fixture archive").click()
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
                page.locator("#projects button").filter(has_text="Beta").click()
                page.wait_for_function("() => state.history?.messages.length === 200")
                # Select by dragging, then leave the regular three-second refresh running.
                content = page.locator("#messages .message-content").last
                content.scroll_into_view_if_needed()
                box = content.bounding_box()
                page.mouse.move(box["x"] + 2, box["y"] + 10)
                page.mouse.down()
                page.mouse.move(box["x"] + 115, box["y"] + 10, steps=12)
                page.mouse.up()
                selected = page.evaluate("window.getSelection().toString()")
                assert selected
                page.evaluate("window.selectedNode = window.getSelection().anchorNode")
                request = page.evaluate("state.loadHistoryRequest")
                page.wait_for_function("() => state.loadHistoryRequest >= " + str(request + 2))
                assert page.evaluate("window.getSelection().toString()") == selected
                assert page.evaluate("window.getSelection().anchorNode === window.selectedNode")
                # Appending streamed text must also retain the selected text node.
                page.evaluate(
                    "const node=window.selectedNode.parentElement;"
                    "updateMessageContent(node,node.textContent+' streamed suffix');"
                )
                assert page.evaluate("window.getSelection().toString()") == selected
                assert page.evaluate("window.getSelection().anchorNode === window.selectedNode")
                page.locator("#copy-five").click()
                expected = "\n\n".join(
                    f"{'You' if i % 2 == 0 else 'AI Manager'}:\nFixture message {i}\nSecond line {i}"
                    for i in range(420, 425)
                )
                page.wait_for_function("async text => (await navigator.clipboard.readText()) === text", arg=expected)
                page.locator("#copy-all").click()
                expected = "\n\n".join(
                    f"{'You' if i % 2 == 0 else 'AI Manager'}:\nFixture message {i}\nSecond line {i}"
                    for i in range(425)
                )
                page.wait_for_function("async text => (await navigator.clipboard.readText()) === text", arg=expected)
                # Preserve selection when falling back after a clipboard API rejection.
                page.evaluate(
                    "window.testClipboard=navigator.clipboard;"
                    "Object.defineProperty(navigator,'clipboard',{configurable:true,value:"
                    "{writeText:async()=>{throw new Error('Fixture clipboard rejection');}}});"
                    "const node=document.querySelector('#messages .message-content').firstChild;"
                    "const range=document.createRange();range.setStart(node,0);range.setEnd(node,7);"
                    "window.getSelection().removeAllRanges();window.getSelection().addRange(range);"
                )
                page.locator("#copy-five").click()
                page.wait_for_function("() => !state.copyingChat")
                assert page.evaluate("window.getSelection().toString()") == "Fixture"
                assert page.evaluate("document.querySelectorAll('.clipboard-fallback').length") == 0
                assert "Fixture message 424" in page.evaluate("window.testClipboard.readText()")
                page.evaluate("delete navigator.clipboard;window.getSelection().removeAllRanges()")
                # Reading older messages must not jump back to the bottom during polling.
                page.evaluate("document.getElementById('messages').scrollTop=100")
                scroll = page.evaluate("document.getElementById('messages').scrollTop")
                page.evaluate("loadHistory()")
                assert page.evaluate("document.getElementById('messages').scrollTop") == scroll
                for width, height, label in [
                    (1440, 1000, "desktop"), (393, 851, "mobile"), (320, 700, "narrow")
                ]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert page.locator("#chat-mode").bounding_box()["width"] <= 112
                    for control in ("#copy-all", "#copy-five", "#archive-chat", "#clear-chat"):
                        assert page.locator(control).bounding_box()["height"] <= 30
                    page.screenshot(path=f"/tmp/linko-minimal-{label}.png", full_page=True)
                assert not errors, errors
                assert page.locator("#chat-mode option").count() == 3
                assert not page.locator("#configure-verification,#configure-scheduler").count()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator("#projects button").filter(has_text="Recovery Fixture").click()
                page.locator("#messages .badge").filter(has_text="Waiting for usage limit").wait_for()
                assert "Next attempt" in page.locator("#messages .badge").inner_text()
                assert page.locator("#send").is_disabled()
                assert page.locator("#archive-chat").is_disabled()
                page.locator("[data-view=tasks]").click()
                page.locator("#task-list").get_by_text("Waiting for connection", exact=False).wait_for()
                assert "Next attempt" in page.locator("#task-list").inner_text()
                page.locator("[data-view=chat]").click()
                for width in (1440, 393, 320):
                    page.set_viewport_size({"width": width, "height": 851})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert not errors, errors
                browser.close()
            print(
                "Browser passed: login, chat, selection across refresh/streaming, copy all 425 messages, "
                "copy five, clipboard fallback, scroll preservation, archive/search, rename, model roles, recovery reasons/timing, "
                "desktop/mobile/narrow controls without JS errors or overflow."
            )
        finally:
            process.terminate()
            process.wait(timeout=20)


if __name__ == "__main__":
    main()

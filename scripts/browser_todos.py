"""Disposable startup, To-Do persistence/selection/races and responsive coverage."""

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory(prefix="linko-todos-") as temporary:
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
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        program = f"""import sys
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s=Settings();app=create_app(s,FakeServer(s))
for name in ('Alpha','Beta'):app.state.registry.add(s.project_roots[0]/name)
with app.state.db.transaction() as conn:
 conn.execute("UPDATE projects SET last_used_at=200 WHERE name='Alpha'")
app.state.db.set_setting('global_last_used_at',100)
uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')
"""
        process = subprocess.Popen([os.sys.executable, "-c", program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + "/api/auth-status").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context()
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator("#username").fill("fixture")
                page.locator("#password").fill("fixture-private-password")
                page.locator("#setup-token").fill((root / "data/setup-token").read_text())
                page.locator("#login-button").click()
                page.wait_for_function("() => state.history")
                ids = page.evaluate("Object.fromEntries(state.portfolio.projects.map(p=>[p.name,p.id]))")
                assert page.evaluate("state.project") == ids["Alpha"]
                assert page.locator("#task-count").is_hidden()
                assert page.locator(".tabs button").evaluate_all("(bs)=>bs.map(b=>b.dataset.view)")[:5] == [
                    "chat",
                    "tasks",
                    "agents",
                    "todo",
                    "memory",
                ]
                snapshot = page.evaluate(
                    "JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])"
                )
                page.reload()
                page.wait_for_function("() => state.history")
                assert page.evaluate("state.project") == ids["Alpha"]
                fresh = context.new_page()
                fresh.goto(base)
                fresh.wait_for_function("() => state.history")
                assert fresh.evaluate("state.project") == ids["Alpha"]
                fresh.close()
                # Initial load can exceed a polling interval without losing startup choice.
                page.route("**/api/portfolio", lambda route: (time.sleep(3.3), route.continue_()))
                page.reload()
                page.wait_for_function("() => state.ready && state.history")
                assert page.evaluate("state.project") == ids["Alpha"]
                page.unroute("**/api/portfolio")
                page.locator("[data-view=todo]").click()
                page.locator("#todo-add").click()
                page.locator("[name=title]").fill("Alpha future idea")
                page.locator("[name=details]").fill("Do this later; preserve detail")
                page.locator("#editor-form .primary").click()
                page.wait_for_function("() => state.todos.length===1")
                a = page.evaluate("state.todos[0]")
                page.locator("#todo-list input").check()
                page.evaluate("selectProject(state.portfolio.projects.find(p=>p.name==='Beta').id)")
                page.wait_for_function("() => state.project===state.portfolio.scope_project_id && !state.todos.length")
                assert page.locator("#todo-delete").is_disabled()
                assert "Alpha future idea" not in page.locator("#todo-list").inner_text()
                page.locator("#todo-add").click()
                page.locator("[name=title]").fill("Beta future idea")
                page.locator("#editor-form .primary").click()
                page.wait_for_function("() => state.todos.length===1")
                # Edit binds the originally opened selection; context change rejects saving.
                page.locator("#todo-list button").click()
                page.evaluate("selectProject(null)")
                page.wait_for_function("() => state.todos.length===2")
                page.locator("[name=title]").fill("Wrong context mutation")
                page.locator("#editor-form .primary").click()
                page.wait_for_function(
                    "() => document.getElementById('editor-error').textContent.includes('Context changed')"
                )
                page.locator("#editor").evaluate("(d)=>d.close()")
                page.locator("#todo-list button").first.click()
                page.locator("[name=title]").fill("Updated Alpha idea")
                page.locator("#editor-form .primary").click()
                page.wait_for_function("() => state.todos.some(t=>t.title==='Updated Alpha idea')")
                # Focus/selection survives polling.
                page.locator("#todo-list input").first.focus()
                page.evaluate("loadTodos()")
                assert page.evaluate("document.activeElement.dataset.todo") == a["id"]
                page.locator("#todo-list .todo-details").first.evaluate("""node=>{
                    const range=document.createRange();range.selectNodeContents(node);
                    const s=window.getSelection();s.removeAllRanges();s.addRange(range);
                }""")
                selected = page.evaluate("getSelection().toString()")
                page.evaluate("loadTodos()")
                assert page.evaluate("getSelection().toString()") == selected
                page.evaluate("getSelection().removeAllRanges()")
                for width, height in ((1440, 1000), (393, 851), (320, 420)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    for control in ("#todo-add", "#todo-delete", "#todo-list input"):
                        assert page.locator(control).first.is_visible()
                    page.screenshot(path=f"/tmp/linko-todos-{width}.png")
                # Delay old portfolio To-Do fetch; switch context while it is in flight.
                page.evaluate("""() => {
                    window.originalApi=api;window.releaseTodo=null;
                    api=async(path,options)=>{const data=await originalApi(path,options);
                        if(path.startsWith('/api/todos'))await new Promise(resolve=>releaseTodo=resolve);return data;};
                    window.oldTodo=loadTodos();
                }""")
                page.wait_for_function("() => !!releaseTodo")
                page.evaluate("api=originalApi;selectProject(state.portfolio.projects.find(p=>p.name==='Beta').id)")
                page.wait_for_function("() => state.todos.length===1 && state.todos[0].title==='Beta future idea'")
                page.evaluate("releaseTodo();oldTodo")
                assert page.locator("#todo-list .panel").count() == 1
                page.locator("#todo-list input").check()
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#todo-delete").click()
                page.wait_for_function("() => state.todos.length===0")
                page.reload()
                page.wait_for_function("() => state.history")
                assert page.evaluate("state.project") == ids["Alpha"]
                page.locator("[data-view=todo]").click()
                page.wait_for_function("() => state.todos.length===1")
                assert (
                    page.evaluate("JSON.stringify([state.portfolio.global_last_used_at,state.portfolio.projects])")
                    == snapshot
                )
                assert page.evaluate("state.portfolio.tasks.length") == 0
                assert page.evaluate("state.portfolio.executions.length") == 0
                # Global is a first-class recent context; ties match existing ordering.
                page.evaluate(
                    "api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:'fixture'})})"
                )
                # Set response recency only in the disposable fixture for reload/no-project coverage.
                for mode in ("global", "empty"):
                    page.route("**/api/portfolio*", lambda route, request, m=mode: override(route, m))
                    page.reload()
                    page.wait_for_function("() => state.history")
                    assert page.evaluate("state.project") is None
                    page.unroute("**/api/portfolio*")
                assert not errors, errors
                print(
                    json.dumps(
                        {
                            "startup": "project/global/reload/fresh/no-project",
                            "todo": "create/edit/archive/persistence/isolation/guards/no-dispatch",
                            "viewports": [1440, 393, 320],
                            "errors": errors,
                        }
                    )
                )
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def override(route, mode):
    response = route.fetch()
    data = response.json()
    data["global_last_used_at"] = 300
    if mode == "empty":
        data["projects"] = []
    route.fulfill(response=response, json=data)


if __name__ == "__main__":
    main()

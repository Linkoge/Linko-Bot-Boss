"""Disposable owner-triggered memory review UI, responsive/scoped/race acceptance."""

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
    evidence = Path(os.environ.get("LINKO_MEMORY_EVIDENCE", "/tmp/linko-memory-browser"))
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linko-memory-ui-") as tmp:
        root = Path(tmp)
        projects = root / "projects"
        for name in ("Alpha", "Beta"):
            (projects / name).mkdir(parents=True)
            (projects / name / "app.py").write_text("feature = True")
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
        program = f"""import sys,asyncio,json
sys.path.insert(0,'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
class ReviewServer(FakeServer):
 async def request(self,method,params,timeout=30):
  if method!='turn/start':return {{}}
  prompt=json.loads(params['input'][0]['text'])
  handler=self.tool_handlers[params['threadId']]
  await handler({{'arguments':{{'kind':'file','path':'app.py'}}}})
  brief=prompt['active']['brief'];brief['state']=['Reviewed feature exists (app.py); not deployed'];brief['goals']=[]
  async def emit():
   await asyncio.sleep(.2)
   q=self.queues[params['threadId']]
   q.put_nowait({{'method':'thread/tokenUsage/updated','params':{{'tokenUsage':{{'total':{{'inputTokens':45}},'last':{{'inputTokens':45,'outputTokens':12}}}}}}}})
   q.put_nowait({{'method':'item/agentMessage/delta','params':{{'delta':json.dumps({{'brief':brief,'mismatches':[],'sources':['file:app.py']}})}}}})
   q.put_nowait({{'method':'turn/completed','params':{{'turn':{{'id':'review-turn','status':'completed'}}}}}})
  asyncio.create_task(emit())
  return {{'turn':{{'id':'review-turn'}}}}
s=Settings();app=create_app(s,FakeServer(s));app.state.memory_reviews.factory=ReviewServer
for name in ('Alpha','Beta'):
 p=app.state.registry.add(s.project_roots[0]/name)
 app.state.registry.update(p['id'],p['revision'],{{'objective':'Keep persistent '+name+' goal'}})
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
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator("#username").fill("fixture")
                page.locator("#password").fill("fixture-private-password")
                page.locator("#setup-token").fill((root / "data/setup-token").read_text())
                page.locator("#login-button").click()
                page.wait_for_function("() => state.history")
                page.evaluate("selectProject(state.portfolio.projects.find(p=>p.name==='Alpha').id)")
                page.locator("[data-view=memory]").click()
                page.wait_for_function("() => state.brief")
                active = page.evaluate("state.brief.active")
                assert page.evaluate("state.brief.reviews.length") == 0
                assert "Keep persistent Alpha goal" in page.locator("#memory-current").inner_text()
                dialogs = []

                def accept(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept()

                page.on("dialog", accept)
                page.locator("#memory-review").click()
                page.wait_for_function("() => state.brief.review_records.some(r=>r.status==='candidate')")
                assert len(dialogs) == 1 and "Cost is unknown" in dialogs[0]
                assert page.evaluate("state.brief.active") == active
                assert "Actual reported usage" in page.locator("#memory-reviews").inner_text()
                assert "45" in page.locator("#memory-reviews").inner_text()
                assert "Active" in page.locator("#memory-reviews").inner_text()
                assert "Candidate" in page.locator("#memory-reviews").inner_text()
                assert page.get_by_role("button", name="Apply candidate").is_disabled()
                page.locator("#memory-reviews textarea").fill("Retain persistent goal; absence is ambiguous")
                page.get_by_role("button", name="Apply candidate").click()
                page.wait_for_function("() => state.brief.review_records[0].status==='applied'")
                assert page.evaluate("state.brief.active") != active
                assert "Keep persistent Alpha goal" in page.locator("#memory-current").inner_text()
                page.reload()
                page.wait_for_function("() => state.history")
                page.evaluate("selectProject(state.portfolio.projects.find(p=>p.name==='Alpha').id)")
                page.locator("[data-view=memory]").click()
                page.wait_for_function("() => state.brief?.review_records[0]?.status==='applied'")
                for width, height in ((1440, 1000), (393, 851), (320, 420)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    assert page.locator("#memory-review").is_visible()
                    page.screenshot(path=str(evidence / f"memory-{width}.png"), full_page=True)
                page.get_by_text("Previous versions · inspect or roll back").click()
                page.get_by_role("button", name="Inspect previous version").click()
                page.wait_for_function("() => document.querySelector('#memory-versions pre')")
                page.get_by_role("button", name="Roll back", exact=True).click()
                page.wait_for_function("() => state.brief.brief.state[0].startsWith('Capabilities not reconciled')")
                assert "Keep persistent Alpha goal" in page.locator("#memory-current").inner_text()
                # An old asynchronous review fetch must not render in another project.
                page.evaluate("""() => {
                  window.originalMemoryApi=api;window.releaseMemory=null;
                  api=async(path,options)=>{const result=await originalMemoryApi(path,options);
                    if(path.endsWith('/brief'))await new Promise(resolve=>releaseMemory=resolve);return result;};
                  window.oldMemory=loadMemory();
                }""")
                page.wait_for_function("() => !!releaseMemory")
                page.evaluate(
                    "api=originalMemoryApi;selectProject(state.portfolio.projects.find(p=>p.name==='Beta').id)"
                )
                page.wait_for_function("() => state.brief?.brief.goals[0]==='Keep persistent Beta goal'")
                page.evaluate("releaseMemory();oldMemory")
                assert "Alpha" not in page.locator("#memory-current").inner_text()
                assert page.evaluate("state.brief.reviews.length") == 0
                page.evaluate("selectProject(null)")
                page.wait_for_function("() => state.project===null")
                assert page.locator("#memory-review").is_disabled()
                assert page.locator("#memory-current").inner_text() == ""
                assert not errors, errors
                print(
                    json.dumps(
                        {
                            "review": "explicit start/progress/usage/diff/mismatch keep/apply/reload/rollback",
                            "scope": "project-only/no automatic reviews/switch guards",
                            "viewports": [1440, 393, 320],
                            "errors": errors,
                        }
                    )
                )
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

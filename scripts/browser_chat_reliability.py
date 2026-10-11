"""Browser faults against disposable DB, native fixture and ephemeral loopback port."""

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PROGRAM = """
import asyncio, json, sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
class Server(FakeServer):
    async def request(self, method, params, timeout=30):
        if method != 'turn/start': return {}
        self.prompts.append(params['input'][0]['text'])
        text=json.loads(params['input'][0]['text'])['message']
        thread=params['threadId'];queue=self.queues[thread]
        async def emit():
            if text != 'Empty':
                queue.put_nowait({'method':'item/agentMessage/delta','params':{'turnId':'t','delta':'Exact '}})
            await asyncio.sleep(1.5)
            if text != 'Empty':
                queue.put_nowait({'method':'item/agentMessage/delta','params':{'turnId':'t','delta':'saved text'}})
            queue.put_nowait({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}})
        asyncio.create_task(emit())
        return {'turn':{'id':'t'}}
    async def interrupt(self, thread_id, turn_id):
        self.queues[thread_id].put_nowait({'method':'turn/completed','params':{'turn':{'id':turn_id,'status':'interrupted'}}})
s=Settings();app=create_app(s,Server(s))
for name in ('Alpha','Beta'):app.state.registry.add(s.project_roots[0]/name)
uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='error')
"""


def main():
    with tempfile.TemporaryDirectory(prefix="linko-chat-browser-") as directory:
        root = Path(directory)
        for name in ("Alpha", "Beta"):
            (root / "projects" / name).mkdir(parents=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "AI_MANAGER_DATA": str(root / "data"),
            "AI_MANAGER_PROJECT_ROOTS": str(root / "projects"),
            "AI_MANAGER_SECURE_COOKIE": "0",
        }
        process = subprocess.Popen([os.sys.executable, "-c", PROGRAM.replace("PORT", str(port))], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(url + "/api/auth-status").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Capture handlers to simulate late delivery from an old attachment.
                page.add_init_script("""
                    window.attachments=[];
                    const NativeSource=window.EventSource;
                    window.EventSource=class extends NativeSource {
                        constructor(url){super(url);this.handlers={};this.closeCalls=0;window.attachments.push(this);}
                        close(){this.closeCalls++;super.close();}
                        addEventListener(name,handler){this.handlers[name]=handler;super.addEventListener(name,handler);}
                    };
                """)
                page.goto(url)
                page.locator("#username").fill("owner")
                page.locator("#password").fill("disposable-browser-password")
                page.locator("#setup-token").fill((root / "data/setup-token").read_text())
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                # Accept on the server, then drop the response before the browser gets it.
                dropped = []

                def lose_receipt(route):
                    response = route.fetch()
                    assert response.status == 200
                    dropped.append((route.request.post_data_json, response.json()))
                    route.abort("failed")

                page.route("**/api/chat", lose_receipt)
                page.locator("#message-input").fill("Lost receipt")
                page.locator("#send").click()
                page.wait_for_function("() => !state.pending.size && submissions.size===1")
                saved = page.evaluate("sessionStorage.getItem(submissionKey)")
                assert json.loads(saved)[0][1]["request_id"] == dropped[0][0]["request_id"]
                page.unroute("**/api/chat", lose_receipt)
                for _ in range(100):
                    if page.evaluate("async () => (await api('/api/history')).messages.at(-1)?.status==='completed'"):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("Fixture did not complete before restart")
                # Restart only this fixture process after completion, keeping its DB and browser session.
                process.terminate()
                process.wait(timeout=10)
                process = subprocess.Popen([os.sys.executable, "-c", PROGRAM.replace("PORT", str(port))], env=env)
                for _ in range(100):
                    try:
                        if httpx.get(url + "/api/auth-status").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                page.reload()
                page.wait_for_function(
                    "() => !submissions.size && state.history?.messages.at(-1)?.status==='completed'"
                )
                assert page.locator("#messages .message.user").count() == 1
                assert page.locator("#messages .message.assistant").count() == 1
                assert page.locator("#messages .message.assistant .message-content").inner_text() == "Exact saved text"
                assert page.evaluate("state.history.messages.at(-1).execution_id") == dropped[0][1]["execution_id"]
                # Every SSE connection fails; history/cursor reconciliation must finish exactly.
                page.route("**/api/executions/*/events?*", lambda route: route.abort("failed"))
                page.locator("#message-input").fill("Broken SSE")
                page.locator("#send").click()
                page.wait_for_function(
                    "() => state.history?.messages.length===4 && state.history.messages.at(-1).status==='completed'"
                )
                assert page.locator("#messages .message-content").last.inner_text() == "Exact saved text"
                assert page.locator("#send").is_enabled() and page.locator("#stop-chat").is_hidden()
                assert page.locator("#status-label").inner_text() == "Ready to help"
                page.unroute("**/api/executions/*/events?*")
                # A selected context and replaced attachment reject old text, errors and expiry.
                page.locator("#message-input").fill("Switch")
                page.locator("#send").click()
                page.wait_for_function("() => !!state.source")
                page.evaluate("window.oldSource=state.source")
                page.locator("#projects button").filter(has_text="Beta").click()
                page.wait_for_function("() => !state.history?.messages.length && !state.execution")
                page.evaluate("""() => {
                    for(const name of ['chat.delta','chat.finished','recovery.scheduled','stream.end','stream.auth_expired'])
                        window.oldSource.handlers[name]({lastEventId:'9999999',data:JSON.stringify({delta:'STALE',error:'STALE'})});
                    window.oldSource.onerror();
                }""")
                assert page.locator("#app").is_visible()
                assert "STALE" not in page.locator("#messages").inner_text()
                # Older navigation resumes after a newer chat has attached its SSE.
                page.evaluate("""() => {
                    const alpha=state.portfolio.projects.find(p=>p.name==='Alpha');
                    window.savedLoadMemory=loadMemory;
                    loadMemory=()=>new Promise(resolve=>{window.releaseNavigation=resolve;});
                    state.view='memory';
                    window.delayedNavigation=selectProject(alpha.id);
                }""")
                page.wait_for_function("() => typeof window.releaseNavigation === 'function'")
                page.evaluate("""async () => {
                    state.view='chat';
                    await selectProject(state.portfolio.projects.find(p=>p.name==='Beta').id);
                }""")
                # Snapshot replacement cursor rejects replayed chunks on the same attachment.
                page.locator("#message-input").fill("Replay")
                page.locator("#send").click()
                page.wait_for_function("() => !!state.source && state.history?.messages.at(-1).content==='Exact '")
                unchanged = page.evaluate("""async () => {
                    const before={source:state.source,closeCalls:state.source.closeCalls,
                        request:state.loadHistoryRequest,history:state.history,project:state.project,
                        selection:state.selection,execution:state.execution,
                        messages:$('messages').innerHTML,status:$('status-label').textContent};
                    window.releaseNavigation();
                    await window.delayedNavigation;
                    loadMemory=window.savedLoadMemory;
                    return state.source===before.source&&before.source.closeCalls===before.closeCalls
                        &&state.loadHistoryRequest===before.request&&state.history===before.history
                        &&state.project===before.project&&state.selection===before.selection
                        &&state.execution===before.execution&&$('messages').innerHTML===before.messages
                        &&$('status-label').textContent===before.status;
                }""")
                assert unchanged, "Delayed stale navigation changed the newer selected chat or SSE attachment"
                page.evaluate("""async () => {
                    const before=state.loadHistoryRequest,previous=state.source,closed=previous.closeCalls;
                    await loadHistory();
                    if(state.loadHistoryRequest!==before+1||previous.closeCalls!==closed+1||state.source===previous)
                        throw new Error('Current default history load must still refresh its attachment');
                    const m=state.history.messages.at(-1),source=state.source;
                    const data=JSON.stringify({delta:'Exact '});
                    source.handlers['chat.delta']({lastEventId:String(m.stream_cursor),data});
                }""")
                page.wait_for_function("() => state.history.messages.at(-1).status==='completed'")
                assert page.locator("#messages .message-content").last.inner_text() == "Exact saved text"
                # Terminal empty and stopped replies cannot look like connecting forever.
                page.locator("#message-input").fill("Empty")
                page.locator("#send").click()
                page.wait_for_function(
                    "() => state.history.messages.at(-1).status==='completed' && state.history.messages.length===4"
                )
                assert page.locator("#messages .message-content").last.inner_text() == "No text response."
                page.locator("#message-input").fill("Empty")
                page.locator("#send").click()
                page.wait_for_function("() => !!state.source && state.history.messages.length===6")
                page.locator("#stop-chat").click()
                page.wait_for_function("() => state.history.messages.at(-1).status==='interrupted'")
                assert page.locator("#messages .message-content").last.inner_text() == "Stopped."
                # Current stream expiry hides the app and invalidates pending callbacks.
                page.locator("#message-input").fill("Expiry")
                page.locator("#send").click()
                page.wait_for_function("() => !!state.source")
                page.evaluate("state.source.handlers['stream.auth_expired']({})")
                assert page.locator("#auth-screen").is_visible() and page.locator("#app").is_hidden()
                assert not errors, errors
                browser.close()
            print(
                "Browser chat reliability faults passed: lost HTTP, reload, broken SSE, exact text/cursor, "
                "switching, delayed navigation, empty/stop, expiry"
            )
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

"""Markdown security, partial streams, saved history and responsive fixture checks."""

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from browser_chat_reliability import PROGRAM
from playwright.sync_api import sync_playwright

TEXT = '''# Reply

A **bold** and *emphasized* paragraph with ~~removed~~ text.

- First
- Second with `inline <code>`
  - Nested

3. Third
4. Fourth

> Quoted text

[Safe link](https://example.com/?a=1&b=2) and <https://example.org>.

| Name | Value |
| --- | --- |
| alpha | beta |

```html
<script>window.markdownXss = 1</script>
<img src=x onerror=alert(1)>
'''+('very_long_code_line_' * 50)+'''
```

<script>window.markdownXss = 2</script>
<img src=x onerror=alert(1)>
[bad](javascript:alert(1)) [data](data:text/html,evil) [vb](vbscript:evil)
![Image alt](https://example.com/never-load.png)
'''


def main():
    with tempfile.TemporaryDirectory(prefix="linko-markdown-") as directory:
        root = Path(directory)
        for name in ("Alpha", "Beta"):
            (root / "projects" / name).mkdir(parents=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        program = PROGRAM.replace("PORT", str(port))
        start = program.index("        async def emit():")
        end = program.index("        asyncio.create_task(emit())", start)
        program = program[:start] + '''        async def emit():
            for chunk in CHUNKS:
                queue.put_nowait({'method':'item/agentMessage/delta','params':{'turnId':'t','delta':chunk}})
                await asyncio.sleep(.12)
            queue.put_nowait({'method':'turn/completed','params':{'turn':{'id':'t','status':'completed'}}})
''' + program[end:]
        # Split through emphasis, links and an unfinished code fence.
        program = "CHUNKS=" + repr([TEXT[i:i + 37] for i in range(0, len(TEXT), 37)]) + "\n" + program
        env = {**os.environ, "AI_MANAGER_DATA": str(root / "data"),
               "AI_MANAGER_PROJECT_ROOTS": str(root / "projects"), "AI_MANAGER_SECURE_COOKIE": "0"}
        process = subprocess.Popen([os.sys.executable, "-c", program], env=env)
        try:
            for _ in range(100):
                try:
                    if httpx.get(url + "/api/auth-status").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                errors, remote = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: remote.append(request.url) if not request.url.startswith(url) else None)
                page.goto(url)
                page.locator("#username").fill("owner")
                page.locator("#password").fill("disposable-browser-password")
                page.locator("#setup-token").fill((root / "data/setup-token").read_text())
                page.locator("#login-button").click()
                page.locator("#app").wait_for(state="visible")
                # Every character prefix is parseable, safe and converges to one final DOM.
                page.evaluate('''text=>{
                  const node=document.createElement('div');document.body.append(node);
                  for(let i=0;i<=text.length;i++){
                    node.replaceChildren(renderChatMarkdown(text.slice(0,i)));
                    if(node.querySelector('script,img,iframe,object,svg'))throw Error('unsafe prefix');
                    for(const a of node.querySelectorAll('[href]'))if(!['http:','https:','mailto:'].includes(new URL(a.href).protocol))throw Error('unsafe scheme');
                  }
                  window.expectedMarkdown=node.innerHTML;node.remove();
                }''', TEXT)
                page.evaluate("""()=>{
                  const n=document.createElement('div');
                  const payloads=['[x](JaVaScRiPt:alert(1))','[x](java&#x73;cript:alert(1))',
                    '[x](java\\nscript:evil)','[x](file:///etc/passwd)','[x](data:image/png;base64,abc)',
                    '<svg onload=alert(1)>','![x](data:image/svg+xml,evil)',
                    '[safe](/api/auth-status) [anchor](#reply) [mail](mailto:owner@example.com)'];
                  for(const text of payloads){n.replaceChildren(renderChatMarkdown(text));
                    if(n.querySelector('script,svg,img,[onload]'))throw Error('unsafe node');
                    for(const a of n.querySelectorAll('[href]'))if(!['http:','https:','mailto:'].includes(new URL(a.href).protocol))throw Error('unsafe href');}
                }""")
                page.evaluate("""()=>{
                  const n=document.createElement('div');n.className='message-content';document.body.append(n);
                  updateMessageContent(n,'**original**');
                  const range=document.createRange();range.selectNodeContents(n);getSelection().addRange(range);
                  updateMessageContent(n,'**stale pending**');updateMessageContent(n,'**original**');
                  if(n._pendingMessage)throw Error('stale selection update');
                  updateMessageContent(n,'**authoritative reset**');getSelection().removeAllRanges();
                  document.dispatchEvent(new Event('selectionchange'));
                  if(n.textContent!=='authoritative reset')throw Error('reset reconciliation');
                  const child=n.firstChild;updateMessageContent(n,'**authoritative reset**');
                  if(n.firstChild!==child)throw Error('unchanged polling replaced DOM');n.remove();
                }""")
                page.locator("#message-input").fill("Render Markdown")
                page.locator("#send").click()
                reply = page.locator("#messages .message.assistant .message-content")
                reply.locator("strong").first.wait_for()
                assert page.locator(".message.assistant .badge").inner_text() != "completed"
                # Preserve selection while final/delta content is pending, then reconcile.
                page.evaluate('''()=>{
                  const n=document.querySelector('.message.assistant .message-content');
                  const range=document.createRange();range.selectNodeContents(n);
                  const s=getSelection();s.removeAllRanges();s.addRange(range);window.selectedDOM=n.innerHTML;
                }''')
                page.wait_for_timeout(400)
                assert reply.inner_html() == page.evaluate("selectedDOM")
                page.evaluate("getSelection().removeAllRanges()")
                page.wait_for_function("() => state.history?.messages.at(-1).status==='completed' && !state.execution", timeout=30000)
                assert page.locator(".message.assistant .badge.completed").count() == 0
                assert reply.inner_html() == page.evaluate("expectedMarkdown")
                assert reply.locator("pre code").inner_text().startswith("<script>window.markdownXss = 1</script>")
                assert reply.locator("ul ul").count() == 1
                assert reply.locator("table").count() == 1
                assert reply.locator("a[href]").count() == 2
                assert reply.locator("script,img,iframe,svg,[onerror]").count() == 0
                assert page.evaluate("window.markdownXss||0") == 0
                page.reload()
                page.locator("#app").wait_for(state="visible")
                page.wait_for_function("() => state.history?.messages.at(-1).status==='completed' && !state.execution")
                assert reply.inner_html() == page.evaluate("text=>{const n=document.createElement('div');n.append(renderChatMarkdown(text));return n.innerHTML}", TEXT)
                # User markup stays literal. Code and links support keyboard access.
                page.evaluate('''text=>{
                  const n=document.querySelector('.message.user .message-content');updateMessageContent(n,text,'user');
                  if(n.textContent!==text||n.children.length)throw Error('user HTML');
                  const code=document.querySelector('.message.assistant pre');code.focus();
                  if(document.activeElement!==code)throw Error('code keyboard focus');
                }''', '<img src=x onerror=alert(1)> **literal**')
                for width, height in ((1440, 1000), (393, 851), (393, 420), (320, 700)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(100)
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (width, height)
                    assert reply.locator("pre").evaluate("n=>n.scrollWidth>n.clientWidth")
                    assert page.locator("#send").bounding_box()["y"] < height
                    if os.environ.get("LINKO_MARKDOWN_EVIDENCE"):
                        page.screenshot(path=str(Path(os.environ["LINKO_MARKDOWN_EVIDENCE"]) / f"markdown-{width}-{height}.png"))
                # Saved/archive reader uses the identical rendering path.
                conversation = page.evaluate("state.history.conversation.id")
                page.evaluate("id=>openSavedHistory(id)", conversation)
                saved = page.locator("#history-detail-messages .message.assistant .message-content")
                saved.wait_for(state="attached")
                assert saved.inner_html() == reply.inner_html()
                assert not errors, errors
                assert not remote, remote
                browser.close()
                print(json.dumps({"markdown": "PASS", "prefixes": len(TEXT) + 1,
                                  "stream_reload_saved_equivalence": True, "security_mobile_selection": True}))
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

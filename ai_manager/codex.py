"""JSON-RPC stdio transport. Only processes created here are supervised."""

import asyncio
import json
import os
import re
import signal
import time
import tomllib
from pathlib import Path

from . import scratch
from .config import Settings
from .tool_content import ImageResult

_RULES_SOURCE = (Path(__file__).resolve().parents[1] / "RULES.md").read_text()
_MANAGER_START, _MANAGER_END = "<!-- manager-only:start -->", "<!-- manager-only:end -->"
_WORKER_START, _WORKER_END = "<!-- worker-only:start -->", "<!-- worker-only:end -->"
RULES = re.sub(re.escape(_WORKER_START) + r".*?" + re.escape(_WORKER_END), "",
               _RULES_SOURCE, flags=re.S).replace(_MANAGER_START, "").replace(_MANAGER_END, "")
WORKER_RULES = re.sub(re.escape(_MANAGER_START) + r".*?" + re.escape(_MANAGER_END), "",
                      _RULES_SOURCE, flags=re.S).replace(_WORKER_START, "").replace(_WORKER_END, "")
# Keep the owner-facing guide and manager advice in one place; omit the rest of the README from the prompt.
_GUIDE_HEADING = "## Architecture design principles\n"
ARCHITECTURE_GUIDE = (_GUIDE_HEADING + (
    (Path(__file__).resolve().parents[1] / "README.md").read_text()
    .split(_GUIDE_HEADING, 1)[1].split("\n## ", 1)[0]
)).strip()


TOOL_HANDLER_WAIT = 5  # seconds a tool call waits for its thread's handler


class CodexError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class TurnUsage:
    """Keep native thread totals, and sum distinct request reports for this execution."""

    def __init__(self):
        self.seen = set()
        self.turn = {}

    def record(self, usage):
        signature = json.dumps(usage.get("total", {}), sort_keys=True)
        if signature not in self.seen:
            self.seen.add(signature)
            for key, value in usage.get("last", {}).items():
                if isinstance(value, int):
                    self.turn[key] = self.turn.get(key, 0) + value
        return {**usage, "turn": self.turn.copy(), "usageReports": len(self.seen)}


def process_start(pid: int) -> str | None:
    try:
        # comm can contain spaces and parentheses; starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def safe_config(native=False) -> dict:
    config = {
        "features.shell_tool": False,
        "features.unified_exec": False,
        "features.shell_snapshot": False,
        "features.code_mode": False,
        "features.multi_agent": False,
        "features.apps": False,
        "features.plugins": False,
        "features.apply_patch_freeform": False,
        "web_search": "disabled",
        "forced_login_method": "chatgpt",
        "model_auto_compact_token_limit": 100000,
    }
    if native:
        return {
            "forced_login_method": "chatgpt",
            "features.shell_tool": True,
            "features.unified_exec": True,
            "features.apply_patch_freeform": True,
            "features.request_user_input": True,
            "approvals_reviewer": "user",
        }
    # Read server names only; never expose loaded config or credentials.
    path = Path.home() / ".codex" / "config.toml"
    try:
        existing = tomllib.loads(path.read_text())
        for name in existing.get("mcp_servers", {}):
            config[f"mcp_servers.{json.dumps(name)}.enabled"] = False
    except (OSError, ValueError):
        pass
    return config


class AppServer:
    account_required = "chatgpt"

    def command(self):
        args = [self.settings.codex_bin, "app-server", "--listen", "stdio://"]
        for key, value in self.config.items():
            args += ["-c", f"{key}={json.dumps(value)}"]
        # Isolated worker: the whole App Server (and every command it runs) belongs to the worker user.
        return self.isolation.wrap("codex", args[1:], self.worker_tag) if self.isolation else args

    def isolate(self, isolation, tag):
        """Run this worker's provider processes as the unprivileged worker user (ai_manager/isolation.py)."""
        self.isolation, self.worker_tag = isolation, tag

    def __init__(self, settings: Settings, native=False):
        self.settings = settings
        self.process = None
        self.process_identity = None
        self.pending = {}
        self.queues = {}
        self.serial = 0
        self.reader = None
        self.stderr_reader = None
        self.start_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.native = native
        self.config = safe_config(native)
        self.question_handlers = {}
        self.catalog = []
        self.catalog_at = 0.0
        self.catalog_retry_at = 0.0
        self.catalog_error = ""
        self.account_type = None
        self.tool_handlers = {}
        # Set by ProviderServer: queues are shared across providers, so a dying process may only signal its own threads.
        self.owns_thread = None
        self.tool_jobs = set()
        self.rate_limits = {}
        self.isolation, self.worker_tag = None, None

    async def start(self):
        async with self.start_lock:
            if self.process and self.process.returncode is None and self.reader and not self.reader.done():
                await self.refresh_catalog()
                return
            await self.close()
            args = self.command()
            self.process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **scratch.env(getattr(self, "scratch", None))},
                start_new_session=True,
                limit=8 * 1024 * 1024,
            )
            self.process_identity = process_start(self.process.pid)
            self.reader = asyncio.create_task(self._read(self.process))
            # Drain stderr without storing arbitrary inherited diagnostics or credentials.
            self.stderr_reader = asyncio.create_task(self._drain(self.process.stderr))
            try:
                await self.request(
                    "initialize",
                    {
                        "clientInfo": {"name": "linko_bot_manager", "title": "Linko Bot Boss", "version": "0.1.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await self.send({"method": "initialized", "params": {}})
                account = await self.request("account/read", {"refreshToken": False})
                self.account_type = (account.get("account") or {}).get("type")
                if self.account_type != self.account_required:
                    raise CodexError("A Codex ChatGPT login is required. API-key providers are disabled.")
                await self.refresh_catalog(force=True)
            except BaseException:
                await self.close()
                raise

    async def refresh_catalog(self, force=False):
        """Metadata only; atomic complete snapshots and bounded stale-cache use."""
        now = time.time()
        if not force and now < max(self.catalog_at + 3600, self.catalog_retry_at):
            if not self.catalog or now - self.catalog_at > 86400:
                raise ValueError('Provider model catalog is unavailable or too stale; retry later')
            return
        try:
            catalog, cursor, seen = [], None, set()
            while True:
                page = await self.request('model/list', {'limit': 100, 'cursor': cursor})
                catalog.extend(m for m in page['data'] if not m.get('hidden'))
                cursor = page.get('nextCursor')
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError('Provider catalog pagination repeated')
                seen.add(cursor)
            if not catalog:
                raise ValueError('Provider model catalog is empty')
            self.catalog, self.catalog_at, self.catalog_retry_at, self.catalog_error = catalog, now, 0.0, ''
        except Exception:
            self.catalog_retry_at, self.catalog_error = now + 300, 'Provider catalog refresh failed'
            if force or not self.catalog or now - self.catalog_at > 86400:
                raise

    async def _drain(self, stream):
        while await stream.read(65536):
            pass

    async def send(self, message):
        async with self.write_lock:
            if not self.process or self.process.returncode is not None:
                raise CodexError("The Codex process is not running", {"codexErrorInfo": "transportLost"})
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method, params, timeout=30):
        self.serial += 1
        request_id = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _read(self, process):
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError:
                    # A line over the stream limit (e.g. a huge thread/read) is dropped, not fatal: the process is
                    # alive and every in-flight turn would otherwise be reported as "stopped unexpectedly".
                    self.dropped_lines = getattr(self, "dropped_lines", 0) + 1
                    continue
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    self.dropped_lines = getattr(self, "dropped_lines", 0) + 1
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    params = message.get("params", {})
                    queue = self.queues.get(params.get("threadId"))
                    if queue is not None:
                        queue.put_nowait({"method": message["method"], "params": params})
                    if message["method"] == "item/tool/call":
                        job = asyncio.create_task(self._tool_call(message))
                        self.tool_jobs.add(job)
                        job.add_done_callback(self.tool_jobs.discard)
                        continue
                    job = asyncio.create_task(self._interaction(message))
                    self.tool_jobs.add(job)
                    job.add_done_callback(self.tool_jobs.discard)
                    continue
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                CodexError(str(message["error"].get("message", "RPC failed")), message["error"])
                            )
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    params = message.get("params", {})
                    if message.get("method") == "account/rateLimits/updated":
                        self.rate_limits = params
                        if listener := getattr(self, "usage_listener", None):
                            listener(params)
                    if message.get("method") == "account/updated":
                        self.rate_limits = {}
                        self.catalog_at, self.catalog_retry_at = 0.0, 0.0
                        if invalidate := getattr(self, "usage_invalidate", None):
                            invalidate()
                    thread_id = params.get("threadId") or params.get("thread_id")
                    if thread_id in self.queues:
                        self.queues[thread_id].put_nowait(message)
        except Exception as error:
            reason = type(error).__name__
        else:
            reason = "connection closed"
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(
                    CodexError(
                        f"The Codex process stopped unexpectedly ({reason})",
                        {"codexErrorInfo": "transportLost"},
                    )
                )
        for thread_id, queue in list(self.queues.items()):
            # Another provider's turn is unaffected (an idle Codex that fails its login check on every start
            # used to break each running Claude manager turn here).
            if self.owns_thread is None or self.owns_thread(thread_id):
                queue.put_nowait({"method": "transport/lost", "params": {"reason": reason}})

    def route(self, model=None, effort=None):
        candidate = (
            next((m for m in self.catalog if m["model"] == model and not m.get("hidden")), None)
            if model
            else next((m for m in self.catalog if m.get("isDefault")), self.catalog[0] if self.catalog else None)
        )
        if not candidate:
            raise ValueError("Model is not in the installed Codex catalog")
        efforts = [e["reasoningEffort"] for e in candidate["supportedReasoningEfforts"]]
        effort = effort or candidate["defaultReasoningEffort"]
        if effort not in efforts:
            raise ValueError(f"Reasoning effort {effort} is not supported by {candidate['model']}")
        return {"model": candidate["model"], "effort": effort}

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        await self.start()
        params = {
            "cwd": str(cwd),
            "model": model,
            "modelProvider": "openai",
            "sandbox": "danger-full-access" if self.native else "read-only",
            "approvalPolicy": "on-request" if self.native else "never",
            "approvalsReviewer": "user",
            "developerInstructions": instructions,
            "config": self.config,
        }
        if thread_id:
            result = await self.request("thread/resume", {**params, "threadId": thread_id, "excludeTurns": True})
        else:
            result = await self.request("thread/start", {**params, "ephemeral": False, "dynamicTools": dynamic_tools})
        return result["thread"]["id"]

    async def _tool_call(self, message):
        params = message.get("params", {})
        handler = self.tool_handlers.get(params.get("threadId"))
        # During startup/recovery the runner registers its handler just after the thread is loaded: wait briefly.
        for _ in range(0 if handler else TOOL_HANDLER_WAIT * 10):
            await asyncio.sleep(0.1)
            handler = self.tool_handlers.get(params.get("threadId"))
            if handler:
                break
        try:
            if not handler:
                raise ValueError("No enabled capabilities for this thread")
            content = await handler(params)
            response = {"success": True, "contentItems": (content.content_items() if isinstance(content, ImageResult)
                        else [{"type": "inputText", "text": json.dumps(content)}])}
        except Exception as error:
            response = {"success": False, "contentItems": [{"type": "inputText", "text": str(error)[:2000]}]}
        await self.send({"id": message["id"], "result": response})

    async def _interaction(self, message):
        params = message.get("params", {})
        handler = self.question_handlers.get(params.get("threadId"))
        try:
            if not handler or message["method"] not in {
                "item/tool/requestUserInput",
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                raise ValueError("Unsupported Codex request")
            result = await handler(message["method"], params)
            await self.send({"id": message["id"], "result": result})
        except Exception as error:
            await self.send({"id": message["id"], "error": {"code": -32601, "message": str(error)[:1000]}})

    async def interrupt(self, thread_id, turn_id):
        return await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def terminate(self):
        """Immediately interrupt this instance's owned process group, retaining disk state."""
        if not self.process or not self.process_identity:
            return
        current = process_start(self.process.pid)
        if current and current != self.process_identity:
            return
        self._signal(signal.SIGKILL, wait=False)

    def _signal(self, sig, wait=True):
        """Signal the dedicated process group; an isolated worker's processes belong to another user, so they are
        signalled by run tag through the fixed launcher."""
        try:
            os.killpg(self.process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        if self.isolation:
            self.isolation.signal(self.worker_tag, sig, wait)

    async def _signal_async(self, sig):
        if self.isolation:
            await asyncio.to_thread(self._signal, sig)  # waits for the launcher's kill helper
        else:
            self._signal(sig)

    async def close(self):
        for job in list(self.tool_jobs):
            job.cancel()
        await asyncio.gather(*list(self.tool_jobs), return_exceptions=True)
        if self.process and self.process_identity:
            current = process_start(self.process.pid)
            if current and current != self.process_identity:
                return  # Never signal a recycled PID's unrelated group.
        if self.process and self.process.returncode is None:
            # Terminate only the dedicated process group this instance created.
            await self._signal_async(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                await self._signal_async(signal.SIGKILL)
                await self.process.wait()
        if self.process and self.process.returncode is not None:
            # The parent can exit before its command children. Clear the dedicated
            # group before the caller releases the project claim, even after EOF.
            await self._signal_async(signal.SIGKILL)
        if self.reader and self.reader is not asyncio.current_task():
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.stderr_reader:
            await asyncio.gather(self.stderr_reader, return_exceptions=True)


RESTRICTED_NOTE = """
This is a restricted work instance with exactly one fixed project: its folder is the only place you work in. There is no
All Projects view, no project add/remove, no deploy, deployment_status, save_deploy_setup, emergency_repair or
repair_status (ignore every earlier mention of them; the application rejects them), and the application's own code is
not editable here. Passing work only merges into the project's main branch. Documents, forms, essays, email drafts and
browsing are normal work here.
"""
# The manager prompt lives in harnesses/linko-manager/prompt.md (the default manager harness; ai_manager/harness.py).
from .harness import builtin  # noqa: E402 -- needs RULES, ARCHITECTURE_GUIDE and RESTRICTED_NOTE above

MANAGER_INSTRUCTIONS = builtin("linko").instructions()
RESTRICTED_INSTRUCTIONS = builtin("linko").instructions(restricted=True)

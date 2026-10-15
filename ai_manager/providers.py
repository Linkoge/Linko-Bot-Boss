"""Codex and Claude behind one App Server interface, routed by exact model and thread.

The owner picks the active provider for new work. Existing assignments and manager turns keep
their exact model, so a pinned Codex assignment still runs on Codex after switching to Claude.
"""

import sys
from pathlib import Path

from .codex import AppServer


def provider_of(model):
    return 'claude' if str(model or '').startswith('claude-') else 'codex'


class ClaudeServer(AppServer):
    account_required = 'claude'

    def __init__(self, settings, native=False):
        super().__init__(settings, native)
        self.config = {}

    def command(self):
        # "app-server" in argv lets orphan recovery recognize this owned process group.
        host = Path(__file__).with_name('claude_host.py')  # Self-contained; independent of cwd/sys.path.
        args = [sys.executable, '-I', str(host), 'app-server',
                '--data', str(self.settings.data_dir), '--claude', self.settings.claude_bin]
        return args + ['--native'] if self.native else args


class ProviderServer:
    def __init__(self, settings, native=False, active=lambda: 'codex', backends=None):
        self.settings = settings
        self.backends = backends or {'codex': AppServer(settings, native), 'claude': ClaudeServer(settings, native)}
        self.active = active
        self.queues, self.question_handlers, self.tool_handlers = {}, {}, {}
        for backend in self.backends.values():
            backend.queues, backend.question_handlers = self.queues, self.question_handlers
            backend.tool_handlers = self.tool_handlers
        self.threads = {}
        self.current = None

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in ('usage_listener', 'usage_invalidate'):
            # Account usage follows whichever provider is active.
            for key, backend in self.backends.items():
                setattr(backend, name, (lambda *a, key=key, f=value: f(*a) if self.active() == key else None))

    def backend(self, name=None):
        return self.backends[name or self.active()]

    def thread_provider(self, thread_id):
        if thread_id in self.threads:
            return self.threads[thread_id]
        return 'claude' if (self.settings.data_dir / 'claude-threads' / f'{thread_id}.json').is_file() else 'codex'

    @property
    def catalog(self):
        return [m for backend in self.backends.values() for m in backend.catalog]

    @property
    def account_type(self):
        return self.backend().account_type

    @property
    def catalog_at(self):
        return self.backend().catalog_at

    @property
    def catalog_error(self):
        return self.backend().catalog_error

    @property
    def rate_limits(self):
        return self.backend().rate_limits

    @property
    def process(self):
        return self.backend(self.current).process

    def owns_pid(self, pid):
        return any(b.process and b.process.pid == pid for b in self.backends.values())

    async def start(self):
        failures = {}
        for name, backend in self.backends.items():
            try:
                await backend.start()
            except Exception as error:
                failures[name] = error  # An unused provider must not block the active one.
        if self.active() in failures:
            raise failures[self.active()]

    def route(self, model=None, effort=None):
        self.current = provider_of(model) if model else self.active()
        return self.backend(self.current).route(model, effort)

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        name = provider_of(model) if model else self.active()
        if thread_id and self.thread_provider(thread_id) != name:
            thread_id = None  # Another provider's history cannot be resumed here.
        thread_id = await self.backend(name).thread(cwd, instructions, thread_id, model, dynamic_tools)
        self.threads[thread_id], self.current = name, name
        return thread_id

    async def request(self, method, params, timeout=30):
        thread_id = (params or {}).get('threadId')
        name = self.thread_provider(thread_id) if thread_id else None
        return await self.backend(name).request(method, params, timeout=timeout)

    async def interrupt(self, thread_id, turn_id):
        return await self.backend(self.thread_provider(thread_id)).interrupt(thread_id, turn_id)

    def terminate(self):
        for backend in self.backends.values():
            backend.terminate()

    async def close(self):
        for backend in self.backends.values():
            await backend.close()


def owns_pid(server, pid):
    if hasattr(server, 'owns_pid'):
        return server.owns_pid(pid)
    return bool(server.process and server.process.pid == pid)

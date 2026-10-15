import asyncio
import json
import os
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ai_manager.config import Settings
from ai_manager.providers import ClaudeServer, ProviderServer, owns_pid
from ai_manager.recovery import classify


class Backend:
    def __init__(self, name, pid):
        self.name, self.process, self.catalog = name, SimpleNamespace(pid=pid), []
        self.account_type, self.catalog_at, self.catalog_error, self.rate_limits = name, 0, '', {}
        self.calls = []

    async def start(self):
        if self.name == 'codex':
            raise RuntimeError('codex logged out')

    def route(self, model=None, effort=None):
        return {'model': model, 'effort': effort, 'backend': self.name}

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.calls.append(('thread', thread_id))
        return thread_id or f'{self.name}-thread'

    async def request(self, method, params, timeout=30):
        self.calls.append((method, params.get('threadId')))
        return {}

    async def interrupt(self, thread_id, turn_id):
        self.calls.append(('interrupt', thread_id))

    def terminate(self):
        pass

    async def close(self):
        pass


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_provider_server_routes_by_model_and_thread(tmp_path):
    settings = Settings(data_dir=tmp_path)
    active = ['claude']
    server = ProviderServer(settings, active=lambda: active[0],
                            backends={'codex': Backend('codex', 11), 'claude': Backend('claude', 22)})
    await server.start()  # Inactive Codex failure does not block Claude.
    active[0] = 'codex'
    with pytest.raises(RuntimeError):
        await server.start()
    assert server.route('gpt-6-luna', 'low')['backend'] == 'codex'
    assert server.process.pid == 11
    assert server.route('claude-haiku-5-5', 'low')['backend'] == 'claude'
    assert server.process.pid == 22 and owns_pid(server, 11) and not owns_pid(server, 33)
    thread = await server.thread(tmp_path, '', 'codex-thread', 'claude-sonnet-5-5')
    assert thread == 'claude-thread' and server.backends['claude'].calls[0] == ('thread', None)
    await server.request('turn/start', {'threadId': thread})
    await server.interrupt(thread, 'turn')
    assert server.backends['claude'].calls[1:] == [('turn/start', thread), ('interrupt', thread)]
    seen = []
    server.usage_listener = seen.append
    server.backends['claude'].usage_listener({'claude': 1})
    server.backends['codex'].usage_listener({'codex': 1})
    assert seen == [{'codex': 1}]
    assert server.backends['codex'].queues is server.queues is server.backends['claude'].queues


FAKE_CLAUDE = '''#!{python}
import json, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
session = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]
def out(event):
    print(json.dumps(event), flush=True)
out({{'type': 'system', 'subtype': 'init', 'session_id': session}})
if 'SLEEP' in prompt:
    time.sleep(30)
if 'LIMIT' in prompt:
    out({{'type': 'result', 'is_error': True, 'result': "You've hit your usage limit", 'session_id': session}})
    sys.exit(1)
for text in ('first', 'second'):
    out({{'type': 'stream_event', 'event': {{'type': 'content_block_start', 'content_block': {{'type': 'text'}}}}}})
    out({{'type': 'stream_event', 'event': {{'type': 'content_block_delta', 'delta': {{'type': 'text_delta', 'text': text}}}}}})
    out({{'type': 'stream_event', 'event': {{'type': 'content_block_stop'}}}})
    out({{'type': 'assistant', 'message': {{'content': [{{'type': 'text', 'text': text}}],
        'usage': {{'input_tokens': 3, 'cache_read_input_tokens': 2, 'output_tokens': 1}}}}}})
out({{'type': 'rate_limit_event', 'rate_limit_info': {{'unifiedWindows': {{
    'five_hour': {{'utilization': 0.25, 'resetsAt': 4102444800}}, 'seven_day': {{'utilization': 0.5, 'resetsAt': 4102444800}}}}}}}})
out({{'type': 'result', 'is_error': False, 'result': 'first second', 'session_id': session,
     'resumed': '--resume' in args, 'model': args[args.index('--model') + 1]}})
'''


async def run_turn(server, thread, text):
    queue = asyncio.Queue()
    server.queues[thread] = queue
    turn = await server.request('turn/start', {'threadId': thread, 'clientUserMessageId': 'key',
                                               'input': [{'type': 'text', 'text': text}],
                                               'model': 'claude-haiku-5-5', 'effort': 'low'})
    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), 20)
        events.append(event)
        if event['method'] == 'turn/completed':
            return turn['turn']['id'], events


@pytest.mark.asyncio
async def test_claude_host_translates_turns_resume_interrupt_and_limits(tmp_path):
    fake = tmp_path / 'claude'
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    settings = Settings(data_dir=tmp_path / 'data', claude_bin=str(fake))
    settings.prepare()
    server = ClaudeServer(settings, native=True)
    limits = []
    server.usage_listener = limits.append
    try:
        await server.start()
        assert server.account_type == 'claude'
        assert server.route('claude-opus-5-5', 'high') == {'model': 'claude-opus-5-5', 'effort': 'high'}
        assert b'app-server' in open(f'/proc/{server.process.pid}/cmdline', 'rb').read()
        thread = await server.thread(tmp_path, 'Be helpful', None, 'claude-haiku-5-5')
        turn_id, events = await run_turn(server, thread, 'hello')
        deltas = ''.join(e['params']['delta'] for e in events if e['method'] == 'item/agentMessage/delta')
        assert deltas == 'first\n\nsecond'
        completed = events[-1]['params']['turn']
        assert completed['id'] == turn_id and completed['status'] == 'completed'
        usage = [e for e in events if e['method'] == 'thread/tokenUsage/updated'][-1]['params']['tokenUsage']
        assert usage['total']['inputTokens'] == 10 and usage['last']['outputTokens'] == 1
        assert limits[-1]['rateLimits']['primary']['usedPercent'] == 25.0
        assert (await server.request('account/rateLimits/read', {}))['rateLimits']['secondary']['windowDurationMins'] == 10080

        assert await server.thread(tmp_path, 'Be helpful', thread, 'claude-haiku-5-5') == thread
        await run_turn(server, thread, 'again')
        saved = await server.request('thread/read', {'threadId': thread, 'includeTurns': True})
        turns = saved['thread']['turns']
        assert [t['status'] for t in turns] == ['completed', 'completed']
        assert turns[0]['items'][0]['clientId'] == 'key' and turns[0]['items'][1]['text'] == 'first\n\nsecond'

        _, events = await run_turn(server, thread, 'LIMIT')
        error = events[-1]['params']['turn']['error']
        assert events[-1]['params']['turn']['status'] == 'failed'
        assert error['codexErrorInfo'] == 'usageLimitExceeded' and classify(error) is None

        queue = asyncio.Queue()
        server.queues[thread] = queue
        turn = await server.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'SLEEP'}],
                                                   'model': 'claude-haiku-5-5', 'effort': 'low'})
        await asyncio.sleep(0.5)
        await server.interrupt(thread, turn['turn']['id'])
        while (event := await asyncio.wait_for(queue.get(), 20))['method'] != 'turn/completed':
            pass
        assert event['params']['turn']['status'] == 'interrupted'
    finally:
        await server.close()


def test_claude_model_list_comes_from_the_environment_with_names_derived_from_ids():
    def catalog(**env):
        out = subprocess.run([sys.executable, '-c', 'import json,ai_manager.claude_host as h;print(json.dumps(h.CATALOG))'],
                             capture_output=True, text=True, check=True, env={**os.environ, **env}).stdout
        return json.loads(out)

    default = catalog(AI_MANAGER_CLAUDE_MODELS='claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5')
    assert [(m['displayName'], m['isDefault']) for m in default] == [('Haiku', False), ('Sonnet', True), ('Opus', False)]
    custom = catalog(AI_MANAGER_CLAUDE_MODELS='claude-fable-5-1,claude-opus-5-5')
    assert [m['model'] for m in custom] == ['claude-fable-5-1', 'claude-opus-5-5'] and custom[0]['displayName'] == 'Fable'

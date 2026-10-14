"""Claude Code behind the subset of the App Server stdio protocol this application uses.

Run as ``python -m ai_manager.claude_host app-server --data DIR [--native]``. Each turn is one
``claude -p`` process in this host's process group, so the existing supervision, termination
and recovery paths apply unchanged. Threads are Claude session IDs; turn history is kept on disk
for ``thread/read`` reconciliation after a restart.
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import uuid
from pathlib import Path

LEVELS = ('low', 'medium', 'high')
CATALOG = [
    {'model': model, 'displayName': name, 'isDefault': name == 'Sonnet', 'inputModalities': ['text', 'image'],
     'supportedReasoningEfforts': [{'reasoningEffort': e} for e in LEVELS], 'defaultReasoningEffort': 'medium'}
    for name, model in (('Haiku', 'claude-haiku-5-5'), ('Sonnet', 'claude-sonnet-5-5'), ('Opus', 'claude-opus-5-5'))
]
MCP_SERVER = 'linko'


def usage_counts(usage):
    cached = usage.get('cache_read_input_tokens', 0) or 0
    created = usage.get('cache_creation_input_tokens', 0) or 0
    fresh = usage.get('input_tokens', 0) or 0
    output = usage.get('output_tokens', 0) or 0
    thinking = (usage.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0
    return {'inputTokens': fresh + cached + created, 'cachedInputTokens': cached, 'outputTokens': output,
            'reasoningOutputTokens': thinking, 'totalTokens': fresh + cached + created + output}


def failure(result):
    """Map a Claude result to the error details the recovery classifier understands."""
    text = str(result.get('result') or result.get('subtype') or 'Claude turn failed')
    status = result.get('api_error_status')
    lower = text.lower()
    if status == 429 or 'usage limit' in lower or 'rate limit' in lower:
        info = 'usageLimitExceeded'
    elif status in (500, 502, 503, 504, 529) or 'overloaded' in lower:
        info = 'serverOverloaded'
    elif status in (401, 403) or 'login' in lower or 'authenticat' in lower:
        info = 'unauthorized'
    else:
        info = 'other'
    return {'message': text[:2000], 'codexErrorInfo': info, **({'httpStatusCode': status} if status else {})}


class Host:
    def __init__(self, data, claude, native):
        self.dir = Path(data) / 'claude-threads'
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.claude, self.native = claude, native
        # Unix socket paths are short (108 bytes); keep it in a private temporary directory.
        self.socket = Path(tempfile.mkdtemp(prefix='linko-claude-')) / 'host.sock'
        self.running = {}  # thread_id -> {'turn', 'process', 'interrupted'}
        self.pending = {}
        self.serial = 0
        self.write_lock = asyncio.Lock()
        self.limits_file = Path(data) / 'claude-rate-limits.json'

    # Persistence -------------------------------------------------------------------------
    def load(self, thread_id):
        path = self.dir / f'{thread_id}.json'
        if not path.is_file():
            raise ValueError('Unknown Claude thread')
        return json.loads(path.read_text())

    def save(self, thread):
        path = self.dir / f"{thread['id']}.json"
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(thread))
        temporary.replace(path)

    # Transport ---------------------------------------------------------------------------
    async def send(self, message):
        async with self.write_lock:
            sys.stdout.write(json.dumps(message) + '\n')
            sys.stdout.flush()

    async def notify(self, method, params):
        await self.send({'method': method, 'params': params})

    async def ask(self, method, params, timeout=600):
        self.serial += 1
        request_id = f'claude-host-{self.serial}'
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def serve(self):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        bridge = await asyncio.start_unix_server(self.bridge, path=str(self.socket))
        os.chmod(self.socket, 0o600)
        try:
            while line := await reader.readline():
                message = json.loads(line)
                if 'method' in message:
                    asyncio.create_task(self.handle(message))
                elif message.get('id') in self.pending:
                    future = self.pending[message['id']]
                    if not future.done():
                        future.set_result(message.get('result') or {'success': False, 'contentItems': [
                            {'type': 'inputText', 'text': str((message.get('error') or {}).get('message', 'failed'))}]})
        finally:
            bridge.close()
            self.socket.unlink(missing_ok=True)
            self.socket.parent.rmdir()
            for state in self.running.values():
                if state['process'].returncode is None:
                    state['process'].kill()

    async def handle(self, message):
        method, params = message['method'], message.get('params') or {}
        if 'id' not in message:
            return  # Notifications such as `initialized`.
        try:
            handler = getattr(self, 'rpc_' + method.replace('/', '_'), None)
            if not handler:
                raise LookupError(f'Unsupported method {method}')
            await self.send({'id': message['id'], 'result': await handler(params)})
        except LookupError as error:
            await self.send({'id': message['id'], 'error': {'code': -32601, 'message': str(error)}})
        except Exception as error:
            await self.send({'id': message['id'], 'error': {'code': -32000, 'message': str(error)[:2000]}})

    # Protocol ----------------------------------------------------------------------------
    async def rpc_initialize(self, params):
        return {'serverInfo': {'name': 'linko-claude-host'}}

    async def rpc_account_read(self, params):
        return {'account': {'type': 'claude'}}

    async def rpc_model_list(self, params):
        return {'data': CATALOG, 'nextCursor': None}

    async def rpc_account_rateLimits_read(self, params):
        try:
            return json.loads(self.limits_file.read_text())
        except (OSError, ValueError):
            return {}

    async def rpc_thread_start(self, params):
        thread = {'id': str(uuid.uuid4()), 'session': False, 'turns': []}
        return self.configure(thread, params)

    async def rpc_thread_resume(self, params):
        return self.configure(self.load(params['threadId']), params)

    def configure(self, thread, params):
        tools = params.get('dynamicTools')
        thread.update(cwd=params['cwd'], instructions=params.get('developerInstructions') or '',
                      model=params.get('model'), native=self.native)
        if tools is not None or 'tools' not in thread:
            thread['tools'] = tools or []
        self.save(thread)
        return {'thread': {'id': thread['id']}}

    async def rpc_thread_read(self, params):
        thread = self.load(params['threadId'])
        for turn in thread['turns']:
            if turn['status'] == 'inProgress' and self.running.get(thread['id'], {}).get('turn') != turn['id']:
                turn['status'] = 'interrupted'  # Its process belonged to an earlier host.
        return {'thread': {'id': thread['id'], 'turns': thread['turns'] if params.get('includeTurns') else []}}

    async def rpc_turn_start(self, params):
        thread = self.load(params['threadId'])
        if thread['id'] in self.running:
            raise ValueError('A Claude turn is already running in this thread')
        text = '\n\n'.join(i['text'] for i in params['input'] if i.get('type') == 'text')
        images = [i['path'] for i in params['input'] if i.get('type') == 'localImage']
        if images:
            text += '\n\nAttached images (open them with the Read tool):\n' + '\n'.join(images)
        turn = {'id': str(uuid.uuid4()), 'status': 'inProgress', 'items': [
            {'type': 'userMessage', 'clientId': params.get('clientUserMessageId'), 'content': [{'type': 'text', 'text': text}]}]}
        thread['turns'].append(turn)
        thread['turns'] = thread['turns'][-50:]
        self.save(thread)
        args = [self.claude, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                '--model', params.get('model') or thread['model'], '--strict-mcp-config']
        if params.get('effort'):
            args += ['--effort', params['effort']]
        args += ['--resume', thread['id']] if thread['session'] else ['--session-id', thread['id']]
        if thread['instructions']:
            args += ['--append-system-prompt', thread['instructions']]
        if thread['native']:
            args += ['--permission-mode', 'bypassPermissions']
        else:
            # Manager: read-only file access for attachments plus its application tools.
            allowed = ['Read'] + [f"mcp__{MCP_SERVER}__{t['name']}" for t in thread['tools']]
            args += ['--tools', 'Read', '--allowedTools', *allowed]
        if thread['tools']:
            bridge = Path(__file__).with_name('claude_mcp.py')
            config = {'mcpServers': {MCP_SERVER: {'command': sys.executable, 'args': [
                '-I', str(bridge), str(self.socket), thread['id']]}}}
            args += ['--mcp-config', json.dumps(config)]
        process = await asyncio.create_subprocess_exec(
            *args, cwd=thread['cwd'], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=64 * 1024 * 1024)
        process.stdin.write(text.encode())
        process.stdin.close()
        self.running[thread['id']] = {'turn': turn['id'], 'process': process, 'interrupted': False}
        asyncio.create_task(self.pump(thread['id'], turn['id'], process))
        return {'turn': {'id': turn['id']}}

    async def rpc_turn_interrupt(self, params):
        state = self.running.get(params['threadId'])
        if not state or state['turn'] != params['turnId']:
            return {}
        state['interrupted'] = True
        if state['process'].returncode is None:
            state['process'].send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(state['process'].wait(), 5)
            except TimeoutError:
                state['process'].kill()
        return {}

    # Turn translation --------------------------------------------------------------------
    async def pump(self, thread_id, turn_id, process):
        ids = {'threadId': thread_id, 'turnId': turn_id}
        texts, result, separate, block_is_text = [], None, False, False
        totals = {}
        try:
            async for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get('type')
                if kind == 'system' and event.get('subtype') == 'init':
                    thread = self.load(thread_id)
                    if not thread['session']:
                        thread['session'] = True
                        self.save(thread)
                elif kind == 'stream_event' and not event.get('parent_tool_use_id'):
                    inner = event.get('event') or {}
                    if inner.get('type') == 'content_block_start':
                        block = inner.get('content_block') or {}
                        block_is_text = block.get('type') == 'text'
                        if block.get('type') == 'tool_use':
                            await self.notify('item/started', {**ids, 'item': {
                                'type': 'mcpToolCall' if block.get('name', '').startswith('mcp__') else 'commandExecution',
                                'id': block.get('id'), 'tool': block.get('name')}})
                    elif inner.get('type') == 'content_block_delta' and block_is_text:
                        delta = (inner.get('delta') or {}).get('text', '')
                        if delta:
                            if separate:
                                delta, separate = '\n\n' + delta, False
                            await self.notify('item/agentMessage/delta', {**ids, 'itemId': turn_id, 'delta': delta})
                    elif inner.get('type') == 'content_block_stop' and block_is_text:
                        separate, block_is_text = True, False
                elif kind == 'assistant' and not event.get('parent_tool_use_id'):
                    message = event.get('message') or {}
                    texts += [c['text'] for c in message.get('content', []) if c.get('type') == 'text' and c.get('text')]
                    if message.get('usage'):
                        last = usage_counts(message['usage'])
                        totals = {k: totals.get(k, 0) + v for k, v in last.items()}
                        await self.notify('thread/tokenUsage/updated', {**ids, 'tokenUsage': {
                            'total': totals, 'last': last, 'modelContextWindow': None}})
                elif kind == 'rate_limit_event':
                    await self.limits(event.get('rate_limit_info') or {})
                elif kind == 'result':
                    result = event
            await process.wait()
        finally:
            state = self.running.pop(thread_id, {})
            if process.returncode is None:
                process.kill()
        if state.get('interrupted'):
            status, error = 'interrupted', None
        elif result and not result.get('is_error'):
            status, error = 'completed', None
        elif result:
            status, error = 'failed', failure(result)
        else:
            status, error = 'failed', {'message': f'Claude exited ({process.returncode}) without a result',
                                       'codexErrorInfo': 'transportLost'}
        thread = self.load(thread_id)
        turn = next(t for t in thread['turns'] if t['id'] == turn_id)
        turn['items'].append({'type': 'agentMessage', 'text': '\n\n'.join(texts)})
        turn['status'] = status
        if error:
            turn['error'] = error
        self.save(thread)
        await self.notify('turn/completed', {'threadId': thread_id, 'turn': turn})

    async def limits(self, info):
        windows = info.get('unifiedWindows') or {}
        snapshot = {'limitId': 'claude', 'limitName': 'Claude'}
        for slot, key, minutes in (('primary', 'five_hour', 300), ('secondary', 'seven_day', 10080)):
            window = windows.get(key)
            if isinstance(window, dict) and isinstance(window.get('utilization'), (int, float)):
                snapshot[slot] = {'usedPercent': round(window['utilization'] * 100, 1), 'windowDurationMins': minutes,
                                  'resetsAt': window.get('resetsAt')}
        if 'primary' not in snapshot and 'secondary' not in snapshot:
            return
        payload = {'rateLimits': snapshot}
        self.limits_file.write_text(json.dumps(payload))
        await self.notify('account/rateLimits/updated', payload)

    # MCP bridge --------------------------------------------------------------------------
    async def bridge(self, reader, writer):
        try:
            request = json.loads(await reader.readline())
            if request.get('op') == 'list':
                tools = self.load(request['thread'])['tools']
                writer.write((json.dumps({'tools': [{k: t[k] for k in ('name', 'description', 'inputSchema')}
                                                    for t in tools]}) + '\n').encode())
                await writer.drain()
                writer.close()
                return
            state = self.running.get(request['thread'])
            if not state:
                raise ValueError('No running turn for this tool call')
            result = await self.ask('item/tool/call', {
                'threadId': request['thread'], 'turnId': state['turn'], 'callId': str(uuid.uuid4()),
                'tool': request['tool'], 'arguments': request.get('arguments') or {}})
            text = '\n'.join(i.get('text', '') for i in result.get('contentItems', []))
            reply = {'success': bool(result.get('success')), 'text': text}
        except Exception as error:
            reply = {'success': False, 'text': str(error)[:2000]}
        writer.write((json.dumps(reply) + '\n').encode())
        await writer.drain()
        writer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['app-server'])
    parser.add_argument('--data', required=True)
    parser.add_argument('--claude', default='claude')
    parser.add_argument('--native', action='store_true')
    options = parser.parse_args()
    asyncio.run(Host(options.data, options.claude, options.native).serve())


if __name__ == '__main__':
    main()

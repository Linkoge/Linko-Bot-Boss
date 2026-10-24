"""Read-only progress of host repair requests, from the durable receipts and run logs under DATA/repair.

Standard library only. Every read tolerates missing, unreadable or half-written files, and nothing is ever written.
Only a short, redacted "last step" leaves this module, never log content."""
import json
import re
import time
from pathlib import Path

DEFAULT_TURN_SECONDS = 7200  # same default as scripts/emergency_repair.py
DEFAULT_MAX_ATTEMPTS = 5
FINISHED_SHOW_SECONDS = 600  # a finished request stays visible this long
STEP_CHARS = 120
TAIL_BYTES = 256 * 1024
STATES = {'pending': 'queued', 'running': 'running', 'waiting': 'waiting', 'succeeded': 'done', 'failed': 'failed'}

SECRET = [
    (re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}'), r'\1 ***'),
    (re.compile(r'(?i)(--?(?:password|passwd|token|secret|api[-_]?key|key)[= ])\S+'), r'\1***'),
    (re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*=\S+'), lambda m: m.group(0).split('=', 1)[0] + '=***'),
    (re.compile(r'\b(?:sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[a-z]|AKIA|AIza)[-_A-Za-z0-9]{8,}'), '***'),
    (re.compile(r'\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{28,}'), '***'),
]


def redact(text):
    for pattern, replacement in SECRET:
        text = pattern.sub(replacement, text)
    return text


def trim(text, limit=STEP_CHARS):
    text = redact(' '.join(str(text).split()))
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _tail_events(path):
    """Parsed JSON events from the end of a log; a partial first or last line is skipped."""
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - TAIL_BYTES))
            data = stream.read()
    except OSError:
        return []
    lines = data.decode('utf-8', 'replace').splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _tool_name(name, arguments):
    command = arguments.get('command') or arguments.get('file_path') or arguments.get('path') or ''
    if isinstance(command, list):
        command = ' '.join(map(str, command))
    return f'{name}: {command}' if command else str(name)


def _steps(event):
    """(kind, text) pairs of one event for Codex (`item.*`) and Claude (`assistant`) JSON logs."""
    item = event.get('item')
    if isinstance(item, dict):
        kind = item.get('type')
        if kind == 'command_execution' and item.get('command'):
            return [('tool', 'command: ' + str(item['command']))]
        if kind == 'agent_message' and item.get('text'):
            return [('text', item['text'])]
        if kind in {'mcp_tool_call', 'file_change', 'web_search'}:
            return [('tool', kind.replace('_', ' '))]
    message = event.get('message')
    if event.get('type') == 'assistant' and isinstance(message, dict) and isinstance(message.get('content'), list):
        found = []
        for part in message['content']:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'tool_use':
                arguments = part.get('input') if isinstance(part.get('input'), dict) else {}
                found.append(('tool', _tool_name(part.get('name', 'tool'), arguments)))
            elif part.get('type') == 'text' and str(part.get('text', '')).strip():
                found.append(('text', part['text']))
        return found
    return []


def last_step(events):
    tool = text = step = ''
    for event in events:
        for kind, value in _steps(event):
            value = value.strip()
            if kind == 'text':
                value = next((line for line in reversed(value.splitlines()) if line.strip()), '')
                text = value
            else:
                tool = value
            step = value or step
    return {'last_tool': trim(tool) if tool else '', 'last_text': trim(text) if text else '',
            'last_step': trim(step) if step else ''}


def request_progress(root, receipt, config, now):
    turn = config.get('turn_seconds') or DEFAULT_TURN_SECONDS
    status = receipt.get('status')
    attempts = int(receipt.get('attempts') or 0)
    log = Path(root) / 'runs' / str(receipt['id']) / f'{attempts}.jsonl'
    try:
        log_at = log.stat().st_mtime
    except OSError:
        log_at = 0
    started = receipt.get('started_at') or (receipt.get('deadline', 0) - turn if receipt.get('deadline') else 0) \
        or receipt.get('created_at') or 0
    state = STATES.get(status, 'queued')
    if state == 'running' and attempts > 1:
        state = 'retrying'
    activity = max(log_at, receipt.get('updated_at') or 0, started)
    stale = state in {'running', 'retrying'} and now - activity > turn
    row = {'id': receipt['id'], 'state': state, 'provider': receipt.get('provider'), 'model': receipt.get('model'),
           'effort': receipt.get('effort'),
           'conversation_id': receipt.get('conversation_id'), 'attempt': attempts,
           'max_attempts': config.get('max_attempts') or DEFAULT_MAX_ATTEMPTS,
           'started_at': started or None, 'deadline': receipt.get('deadline'),
           'retry_at': receipt.get('retry_at') or None, 'turn_seconds': turn,
           'elapsed': max(0, int(now - started)) if started else 0, 'stale': stale,
           'idle': max(0, int(now - activity)) if activity else 0, 'updated_at': receipt.get('updated_at')}
    row.update(last_step(_tail_events(log)))
    return row


def host_requests(data_dir, now=None):
    """Active (and just-finished) host repair requests; never raises on missing or partial files."""
    now = time.time() if now is None else now
    root = Path(data_dir) / 'repair'
    config = _json(root / 'installed.json') or {}
    rows = []
    try:
        paths = sorted((root / 'requests').glob('*.json'))
    except OSError:
        return rows
    for path in paths:
        receipt = _json(path)
        if not receipt or not isinstance(receipt.get('id'), str):
            continue
        if receipt.get('status') in {'succeeded', 'failed'} and now - (receipt.get('updated_at') or 0) > FINISHED_SHOW_SECONDS:
            continue
        try:
            rows.append(request_progress(root, receipt, config, now))
        except (OSError, ValueError, TypeError):
            continue
    return rows

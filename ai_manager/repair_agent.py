"""Read-only status of the host agent (emergency repair): one compact state, plus a bounded summary for the manager.

Standard library only; reads the launcher's files under DATA/repair (installed.json, heartbeat.json, requests/*.json,
runs/<id>/<attempt>.jsonl) and never writes. Tolerates missing or half-written files."""
import json
import re
import time
from pathlib import Path

from .repair_progress import trim

HEARTBEAT_STALE_SECONDS = 600  # the launcher ticks every minute; none for this long means it is not running
ACTIVITY_STALE_SECONDS = 1200  # a running job whose log and receipt are silent this long is stuck
SUMMARY_JOBS = 5
RESULT_CHARS = 100
ERROR_CHARS = 160
AUTH_MESSAGE = 'Agent login expired - run `claude /login` (or `codex login`) as the host agent user'
AUTH_PATTERN = re.compile(
    r'(?i)(oauth token has expired|token (?:has )?expired|login (?:has )?expired|not logged in|please run /login|'
    r'run\s+`?(?:claude|codex)\s+/?login|authentication[_ ]error|invalid[ _]api[ _]key|failed to authenticate|'
    r'invalid authentication credentials|\b401\b.{0,40}unauthori[sz]ed|unauthori[sz]ed.{0,40}\b401\b)')


def looks_expired(text):
    return bool(text) and bool(AUTH_PATTERN.search(str(text)))


def _json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0


def _title(row):
    text = ' '.join(str(row.get('instructions') or row.get('id') or '').split())
    return trim(text, 80)


def _error(row):
    if row.get('auth_expired') or looks_expired(row.get('error')):
        return AUTH_MESSAGE
    return trim(row['error'], ERROR_CHARS) if row.get('error') else ''


def agent_status(data_dir, now=None):
    """{'installed', 'state', 'job', 'started_at', 'heartbeat', 'last_error', 'auth_expired', 'recent'}.
    state: idle | running | waiting | stuck | login_expired | not_installed."""
    now = time.time() if now is None else now
    root = Path(data_dir) / 'repair'
    config = _json(root / 'installed.json')
    out = {'installed': bool(config), 'state': 'not_installed', 'job': None, 'started_at': None, 'heartbeat': None,
           'last_error': '', 'auth_expired': False, 'recent': []}
    if not config:
        return out
    rows = []
    for path in sorted((root / 'requests').glob('*.json')) if (root / 'requests').is_dir() else []:
        row = _json(path)
        if row and isinstance(row.get('id'), str):
            rows.append(row)
    rows.sort(key=lambda r: (r.get('created_at') or 0, r['id']))
    beat = (_json(root / 'heartbeat.json') or {}).get('at')
    beat = beat if isinstance(beat, (int, float)) else 0
    active = next((r for r in rows if r.get('status') not in {'succeeded', 'failed'}), None)
    finished = [r for r in rows if r.get('status') in {'succeeded', 'failed'}]
    out['heartbeat'] = beat or None
    out['recent'] = [{'id': r['id'], 'status': r['status'], 'title': _title(r),
                      'result': trim(r.get('result') or r.get('error') or '', RESULT_CHARS),
                      'at': r.get('updated_at')} for r in reversed(finished[-SUMMARY_JOBS:])]
    failed = [r for r in finished if r['status'] == 'failed']
    if active:
        out['job'] = {'id': active['id'], 'title': _title(active), 'status': active.get('status'),
                      'attempt': active.get('attempts') or 0, 'provider': active.get('provider')}
        out['started_at'] = active.get('started_at') or active.get('created_at')
        log = root / 'runs' / active['id'] / f"{active.get('attempts') or 0}.jsonl"
        activity = max(_mtime(log), active.get('updated_at') or 0)
        if active.get('status') == 'running':
            out['heartbeat'] = max(beat, _mtime(log)) or None
        out['last_error'] = _error(active)
        if active.get('status') == 'running':
            out['state'] = 'stuck' if now - activity > ACTIVITY_STALE_SECONDS else 'running'
        else:
            out['state'] = 'waiting'
        if not beat or now - beat > HEARTBEAT_STALE_SECONDS:
            out['state'] = 'stuck'
            out['last_error'] = out['last_error'] or 'Host agent launcher has not ticked for ' \
                f'{int((now - beat) // 60) if beat else "many"} min (is the repair timer running?)'
    else:
        out['state'] = 'idle'
    last_failed = failed[-1] if failed else None
    if not out['last_error'] and last_failed:
        out['last_error'] = _error(last_failed)
    out['auth_expired'] = out['last_error'] == AUTH_MESSAGE
    if out['auth_expired'] and not (active and active.get('status') == 'running' and out['state'] == 'running'):
        out['state'] = 'login_expired'
    return out


def compact_summary(data_dir, now=None):
    """Bounded text-friendly summary for the manager's repair_status: state, current job, last 5 jobs, last error."""
    status = agent_status(data_dir, now)
    job = status['job']
    return {'state': status['state'], 'current_job': ({k: job[k] for k in ('id', 'title', 'status', 'attempt')}
                                                       if job else None),
            'started_at': status['started_at'], 'last_heartbeat': status['heartbeat'],
            'last_error': status['last_error'],
            'last_jobs': [{k: r[k] for k in ('id', 'status', 'title', 'result')} for r in status['recent']]}

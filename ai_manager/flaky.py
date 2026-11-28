"""Failed-test bookkeeping for the deploy's full suite. Standard library only (scripts/full_suite.py and scripts/deploy.py use it).

* `failures(xml)` reads pytest's junitxml (`-o junit_family=xunit1`) into [(node id, one-line error)].
* `decide(first, rerun)` is the rerun decision: tests that fail again on the serial rerun are real, the others flaky.
* The journal (<data dir>/flaky-journal.jsonl, never in the repository) is append-only JSON lines: one record per
  failure {time, commit, test, error, kind: flaky|real}, and later {time, test, kind: 'fixed', fix_commit} lines.
* `sync_todos` turns a test recorded flaky REPEAT times within DAYS days into one ready To-Do "Fix flaky test <id>".
"""
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

JOURNAL_NAME = 'flaky-journal.jsonl'
JOURNAL_ENV = 'AI_MANAGER_FLAKY_JOURNAL'  # set by the deploy for the suite it runs; unset = no journal (plain dev runs)
COMMIT_ENV = 'AI_MANAGER_FLAKY_COMMIT'
REPEAT, DAYS = 2, 14
TITLE = 'Fix flaky test '
ERROR_LENGTH = 200


def one_line(text):
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return (lines[0] if lines else 'failed')[:ERROR_LENGTH]


def failures(xml_text):
    """[(node id, one-line error)] of every failed or errored test case; [] for unreadable or empty XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    found = []
    for case in root.iter('testcase'):
        bad = case.find('failure')
        bad = case.find('error') if bad is None else bad
        if bad is None:
            continue
        path, name, classname = case.get('file') or '', case.get('name') or '', case.get('classname') or ''
        if path:
            module = path[:-3].replace('/', '.') if path.endswith('.py') else path
            rest = classname[len(module):].strip('.') if classname.startswith(module) else ''
            node = path + '::' + (rest.replace('.', '::') + '::' if rest else '') + name
        else:
            node = classname + '::' + name
        found.append((node, one_line(bad.get('message') or bad.text)))
    return found


def failure_lines(items, indent='  '):
    return [f'{indent}{node} - {error}' for node, error in items]


def decide(first, rerun):
    """first: the failed [(id, error)] of the parallel phase; rerun: the ones still failing after the serial rerun
    (None when the rerun itself crashed without a report: then nothing is proven flaky). -> (flaky, real) lists."""
    still = dict(rerun) if rerun is not None else dict(first)
    real = [(node, still[node]) for node, _ in first if node in still]
    flaky = [(node, error) for node, error in first if node not in still]
    return flaky, real


def path_of(environ=None):
    value = (os.environ if environ is None else environ).get(JOURNAL_ENV)
    return Path(value) if value else None


def append(path, records):
    if not path or not records:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + '\n')
    os.chmod(path, 0o600)


def record(test, error, kind, commit='', now=None):
    return {'time': now if now is not None else time.time(), 'commit': commit, 'test': test, 'error': error, 'kind': kind}


def read(path):
    out = []
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, TypeError):
        return out
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get('test') and value.get('kind'):
            out.append(value)
    return out


def unfixed_real(records):
    """Tests whose latest record is a real failure with no later fix."""
    state = {}
    for item in sorted(records, key=lambda r: r.get('time', 0)):
        state[item['test']] = item['kind']
    return sorted(test for test, kind in state.items() if kind == 'real')


def mark_fixed(path, commit, now=None):
    """A complete green run at `commit`: every real failure still open was fixed by it."""
    now = now if now is not None else time.time()
    append(path, [{'time': now, 'test': test, 'kind': 'fixed', 'fix_commit': commit} for test in unfixed_real(read(path))])


def top(records, limit=5):
    """The most often flaky tests: [{test, count, last_seen}], most first, ties by latest."""
    seen = {}
    for item in records:
        if item['kind'] == 'flaky':
            entry = seen.setdefault(item['test'], {'test': item['test'], 'count': 0, 'last_seen': 0})
            entry['count'] += 1
            entry['last_seen'] = max(entry['last_seen'], item.get('time', 0))
    return sorted(seen.values(), key=lambda e: (-e['count'], -e['last_seen'], e['test']))[:limit]


def offenders(records, now=None):
    """{test: its flaky records} for tests flaky REPEAT+ times in the last DAYS days."""
    since = (now if now is not None else time.time()) - DAYS * 86400
    grouped = {}
    for item in records:
        if item['kind'] == 'flaky' and item.get('time', 0) >= since:
            grouped.setdefault(item['test'], []).append(item)
    return {test: items for test, items in grouped.items() if len(items) >= REPEAT}


def details(test, items):
    rows = '\n'.join(time.strftime('- %Y-%m-%d %H:%M UTC', time.gmtime(i['time'])) + f" commit {i.get('commit', '')[:10] or '?'}: {i.get('error', '')}"
                     for i in sorted(items, key=lambda r: r['time']))
    return (f'The deploy suite reran `{test}` serially after it failed in the parallel phase and it passed, '
            f'{len(items)} times in {DAYS} days. Find why it is order- or load-dependent and make it deterministic '
            f'(see AGENTS.md "Known pitfalls"). Do not weaken or skip it.\n\nRecords:\n{rows}')


def sync_todos(db, todos, project_id, now=None):
    """Create the missing "Fix flaky test <id>" To-Dos (once per test, archived ones count); returns the new titles."""
    records = read(Path(db.path).parent / JOURNAL_NAME)
    made = []
    for test, items in sorted(offenders(records, now).items()):
        title = (TITLE + test)[:200]
        if db.one('SELECT 1 FROM todos WHERE project_id=? AND title=?', (project_id, title)):
            continue
        todos.save(project_id, title, details(test, items)[:24000], kind='ready')
        made.append(title)
    return made

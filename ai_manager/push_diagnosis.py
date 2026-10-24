"""Safe, structured explanation of a failed GitHub push. Raw Git output is redacted before it leaves this module."""
import re

TAIL_BYTES = 8192
MASK = '***'
# Key-shaped values GitHub push protection typically flags: (label, pattern).
SECRET_PATTERNS = [
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('GitHub token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})')),
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')),
    ('xAI key', re.compile(r'\bxai-[A-Za-z0-9_-]{16,}')),
    ('API key', re.compile(r'\bsk-[A-Za-z0-9_-]{16,}')),
]
PEM_BLOCK = re.compile(r'-----BEGIN [A-Z ]*-----.*?(?:-----END [A-Z ]*-----|\Z)', re.S)
ASSIGNMENT = re.compile(r'(?i)\b([A-Za-z0-9_]*(?:key|token|secret|passw(?:or)?d|credential)[A-Za-z0-9_]*)(\s*[=:]\s*)\S+')
USERINFO = re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@')
LONG_RUN = re.compile(r'[A-Za-z0-9+/=_-]{32,}')
UNBLOCK_URL = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/security/secret-scanning/unblock-secret/\w+')
HEADER = re.compile(r'^\s*[—–-]{2,}\s+(.+?)\s+[—–-]+\s*$')
SHA = re.compile(r'^[0-9a-f]{7,40}$')
MAX_HITS = 20


def redact(text, secrets=(), generic=True):
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    text = PEM_BLOCK.sub(MASK, text)
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    text = USERINFO.sub(r'\1' + MASK + '@', text)
    text = ASSIGNMENT.sub(lambda m: m[1] + m[2] + MASK, text)
    return LONG_RUN.sub(MASK, text) if generic else text


def clean_output(raw, secrets=()):
    """Bounded tail of Git's stderr/stdout with every key-shaped value masked."""
    text = raw[-TAIL_BYTES:].decode('utf-8', 'replace') if isinstance(raw, bytes) else raw[-TAIL_BYTES:]
    return redact(text, secrets, generic=False)


def _lines(output):
    return [re.sub(r'^remote:\s?', '', line).rstrip() for line in output.splitlines()]


def _push_protection(lines):
    secrets, current, commit, urls = {}, None, None, []
    for line in lines:
        text = line.strip().removeprefix('- ')
        header = HEADER.match(line)
        if header:
            current, commit = redact(header[1], generic=False)[:80], None
            secrets.setdefault(current, [])
        elif text.startswith('commit:'):
            candidate = text[7:].strip()
            commit = candidate[:12] if SHA.match(candidate) else None
        elif text.startswith('path:') and current is not None:
            path, _, number = text[5:].strip().rpartition(':')
            if not number.isdigit():
                path, number = text[5:].strip(), None
            location = {'commit': commit, 'path': redact(path, generic=False)[:200],
                        'line': int(number) if number else None}
            if len(secrets[current]) < MAX_HITS:
                secrets[current].append(location)
        for url in UNBLOCK_URL.findall(line):
            if url not in urls:
                urls.append(url)
    return [{'type': kind, 'locations': locations} for kind, locations in secrets.items()], urls[:MAX_HITS]


def _place(location):
    where = location['path'] + (f":{location['line']}" if location['line'] else '')
    return where + (f" (commit {location['commit']})" if location['commit'] else '')


def _push_protection_message(secrets, urls):
    hits = [f"{item['type']} in {_place(loc)}" for item in secrets for loc in item['locations']]
    if not hits:
        hits = [item['type'] for item in secrets]
    text = 'GitHub blocked the push: secret scanning found ' + (', '.join(hits[:5]) or 'a secret')
    if len(hits) > 5:
        text += f' and {len(hits) - 5} more'
    text += '. Fix the file or allow it on GitHub'
    return text + (': ' + urls[0] if urls else '.')


def _excerpt(lines, secrets=()):
    """At most three short lines that survive the strictest redaction unchanged."""
    kept = []
    for line in lines:
        if line.strip() and redact(line, secrets) == line and len(kept) < 3:
            kept.append(line.strip()[:200])
    return kept


CLASSES = [
    ('hook-declined', re.compile(r'(?i)hook declined')),
    ('auth-denied', re.compile(r'(?i)authentication failed|permission denied|could not read (?:username|password)|'
                              r'invalid username or password|requested url returned error: 40[13]|denied to|'
                              r'write access to repository not granted|bad credentials')),
    ('repository-not-found', re.compile(r'(?i)repository not found|does not appear to be a git repository|'
                                       r'requested url returned error: 404')),
    ('network', re.compile(r'(?i)could not resolve host|timed out|connection (?:refused|reset|closed)|'
                           r'network is unreachable|unable to access|operation timed out|early eof')),
]
MESSAGES = {
    'rejected-non-fast-forward': 'GitHub rejected the push: the remote branch has commits this repository lacks (non-fast-forward). The app never force-pushes.',
    'auth-denied': 'GitHub denied access: authentication failed or the token/key lacks write permission.',
    'repository-not-found': 'GitHub repository not found, or the credentials cannot see it.',
    'network': 'The push could not reach GitHub (network error or timeout); retrying automatically.',
    'hook-declined': 'A remote hook declined the push.',
    'unknown': 'The push failed for a reason the application could not classify.',
}


def diagnose(output, secrets=()):
    """Parse Git push/fetch output into {'reason', 'message', ...}. Only redacted, parsed fields are returned."""
    raw = output.decode('utf-8', 'replace') if isinstance(output, bytes) else (output or '')
    lines = _lines(raw[-TAIL_BYTES:])
    clean = redact(raw[-TAIL_BYTES:], secrets, generic=False)
    kept = _excerpt(lines, secrets)
    if 'GH013' in raw or 'Push cannot contain secrets' in raw or re.search(r'(?i)push protection', raw):
        found, urls = _push_protection(lines)
        detail = {'kind': 'push-protection', 'rule': 'GH013', 'secrets': found, 'unblock_urls': urls}
        return {'reason': 'github-push-protection', 'message': _push_protection_message(found, urls),
                'detail': detail, 'excerpt': kept}
    kind = 'unknown'
    if re.search(r'(?i)non-fast-forward|fetch first|\[rejected\]|\(.*behind.*\)', clean):
        kind = 'rejected-non-fast-forward'
    else:
        for name, pattern in CLASSES:
            if pattern.search(clean):
                kind = name
                break
    return {'reason': kind if kind != 'unknown' else 'git-push-or-verification-failed', 'message': MESSAGES[kind],
            'detail': {'kind': kind}, 'excerpt': kept}


def precheck(diff):
    """Cheap local scan of `git log -p -U0` output for literal key-shaped strings; never returns the values."""
    hits, commit, path, line = [], None, None, 0
    for row in diff.decode('utf-8', 'replace').splitlines():
        if row.startswith('@@commit '):
            commit = row[9:21]
        elif row.startswith('+++ '):
            path = row[4:].removeprefix('b/')
        elif row.startswith('@@'):
            match = re.search(r'\+(\d+)', row)
            line = int(match[1]) - 1 if match else 0
        elif row.startswith('+'):
            line += 1
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(row):
                    hits.append({'type': label, 'path': redact(path or '', generic=False)[:200], 'line': line, 'commit': commit})
                    break
            if len(hits) >= MAX_HITS:
                break
    return hits


# Incoming (fetch / merge / ref) failures: same redaction and classes, messages name the failing operation.
OPERATIONS = {'inspect': 'checkout inspection', 'fetch': 'fetch', 'refs': 'ref comparison',
              'merge': 'fast-forward merge', 'deploy': 'deploy request', 'update': 'update'}
INCOMING_CLASSES = [
    ('missing-main', re.compile(r"(?i)couldn't find remote ref|remote ref does not exist")),
    ('missing-ref', re.compile(r'(?i)unknown revision|bad revision|ambiguous argument|not a valid (?:object|ref)|'
                               r'needed a single revision|unborn|not a git repository')),
    ('local-changes', re.compile(r'(?i)would be overwritten|untracked working tree files|local changes')),
    ('local-lock', re.compile(r'(?i)index\.lock|cannot lock ref|unable to create .*\.lock|another git process')),
    ('not-fast-forward', re.compile(r'(?i)not possible to fast-forward|diverging|refusing to merge')),
]
INCOMING_MESSAGES = {
    'auth-denied': 'GitHub denied access: authentication failed or the token/key lacks read permission.',
    'repository-not-found': 'GitHub repository not found, or the credentials cannot see it.',
    'missing-main': "The remote has no 'main' branch to fetch.",
    'network': 'Could not reach GitHub (DNS, network error or timeout).',
    'missing-ref': 'A local or remote-tracking main ref is missing or unreadable.',
    'local-changes': 'Local files would be overwritten by the update; they were left untouched.',
    'local-lock': 'Another Git process holds a lock in this checkout.',
    'not-fast-forward': 'Local main cannot be fast-forwarded to origin/main.',
}


def diagnose_incoming(operation, output, secrets=(), attempts=1):
    """Classify a failed incoming Git command: {'cause', 'message', 'excerpt'}; only redacted, bounded text."""
    raw = output.decode('utf-8', 'replace') if isinstance(output, bytes) else (output or '')
    tail = raw[-TAIL_BYTES:]
    clean = redact(tail, secrets, generic=False)
    excerpt = _excerpt(_lines(tail), secrets)
    classes = ([c for c in CLASSES if c[0] in {'auth-denied', 'repository-not-found', 'network'}]
               + INCOMING_CLASSES) if operation == 'fetch' else INCOMING_CLASSES
    cause = next((name for name, pattern in classes if pattern.search(clean)), 'unknown')
    label = OPERATIONS.get(operation, operation)
    after = f' after {attempts} attempts' if attempts > 1 else ''
    if cause == 'unknown':
        detail = ' | '.join(excerpt)
        message = f'GitHub {label} failed{after}' + (f': {detail}' if detail else ' for a reason the application could not classify.')
    else:
        message = f'GitHub {label} failed{after}: {INCOMING_MESSAGES[cause]}'
    return {'cause': cause, 'message': message[:400], 'excerpt': excerpt}

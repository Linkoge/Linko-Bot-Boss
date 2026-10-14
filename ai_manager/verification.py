"""Deterministic risk-tiered worker verification. Contract: docs/VERIFICATION_TIERS.md.

No AI routing call: the backend derives a floor from declared change paths, request text and
assignment kind. The manager may raise the tier, never lower it. After a worker finishes, the
actual branch diff is classified the same way, so mis-declared paths cannot keep risky work Small.
"""
import re
import subprocess
from pathlib import PurePosixPath

TIERS = ('small', 'normal', 'risky')
VERSION = 2
SMALL_MAX_FILES = 3
DIFF_LIMIT = 60000

DOC = {'.md', '.markdown', '.txt', '.rst', '.adoc'}
ASSET = {'.css', '.scss', '.sass', '.less', '.html', '.htm', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp',
         '.ico', '.woff', '.woff2', '.webmanifest'}
UI_SCRIPT = {'.js', '.mjs', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}
# Dependency, environment and infrastructure manifests are risky regardless of suffix.
RISKY_NAMES = {'pyproject.toml', 'uv.lock', 'requirements.txt', 'package.json', 'package-lock.json', 'yarn.lock',
               'pnpm-lock.yaml', 'poetry.lock', 'pipfile', 'pipfile.lock', 'dockerfile', 'docker-compose.yml',
               'docker-compose.yaml', '.env', 'cargo.toml', 'cargo.lock', 'go.mod', 'go.sum'}
RISKY_SUFFIXES = {'.sql', '.sqlite', '.sqlite3', '.db', '.service', '.timer', '.socket'}
RISKY_DIRS = {'deploy', 'deployment', 'migrations', 'migration', 'alembic', 'auth', 'db', 'database', 'security',
              '.github', 'infra', 'systemd'}
# Words of a code file's name: DB, auth, deploy, worker, manager and recovery logic.
RISKY_WORDS = {'db', 'database', 'sqlite', 'sql', 'schema', 'migration', 'migrations', 'migrate', 'auth', 'login',
               'logout', 'session', 'sessions', 'security', 'csrf', 'password', 'passwords', 'secret', 'secrets',
               'credential', 'credentials', 'permission', 'permissions', 'deploy', 'deployment', 'release', 'rollback',
               'backup', 'restore', 'worker', 'workers', 'manager', 'orchestration', 'orchestrator', 'scheduler',
               'recovery', 'lease', 'assignment', 'parallel', 'provider', 'providers', 'codex', 'claude', 'tasks',
               'server', 'service', 'api'}
# Backend entry points/configuration (app.py, web.py, settings.py) but not UI scripts such as app.js.
BACKEND_WORDS = {'app', 'main', 'web', 'cli', 'config', 'settings', 'chat', 'routes', 'middleware', 'wsgi', 'asgi'}
RISKY_TEXT = re.compile(
    r'\b(databases?|sqlite|schemas?|migrations?|migrate|sql|auth|authentication|authorization|authz|log ?ins?|'
    r'logouts?|passwords?|sessions?|csrf|credentials?|secrets?|encrypt\w*|security|permissions?|rollbacks?|'
    r'backups?|restores?|systemd|leases?)\b', re.I)

PLANS = {
    'small': {
        'checks': ['Smoke suite once (here: python -m pytest -q -m smoke)',
                   'Only tests covering the touched files (find via test names/imports)',
                   'Build/lint of touched files (e.g. ruff check, node --check)'],
        'skip': ['Full suites for Small-only releases', 'Reviewer'],
    },
    'normal': {
        'checks': ['Smoke suite once', 'Targeted tests while iterating', 'Build/lint'],
        'skip': ['End-of-task full backend run: Normal/Risky release preparation runs it once', 'Reviewer'],
    },
    'risky': {
        'checks': ['Smoke suite once', 'Targeted tests while iterating', 'Build/lint',
                   'Browser/acceptance scripts covering touched behavior'],
        'skip': ['End-of-task full backend run: Normal/Risky release preparation runs it once',
                 'Self-review passes: automatic independent diff review only for sensitive actual changes'],
    },
}
ESCALATE = ('If actual changes touch risky areas (DB/schema, auth/session, deploy/release/backup, worker/manager/'
            'recovery logic) or exceed declared change_paths, apply the higher tier checks. Never lower the tier.')
REPORT = ('Final reply at most 12 short lines: outcome, files changed, commit, checks run with pass/fail counts, '
          'limitations/pending. Do not paste logs; commands and outputs are retained in the execution record, '
          'which is the detailed evidence together with updated authoritative docs. Correct substantive findings/failures and '
          'rerun affected checks after fixes; full backend runs once at Normal/Risky release preparation.')
REVIEW_INSTRUCTIONS = """Review one assignment diff read-only. Never modify files, run mutating commands or ask questions.
Report only concrete defects: correctness, security, data loss, concurrency or missed acceptance criteria,
each with file:line and a one-line reason. Reply exactly 'No blocking findings.' when there are none. At most 200 words.
"""


def rank(tier):
    return TIERS.index(tier)


def kind(path):
    name = path.name.lower()
    if any(p in {'tests', 'test', '__tests__'} for p in path.parts[:-1]) or re.match(
            r'^test_.+\.py$|.+_test\.\w+$|.+\.(test|spec)\.\w+$', name):
        return 'test'
    if path.suffix.lower() in DOC:
        return 'doc'
    if path.suffix.lower() in ASSET:
        return 'asset'
    if path.suffix.lower() in UI_SCRIPT:
        return 'ui_script'
    return 'code'


def path_risk(value):
    """Reason a declared/changed path is risky, else ''."""
    path = PurePosixPath(value)
    name, category = path.name.lower(), kind(path)
    if name in RISKY_NAMES or path.suffix.lower() in RISKY_SUFFIXES:
        return 'risky file ' + value
    if category in {'doc', 'test'}:
        return ''  # Text and tests never change runtime behavior by themselves.
    if any(part.lower() in RISKY_DIRS for part in path.parts[:-1]):
        return 'risky directory ' + value
    words = set(re.split(r'[^a-z0-9]+', name.rsplit('.', 1)[0] if '.' in name else name))
    if words & RISKY_WORDS or (category == 'code' and words & BACKEND_WORDS):
        return 'risky module ' + value
    return ''


def floor(paths, text='', integration=False, deployment=False):
    """Deterministic minimum tier and reasons. Unknown scope is at least Normal."""
    reasons = [r for r in map(path_risk, paths) if r]
    if integration:
        reasons.append('serialized integration/combined validation')
    if deployment:
        reasons.append('deployment preparation/audit')
    reasons.extend(sorted({'request mentions ' + m.lower() for m in RISKY_TEXT.findall(text or '')}))
    if reasons:
        return 'risky', reasons
    files = [PurePosixPath(p) for p in paths]
    kinds = [kind(p) for p in files]
    if not files:
        return 'normal', ['no concrete change_paths']
    if any(not p.suffix for p in files):
        return 'normal', ['directory or unknown scope']
    if len(files) > SMALL_MAX_FILES:
        return 'normal', [f'more than {SMALL_MAX_FILES} files']
    if any(k == 'code' for k in kinds):
        return 'normal', ['backend/configuration code']
    if sum(k in {'ui_script', 'test'} for k in kinds) > 1:
        return 'normal', ['more than one script/test file']
    return 'small', ['UI/text change of at most ' + str(SMALL_MAX_FILES) + ' files']


def select(paths, objective='', acceptance='', requested=None, integration=False, deployment=False, previous=None):
    if requested is not None and requested not in TIERS:
        raise ValueError('Verification tier must be small, normal or risky')
    tier, reasons = floor(paths, objective + '\n' + acceptance, integration, deployment)
    for higher, why in ((requested, 'manager raised tier'), ((previous or {}).get('tier'), 'tier never lowers')):
        if higher and rank(higher) > rank(tier):
            tier, reasons = higher, reasons + [why]
    if requested and rank(requested) < rank(tier):
        reasons.append(f'requested {requested} not allowed')
    return {'version': VERSION, 'tier': tier, 'requested': requested, 'reasons': reasons[:12]}


def plan(record, integration=False):
    tier = 'risky' if integration else record['tier']
    return {'tier': tier, 'reasons': record['reasons'], **PLANS[tier], 'escalate': ESCALATE, 'report': REPORT}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('git ' + args[0] + ' failed: ' + result.stderr.strip()[:300])
    return result.stdout


def changes(root, base):
    """Actual paths and full diff for classification; only the AI review input is bounded."""
    paths = set(git(root, 'diff', '--name-only', base).splitlines())
    paths |= set(git(root, 'ls-files', '--others', '--exclude-standard').splitlines())
    diff = git(root, 'diff', '--stat', base) + '\n' + git(root, 'diff', base)
    return sorted(p for p in paths if p), diff, len(diff) > DIFF_LIMIT


# Review selection is narrower than tier classification. Ordinary manager/backend names
# still raise the verification floor but do not alone purchase an independent AI review.
REVIEW_WORDS = {'auth', 'authentication', 'authorization', 'login', 'logout', 'session', 'sessions',
                'csrf', 'security', 'secret', 'secrets', 'password', 'passwords', 'permission', 'permissions',
                'credential', 'credentials', 'encrypt', 'encryption', 'crypto', 'authz',
                'db', 'database', 'schema', 'migration', 'migrations', 'migrate', 'deploy', 'deployment',
                'release', 'rollback', 'backup', 'restore', 'worker', 'workers', 'parallel', 'recovery',
                'lease', 'scheduler', 'tasks', 'orchestration', 'assignment'}
REVIEW_TEXT = re.compile(r'\b(auth\w*|security|encrypt\w*|crypto\w*|permissions?|csrf|sessions?|credentials?|secrets?|passwords?|'
                         r'migrations?|schemas?|deploy\w*|rollback|backup|restore|concurren\w*|'
                         r'recovery|leases?)\b', re.I)


def review_reasons(paths, text='', diff=''):
    reasons = []
    for value in paths:
        path = PurePosixPath(value)
        if kind(path) in {'doc', 'test'}:
            continue
        words = set(re.split(r'[^a-z0-9]+', path.stem.lower())) | {p.lower() for p in path.parts[:-1]}
        direct = REVIEW_WORDS - {'db', 'database', 'worker', 'workers', 'parallel', 'recovery',
                                 'lease', 'scheduler', 'tasks', 'orchestration', 'assignment'}
        blocks = diff.split('diff --git ')
        scoped = ''.join(block for block in blocks[1:] if block.splitlines()[0].endswith(' b/' + value))
        source = scoped if len(blocks) > 1 else diff
        changed = '\n'.join(line[1:] for line in source.splitlines()
                            if line.startswith(('+', '-')) and not line.startswith(('+++', '---')))
        behavioral = re.search(r'\b(schema|migrat\w*|alter|create|lease\w*|concurren\w*|lock\w*|'
                               r'recover\w*|claim\w*|heartbeat|parallel|cancel\w*|resume\w*|await|async|asyncio|gather|semaphore|queue|transaction|retry|reconnect|executor|thread|spawn|stop_requested)\b', changed, re.I)
        if (words & direct or path.suffix.lower() in RISKY_SUFFIXES
                or (words & REVIEW_WORDS and behavioral)):
            reasons.append('sensitive actual path ' + value)
    if any(kind(PurePosixPath(p)) not in {'doc', 'test'} for p in paths) and REVIEW_TEXT.search(text):
        reasons.append('sensitive assignment behavior')
    return reasons


BROWSERS = ('model_scroll', 'composer', 'parallel_composer', 'worker_delivery', 'audio',
            'chat_reliability', 'project_ui', 'project_stash', 'global_recency', 'agent_controls',
            'todos', 'todo_chat', 'project_memory', 'fetch_recovery', 'smoke', 'markdown',
            'deployment', 'account_usage', 'auto_deploy', 'pwa')
# Shared app/style/HTTP/auth/DB primitives affect all browser behavior. Feature modules
# select only their consumers. Unknown runtime modules conservatively use shared coverage.
BROWSER_FEATURES = {
    'model': ('model_scroll',), 'usage': ('account_usage',), 'audio': ('audio', 'composer'),
    'attachment': ('audio', 'composer'), 'memory': ('project_memory',),
    'registry': ('project_ui', 'project_stash'), 'project': ('project_ui', 'project_stash'),
    'stash': ('project_stash',), 'recency': ('global_recency',), 'markdown': ('markdown',),
    'todo': ('todos', 'todo_chat'), 'pwa': ('pwa',), 'manifest': ('pwa',),
    'deploy': ('deployment', 'auto_deploy'), 'release': ('deployment', 'auto_deploy'),
    'verification': ('worker_delivery', 'agent_controls', 'auto_deploy', 'deployment'),
    'worker': ('worker_delivery', 'agent_controls', 'parallel_composer', 'auto_deploy'),
    'task': ('worker_delivery', 'agent_controls', 'parallel_composer', 'todos', 'auto_deploy'),
    'assignment': ('worker_delivery', 'agent_controls', 'parallel_composer', 'auto_deploy'),
    'parallel': ('parallel_composer', 'worker_delivery', 'auto_deploy'),
    'recovery': ('fetch_recovery', 'chat_reliability', 'worker_delivery'),
    'orchestration': ('worker_delivery', 'agent_controls', 'parallel_composer', 'todos', 'auto_deploy'),
    'codex': ('worker_delivery', 'agent_controls', 'parallel_composer', 'auto_deploy', 'model_scroll'),
    'chat': ('chat_reliability', 'composer', 'todo_chat', 'worker_delivery', 'markdown'),
}


def browser_selection(paths):
    selected, reasons = set(), {}
    for value in paths:
        path = PurePosixPath(value)
        if kind(path) in {'doc', 'test'}:
            continue
        if path.parts and path.parts[0] == '.data':
            continue  # Retained reports/audits are evidence, not served runtime behavior.
        name = path.stem.lower()
        if name.startswith('browser_') and name[8:] in BROWSERS:
            relevant = (name[8:],)
            why = 'changed browser check'
        else:
            features = [k for k in BROWSER_FEATURES if k in name]
            relevant = tuple(b for k in features for b in BROWSER_FEATURES[k]) if features else BROWSERS
            why = 'feature consumers' if features else 'shared or unknown runtime behavior'
        for browser in relevant:
            selected.add(browser)
            reasons.setdefault(browser, []).append(value + ': ' + why)
    return [b for b in BROWSERS if b in selected], reasons


def release_policy(paths, records=()):
    actual = floor(paths)[0] if paths else 'small'
    tier = max([actual] + [r['tier'] for r in records if r], key=rank)
    browsers, coverage = browser_selection(paths)
    if tier == 'small' and len(browsers) == len(BROWSERS):
        # Existing representative acceptance covers shared visual Small behavior.
        browsers = ['composer', 'project_ui', 'smoke']
        coverage = {b: coverage[b] for b in browsers}
    return {'version': VERSION, 'tier': tier, 'actual': actual, 'changed_files': sorted(paths),
            'full_backend': tier != 'small', 'browsers': browsers, 'coverage': coverage,
            'skipped_browsers': [b for b in BROWSERS if b not in browsers],
            'skipped': ['unrelated browser scripts'] + (['full backend (Small-only)'] if tier == 'small' else [])}

"""Deterministic risk-tiered worker verification. Contract: docs/VERIFICATION_TIERS.md.

No AI routing call: the backend derives a floor from declared change paths, request text and
assignment kind. The manager may raise the tier, never lower it. After a worker finishes, the
actual branch diff is classified the same way, so mis-declared paths cannot keep risky work Small.
"""
import re
import subprocess
from pathlib import PurePosixPath

TIERS = ('small', 'normal', 'risky')
VERSION = 1
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
        'skip': ['Full suite: release preparation runs it once', 'Reviewer'],
    },
    'normal': {
        'checks': ['Smoke suite once', 'Targeted tests while iterating', 'Build/lint',
                   'Full backend suite exactly once at the end, after final edits'],
        'skip': ['Repeated full-suite runs', 'Reviewer'],
    },
    'risky': {
        'checks': ['Smoke suite once', 'Targeted tests while iterating', 'Build/lint',
                   'Browser/acceptance scripts covering touched behavior',
                   'Full backend suite exactly once at the end, after final edits'],
        'skip': ['Repeated full-suite runs',
                 'Self-review passes: one cheap read-only diff review runs automatically after you finish'],
    },
}
ESCALATE = ('If actual changes touch risky areas (DB/schema, auth/session, deploy/release/backup, worker/manager/'
            'recovery logic) or exceed declared change_paths, apply the higher tier checks. Never lower the tier.')
REPORT = ('Final reply at most 12 short lines: outcome, files changed, commit, checks run with pass/fail counts, '
          'limitations/pending. Do not paste logs; commands and outputs are retained in the execution record, '
          'which is the detailed evidence together with updated authoritative docs.')
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
    """Actual changed paths and a bounded diff of the assignment branch versus its base."""
    paths = set(git(root, 'diff', '--name-only', base).splitlines())
    paths |= set(git(root, 'ls-files', '--others', '--exclude-standard').splitlines())
    diff = git(root, 'diff', '--stat', base) + '\n' + git(root, 'diff', base)
    return sorted(p for p in paths if p), diff[:DIFF_LIMIT], len(diff) > DIFF_LIMIT

"""Deterministic risk-tiered worker verification. Contract: docs/VERIFICATION_TIERS.md.

No AI routing call: the backend derives a floor from declared change paths, request text and
assignment kind. The manager may raise the tier, never lower it. After a worker finishes, the
actual branch diff is classified the same way, so mis-declared paths cannot keep risky work Small.
"""
import ast
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
    untracked = set(git(root, 'ls-files', '--others', '--exclude-standard').splitlines())
    paths |= untracked
    diff = git(root, 'diff', '--stat', base) + '\n' + git(root, 'diff', base)
    for value in sorted(untracked):
        result = subprocess.run(['git', 'diff', '--no-index', '--', '/dev/null', value], cwd=root,
                                capture_output=True, text=True, timeout=60)
        if result.returncode not in (0, 1):
            raise ValueError('Cannot inspect untracked diff: ' + value)
        diff += '\n' + result.stdout
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



# Explicit operations/identifiers, not generic backend vocabulary such as async,
# await, server or manager. Quoted display text and comments are not code signals.
DIFF_AUTH = re.compile(r'\b(auth|authentication|authorization|authenticate|authorize|authorized|authz|login|logout|signin|signout|csrf|passwords?|credentials?|secrets?|permissions?|'
                       r'sessions?|cookies?|jwt|bcrypt|token hash|access token|refresh token|bearer|allowed|require owner|compare digest|encrypt\w*|crypto\w*)\b', re.I)
DIFF_MIGRATION = re.compile(r'\b(schema|migrat\w*)\b', re.I)
DIFF_DEPLOYMENT = re.compile(r'\b(deploy\w*|rollback|backup|restore|snapshot|systemctl)\b', re.I)
DIFF_CONCURRENCY = re.compile(
    r'\b(lease\w*|heartbeat|recover\w*|claim\w*|stop requested|retry at|lease owner)\b|'
    r'\b(start|stop|pause|resume|cancel|spawn|restart) (worker|assignment)\b|'
    r'\b(asyncio|threading|multiprocessing|concurrent)\s*\.\s*'
    r'(create task|gather|wait|wait for|task group|lock|semaphore|queue|thread|process|futures)\b|'
    r'\b(acquire|release|gather|create task|task group|lock|semaphore)\s*\(|\b(lock|semaphore|queue)\s*\.\s*'
    r'(acquire|release|put|get|join)\b|\b(process|worker|workers)\s*\.\s*'
    r'(terminate|kill|start|stop|pause|resume|cancel)\b', re.I)
QUOTED = re.compile(r"'(?:(?:\\.)|[^'\\])*'|\"(?:(?:\\.)|[^\"\\])*\"")



ROLE_KEYS = {'role', 'roles', 'permission', 'permissions', 'scope', 'scopes', 'privilege', 'privileges',
             'is_admin', 'is_owner', 'is_superuser'}
CHAT_ROLES = {'user', 'assistant', 'system', 'developer', 'tool', 'function'}



PRINCIPAL_NAMES = {'user', 'principal', 'actor', 'account', 'identity', 'subject', 'requester'}
OWNERSHIP_NAMES = {'owner', 'author', 'creator', 'created_by', 'owned_by', 'user', 'account',
                   'tenant', 'organization', 'org', 'group'}
AUTH_STATE = {'admin', 'administrator', 'staff', 'superuser', 'owner', 'authenticated', 'authorized',
              'permission', 'permissions', 'perm', 'perms', 'role', 'roles', 'privilege', 'privileges',
              'scope', 'scopes', 'access'}
AUTH_ACTIONS = {'access', 'read', 'write', 'edit', 'delete', 'manage', 'view', 'create', 'share', 'execute'}
DISPLAY_OBJECTS = {'label', 'labels', 'display', 'usage', 'metadata', 'message'}


def authorization_flag(name):
    words = name.split('_')
    return (name in AUTH_STATE - {'owner', 'access'} or name in {'owns', 'owned_by'}
            or words[0] in {'is', 'has', 'check', 'require', 'verify', 'get'} and bool(set(words[1:]) & AUTH_STATE)
            or words[0] in {'can', 'may', 'allows', 'permits'} and bool(set(words[1:]) & AUTH_ACTIONS))


def identifier_name(value):
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', value).lower()


def expression_names(node):
    """Member/key names carry identity semantics; arbitrary string values do not."""
    names = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            names.add(identifier_name(part.id))
        elif isinstance(part, ast.Attribute):
            names.add(identifier_name(part.attr))
        elif isinstance(part, ast.Subscript) and isinstance(part.slice, ast.Constant) and isinstance(part.slice.value, str):
            names.add(identifier_name(part.slice.value))
        elif isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) and part.func.attr == 'get':
            if part.args and isinstance(part.args[0], ast.Constant) and isinstance(part.args[0].value, str):
                names.add(identifier_name(part.args[0].value))
    return names




def principal_reference(names):
    return any(name in PRINCIPAL_NAMES or name.removesuffix('_id') in PRINCIPAL_NAMES
               or name.startswith('current_') and name[8:] in PRINCIPAL_NAMES for name in names)

def authorization_predicate(line):
    """Recognize access decisions/flag mutations by AST structure, not ordinary metadata words."""
    source = line.strip()
    if source.startswith(('elif ', 'while ')):
        source = 'if ' + source.split(' ', 1)[1]
    if source.startswith('if ') and source.endswith(':'):
        source += '\n pass'
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False  # Partial/non-Python hunks still use the existing literal/operation rules.
    decisions, writes = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            decisions.append(node.test)
        elif isinstance(node, ast.Return) and node.value is not None:
            decisions.append(node.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            writes.extend(targets)
            # e.g. allowed = user.id == resource.owner_id also changes an access decision.
            if isinstance(node.value, (ast.Compare, ast.BoolOp, ast.UnaryOp, ast.Call, ast.Attribute)):
                decisions.append(node.value)
        elif isinstance(node, ast.Expr) and isinstance(node.value, (ast.Compare, ast.BoolOp, ast.Call)):
            decisions.append(node.value)
    for target in writes:
        if isinstance(target, (ast.Attribute, ast.Subscript)) or (isinstance(target, ast.Name)
                and identifier_name(target.id).startswith(('is_', 'has_', 'can_', 'may_'))):
            names = expression_names(target)
            if any(authorization_flag(name) for name in names):
                # Rendering/provider metadata objects are not authorization state.
                if not names & DISPLAY_OBJECTS or principal_reference(names):
                    return True
    for decision in decisions:
        names = expression_names(decision)
        if names & DISPLAY_OBJECTS and not principal_reference(names):
            continue
        if any(authorization_flag(name) for name in names):
            return True
        for comparison in (n for n in ast.walk(decision) if isinstance(n, ast.Compare)):
            # Both operands/relations must participate: merely reading an author,
            # rendering owner labels or comparing unrelated IDs is not an access guard.
            relation = expression_names(comparison)
            principal = principal_reference(relation)
            ownership = any(name in {'owned_by', 'created_by'} or name.endswith('_id')
                            and name[:-3] in OWNERSHIP_NAMES for name in relation)
            ownership |= bool(relation & {'owner', 'creator'} and principal)
            identity = bool(relation & {'id', 'pk', 'uuid'} or any(name.endswith('_id') for name in relation))
            if (principal and identity) or ownership and (principal or identity):
                return True
    return False

def authorization_literals(line):
    """Keep authorization keys in access/control expressions, not arbitrary display strings."""
    quoted = list(QUOTED.finditer(line))
    values = []
    for match in quoted:
        try:
            value = ast.literal_eval(match.group())
        except (ValueError, SyntaxError):
            value = ''
        values.append(value.lower() if isinstance(value, str) else '')
    stripped = QUOTED.sub(' ', line)
    principal = bool(re.search(r'\b(user|principal|account|identity|claims|current_user)\b', stripped))
    # Provider message role metadata is distinct from a principal's access role.
    chat_metadata = (not principal and bool(re.search(r'\b(message|messages|chat|provider_message)\b', stripped))
                     and any(value in CHAT_ROLES for value in values))
    guard = bool(re.search(r'\b(if|elif|assert|while|return)\b|(?:==|!=|\bnot in\b|\bin\b)', stripped))
    role_identifier = bool(re.search(r'\b(role|roles|is_admin|is_owner|is_superuser)\b', stripped))
    sensitive = ((guard or principal) and role_identifier and not chat_metadata) or authorization_predicate(line)
    for match, value in zip(quoted, values, strict=True):
        prefix = line[:match.start()]
        access = re.search(r'(?:\[\s*|\.get\(\s*|getattr\([^,]+,\s*)$', prefix)
        display = not principal and re.search(r'\b(labels?|display|usage|metadata)\s*(?:\[|\.get\()\s*$', prefix)
        if not display and value in ROLE_KEYS and access and (value not in {'role', 'roles'} or not chat_metadata):
            sensitive = True
    # Metadata permission/role properties do not turn display-only writes into access decisions.
    if not principal:
        stripped = re.sub(r'\b(label|labels|display|usage|metadata|message)\.(role|roles|permissions?|privileges?|scopes?)\b(?!\s*\()',
                          r'\1.display_value', stripped)
    # Preserve a signal rather than injecting all quoted contents into identifier rules.
    return stripped + (' permission' if sensitive else '')

def code_signals(lines):
    """Deterministic sensitive-operation categories for changed code / enclosing hunk scope."""
    code, literals = [], []
    for line in lines:
        if line.lstrip().startswith(('#', '//', '/*', '*')):
            continue
        masked = QUOTED.sub(lambda match: ' ' * len(match.group()), line)
        ends = [masked.find(marker) for marker in ('#', '//') if marker in masked]
        if ends:
            line = line[:min(ends)]
        literals.extend(QUOTED.findall(line))
        line = authorization_literals(line)
        # Normalize snake_case and camelCase identifiers while retaining operation punctuation.
        line = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', line).replace('_', ' ')
        code.append(line)
    identifiers = '\n'.join(code)
    strings = '\n'.join(literals)
    categories = [name for name, pattern in (('auth/security', DIFF_AUTH),
                  ('database migration', DIFF_MIGRATION), ('deployment/rollback', DIFF_DEPLOYMENT),
                  ('worker concurrency/recovery', DIFF_CONCURRENCY)) if pattern.search(identifiers)]
    # SQL and HTTP/command declarations carry behavior in strings, not identifiers.
    if re.search(r'\b(create|alter|drop)\s+(table|index|trigger|view)\b', strings, re.I) and re.search(
            r'\b(execute|executescript|sql|op)\b', identifiers, re.I):
        categories.append('database migration')
    if (re.search(r'[\"\'](?:authorization|cookie|set-cookie|x-csrf-token)[\"\']', strings, re.I)
            and re.search(r'\b(headers?|cookies?|request|response|csrf)\b', identifiers, re.I)):
        categories.append('auth/security')
    if re.search(r'\bsystemctl\b|\bgit\b.*\b(checkout|reset)\b', strings, re.I):
        categories.append('deployment/rollback')
    return sorted(set(categories))


def review_reasons(paths, text='', diff=''):
    reasons = []
    blocks = diff.split('diff --git ')
    for value in paths:
        path = PurePosixPath(value)
        if kind(path) in {'doc', 'test'} or (path.parts and path.parts[0] == '.data'):
            continue
        words = set(re.split(r'[^a-z0-9]+', path.stem.lower())) | {p.lower() for p in path.parts[:-1]}
        direct = REVIEW_WORDS - {'db', 'database', 'worker', 'workers', 'parallel', 'recovery',
                                 'lease', 'scheduler', 'tasks', 'orchestration', 'assignment'}
        scoped = ''.join(block for block in blocks[1:] if block.splitlines()[0].endswith(' b/' + value))
        source = scoped if len(blocks) > 1 else diff
        if words & direct or path.suffix.lower() in RISKY_SUFFIXES:
            reasons.append('sensitive actual path ' + value)
            continue
        # Added AND removed code matter. A removed permission gate is still security
        # work. Enclosing function context catches e.g. `return True` in require_owner.
        # Inspect each hunk separately so another unchanged sensitive function in the
        # file cannot make an ordinary edit buy a blanket review.
        hunks = re.split(r'(?m)^@@[^\n]*@@', source)
        headers = re.findall(r'(?m)^@@[^\n]*@@([^\n]*)', source)
        for index, hunk in enumerate(hunks):
            changed = [line[1:] for line in hunk.splitlines() if line.startswith(('+', '-'))
                       and not line.startswith(('+++', '---'))]
            if not changed or not any(line.strip() and not line.lstrip().startswith(('#', '//', '/*', '*'))
                                      for line in changed):
                continue
            scope = [headers[index - 1]] if index else []
            # A literal-only edit may be inside a continued role comparison.
            # Bind it to the nearest context expression, not every nearby guard.
            lines = hunk.splitlines()
            for position, line in enumerate(lines):
                if line.startswith(('+', '-')) and QUOTED.search(line[1:]) and not re.search(
                        r'[a-zA-Z_]', QUOTED.sub(' ', line[1:])):
                    previous = next((v[1:] for v in reversed(lines[:position]) if v.startswith(' ')), '')
                    if previous.rstrip().endswith(('(', '[', '{')):
                        scope.append(previous)
            # Context matters for changed boolean/status/control decisions in an
            # existing guard. Display literals do not inherit nearby guards.
            effects = QUOTED.sub(' ', '\n'.join(changed))
            if re.search(r'\breturn\s+(True|False|None|[0-9]+)|\bstatus(?:_code)?\s*=|'
                         r'\b(if|elif|raise|assert|while)\b', effects):
                scope += [line[1:] for line in hunk.splitlines() if line.startswith(' ')]
            for category in code_signals(changed + scope):
                reason = 'sensitive ' + category + ' diff ' + value
                if reason not in reasons:
                    reasons.append(reason)
    if any(kind(PurePosixPath(p)) not in {'doc', 'test'} and PurePosixPath(p).parts[0] != '.data'
           for p in paths) and REVIEW_TEXT.search(text):
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

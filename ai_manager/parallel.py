"""Per-request consent and deterministic workspace admission; no inference/router."""
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

INTEGRATION = 'assignment_integration'


def save(conn, key, value):
    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, json.dumps(value)))


def load(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def policy(db, params, args, project_id):
    permission = db.setting('execution_parallel:' + str(params.get('manager_execution_id')), {})
    quote = args.get('owner_parallel_quote', '')
    if quote:
        execution = db.one('SELECT conversation_id,project_id,mode FROM executions WHERE id=?', (params['manager_execution_id'],))
        source = db.one("SELECT content FROM messages WHERE execution_id=? AND role='user'", (params['manager_execution_id'],))
        if (params.get('internal') or not execution or execution['project_id'] not in {None, project_id}
                or execution['mode'] == 'conversation' or execution['conversation_id'] != params['conversation_id']
                or not source or quote not in source['content'] or len(quote) < 8
                or quote.strip(' \t\r\n.!?') not in {sentence.strip(' \t\r\n.!?') for sentence in re.findall(r'[^.!?\n]+[.!?]?', source['content'])}
                or not re.search(r'\bparallel\b', quote, re.I)
                or not re.search(r'\b(run|execute|implement|allow|work|do|proceed|start|enable|approved|authorized|permitted)\b', quote, re.I)
                or re.search(r'\b(no|not|never|don.t|without|unless|if|could|can|should)\b|\?', quote, re.I)):
            raise ValueError('Parallel permission needs explicit instruction quoted from this owner request')
        permission = {'source': 'chat', 'quote': quote, 'execution_id': params['manager_execution_id']}
    if params.get('internal'):
        permission = {}
    paths = args.get('change_paths', [])
    if not isinstance(paths, list) or len(paths) > 64:
        raise ValueError('Provide at most 64 relative change paths')
    for path in paths:
        if (not isinstance(path, str) or not path or len(path) > 300 or '\\' in path or '\x00' in path
                or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                or any(c in path for c in '*?[')):
            raise ValueError('Change paths must be concrete relative files/directories')
    depends = args.get('depends_on', [])
    if not isinstance(depends, list) or len(depends) > 64:
        raise ValueError('Provide at most 64 dependency assignment IDs')
    for task_id in depends:
        task = db.one('SELECT project_id FROM tasks WHERE id=?', (task_id,))
        if not task or task['project_id'] != project_id:
            raise ValueError('Dependency belongs to another project or does not exist')
    return {'permission': permission, 'parallel_safe': args.get('parallel_safe') is True,
            'reason': args.get('parallel_reason', '')[:2000], 'change_paths': paths, 'depends_on': depends}


def overlaps(left, right):
    return any(a == b or a in b.parents or b in a.parents
               for a in map(PurePosixPath, left) for b in map(PurePosixPath, right))


def admission(conn, task):
    if active_workers(conn, task.get('lease_owner') if isinstance(task, dict) else task['lease_owner']):
        return 'Single sequential execution; waiting for the active assignment'
    p = load(conn, 'assignment_policy:' + task['id'], {})
    integration = load(conn, INTEGRATION)
    if integration and integration['task_id'] != task['id']:
        if p.get('integration') or integration['project_id'] == task['project_id']:
            return 'Queued while serialized integration/combined validation owns the project'
    if p.get('integration'):
        if not integration or integration['task_id'] != task['id']:
            return 'Integration reservation needs an explicit serialized continuation'
        if conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND lease_owner IS NOT NULL AND id!=?',
                        (task['project_id'], task['id'])).fetchone():
            return 'Integration waits for all owned project writers to stop'
        return ''
    for dependency in p.get('depends_on', []):
        row = conn.execute('SELECT lifecycle,lease_owner FROM tasks WHERE id=?', (dependency,)).fetchone()
        if not row or row['lifecycle'] != 'completed' or row['lease_owner']:
            return 'Queued for unfinished dependency ' + dependency
        # Completion alone does not put its source in this assignment base.
        dep = conn.execute('SELECT workspace_branch FROM tasks WHERE id=?', (dependency,)).fetchone()
        if dep['workspace_branch'] and (load(conn, 'assignment_source:' + dependency, {}) or {}).get('state') != 'integrated':
            return 'Dependency source requires sequential integration before new coding'
    busy = conn.execute('SELECT * FROM tasks WHERE project_id=? AND id!=? AND lease_owner IS NOT NULL',
                        (task['project_id'], task['id'])).fetchall()
    # FIFO ordinary requests cannot overtake an earlier queued ordinary request.
    earlier = conn.execute("SELECT id FROM tasks WHERE project_id=? AND lifecycle='queued' AND (created_at<? OR (created_at=? AND id<?))",
                           (task['project_id'], task['created_at'], task['created_at'], task['id'])).fetchall()
    if earlier and not p.get('permission'):
        return 'Queued behind an earlier project request (sequential FIFO)'
    if not busy:
        pending = conn.execute("SELECT 1 FROM tasks t JOIN settings s ON s.key='assignment_source:'||t.id WHERE t.project_id=? AND t.id!=? AND json_extract(s.value,'$.state')='pending'", (task['project_id'], task['id'])).fetchone()
        if pending and not task['native_thread']:
            return 'Completed parallel sources require sequential integration/combined validation'
        return ''
    if not p.get('permission'):
        return 'Sequential request; waiting for the current project assignment'
    if not p.get('parallel_safe') or not p.get('change_paths'):
        return p.get('reason') or 'Parallel request queued: dependency/conflict assessment is incomplete'
    for other in busy:
        op = load(conn, 'assignment_policy:' + other['id'], {})
        if not other['workspace'] or op.get('integration'):
            return 'Parallel request queued: existing assignment owns the shared checkout or integration'
        if not op.get('parallel_safe'):
            return 'Parallel request queued: current assignment requires sequential/shared operations'
        if not op.get('change_paths') or overlaps(p['change_paths'], op['change_paths']):
            return 'Parallel request queued: overlapping or unknown change paths'
    return ''


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('Workspace Git operation failed: ' + result.stderr.strip()[:600])
    return result.stdout.strip()


def check_workspace(task):
    root = Path(task['workspace'])
    if root.is_symlink() or not root.is_dir() or git(root, 'rev-parse', '--show-toplevel') != str(root.resolve()):
        raise ValueError('Retained assignment workspace missing or changed; manual recovery required')
    if git(root, 'symbolic-ref', '--short', 'HEAD') != task['workspace_branch']:
        raise ValueError('Retained assignment branch changed; manual recovery required')
    return root


def prepare_workspace(db, conn, task):
    task = dict(task)
    if task['workspace']:
        check_workspace(task)
        return task
    # Never move an established native thread from its historical cwd.
    if task['native_thread'] or task['legacy']:
        if load(conn, 'assignment_policy:' + task['id'], {}).get('permission'):
            raise ValueError('Historical assignment remains sequential in its original workspace')
        return task
    root = Path(task['path'])
    from .pipeline import atomic, read
    receipt_path = db.path.parent / 'assignment-workspaces' / (task['id'] + '.json')
    receipt = read(receipt_path)
    try:
        if git(root, 'rev-parse', '--show-toplevel') != str(root.resolve()):
            raise ValueError('Project path is not the repository root')
        shared = git(root, 'rev-parse', 'HEAD')
        candidate = load(conn, 'project_candidate:' + task['project_id'], {})
        base = receipt['commit'] if receipt else candidate.get('commit', shared)
        git(root, 'cat-file', '-e', base + '^{commit}')
        if not receipt and candidate:
            try:
                git(root, 'merge-base', '--is-ancestor', base, shared)
                base = shared  # Shared checkout advanced after candidate/deployment.
            except ValueError:
                git(root, 'merge-base', '--is-ancestor', shared, base)
        if not receipt and git(root, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Shared checkout has uncommitted work')
    except ValueError:
        if receipt or load(conn, 'project_candidate:' + task['project_id']):
            raise ValueError('Retained candidate/shared checkout needs sequential review; original sources preserved')
        if load(conn, 'assignment_policy:' + task['id'], {}).get('permission'):
            raise ValueError('Parallel request queued: a clean committed Git base is required')
        return task  # Ordinary non-Git/dirty projects keep original sequential behavior.
    workspace = db.path.parent / 'assignment-workspaces' / task['id']
    branch = 'linko/assignment-' + task['id']
    workspace.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not receipt:
        atomic(receipt_path, {'commit': base, 'path': str(workspace), 'branch': branch})
    elif receipt['path'] != str(workspace) or receipt['branch'] != branch:
        raise ValueError('Assignment workspace receipt changed; inspect before recovery')
    # Deterministic identities survive a crash after Git add but before SQLite commit.
    if workspace.exists():
        retained = {**task, 'workspace': str(workspace), 'workspace_branch': branch}
        check_workspace(retained)
        git(workspace, 'merge-base', '--is-ancestor', base, branch)
    else:
        exists = subprocess.run(['git', '-C', str(root), 'show-ref', '--verify', 'refs/heads/' + branch], capture_output=True).returncode == 0
        if exists:
            git(root, 'worktree', 'add', str(workspace), branch)
        else:
            git(root, 'worktree', 'add', '-b', branch, str(workspace), base)
    conn.execute('UPDATE tasks SET workspace=?,workspace_branch=?,workspace_base=? WHERE id=?',
                 (str(workspace), branch, base, task['id']))
    task.update(workspace=str(workspace), workspace_branch=branch, workspace_base=base)
    db.event(conn, 'task.workspace', {'path': str(workspace), 'branch': branch, 'base': base}, task['project_id'], task['id'])
    return task


def reserve_integration(db, conn, task, source_ids):
    if not task['workspace']:
        raise ValueError('Integration requires a retained isolated assignment workspace')
    held = load(conn, INTEGRATION)
    if held and held['task_id'] != task['id']:
        raise ValueError('Another integration/combined validation is reserved')
    if conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND lease_owner IS NOT NULL', (task['project_id'],)).fetchone():
        raise ValueError('Integration waits for confirmed cleanup of every project writer')
    p = load(conn, 'assignment_policy:' + task['id'], {})
    prior_sources = {source['task_id']:source for source in p.get('sources', [])}
    sources = []
    for source_id in source_ids:
        source = conn.execute('SELECT * FROM tasks WHERE id=? AND project_id=?', (source_id, task['project_id'])).fetchone()
        resuming_self = source_id == task['id'] and source_id in prior_sources
        if not source or not source['workspace'] or source['lease_owner'] or (source['lifecycle'] != 'completed' and not resuming_self):
            raise ValueError('Integration source must be completed, isolated and stopped')
        check_workspace(source)
        if not resuming_self and git(source['workspace'], 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Source changes must be committed; branch and partial files are retained')
        pinned = prior_sources.get(source_id) or {'task_id': source_id, 'branch': source['workspace_branch'], 'commit': git(source['workspace'], 'rev-parse', 'HEAD')}
        git(source['workspace'], 'cat-file', '-e', pinned['commit'] + '^{commit}')
        sources.append(pinned)
    if not sources:
        raise ValueError('Integration needs explicit completed source assignment IDs')
    p = load(conn, 'assignment_policy:' + task['id'], {})
    p.update(integration=True, sources=sources)
    save(conn, 'assignment_policy:' + task['id'], p)
    save(conn, INTEGRATION, {'task_id': task['id'], 'project_id': task['project_id']})
    db.event(conn, 'integration.reserved', {'sources': sources}, task['project_id'], task['id'])


def release_integration(conn, task_id):
    held = load(conn, INTEGRATION)
    row = conn.execute('SELECT lease_owner,lifecycle FROM tasks WHERE id=?', (task_id,)).fetchone()
    if held and held['task_id'] == task_id and row and not row['lease_owner'] and row['lifecycle'] != 'queued':
        conn.execute('DELETE FROM settings WHERE key=?', (INTEGRATION,))


def reconcile_integration(conn):
    held = load(conn, INTEGRATION)
    if held:
        release_integration(conn, held['task_id'])


def retain_source(db, conn, task_id):
    """Record actual committed source after cleanup; merge evidence is Git ancestry,
    while validation evidence stays the worker report."""
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task or task['lease_owner'] or task['lifecycle'] != 'completed' or not task['workspace']:
        return
    try:
        check_workspace(task)
        head = git(task['workspace'], 'rev-parse', 'HEAD')
        dirty = bool(git(task['workspace'], 'status', '--porcelain', '--untracked-files=all'))
        save(conn, 'assignment_source:' + task_id, {'commit': head, 'state': 'pending', 'dirty': dirty})
        if dirty:
            return
        p = load(conn, 'assignment_policy:' + task_id, {})
        save(conn, 'assignment_source:' + task_id, {'commit': head, 'state': 'pending'})
        if p.get('integration'):
            for source in p['sources']:
                git(task['workspace'], 'merge-base', '--is-ancestor', source['commit'], head)
        else:
            others = conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND id!=? AND lease_owner IS NOT NULL',
                                  (task['project_id'], task_id)).fetchone()
            pending = conn.execute("SELECT 1 FROM tasks t JOIN settings s ON s.key='assignment_source:'||t.id WHERE t.project_id=? AND t.id!=? AND json_extract(s.value,'$.state')='pending'", (task['project_id'], task_id)).fetchone()
            if others or pending:
                return
        candidate = load(conn, 'project_candidate:' + task['project_id'])
        if candidate:
            git(task['workspace'], 'merge-base', '--is-ancestor', candidate['commit'], head)
        save(conn, 'project_candidate:' + task['project_id'], {'commit': head, 'task_id': task_id,
             'workspace': task['workspace'], 'branch': task['workspace_branch'], 'validation': 'worker report'})
        for source_id in [task_id] + [v['task_id'] for v in p.get('sources', [])]:
            source = load(conn, 'assignment_source:' + source_id)
            if source:
                save(conn, 'assignment_source:' + source_id, {**source, 'state': 'integrated', 'candidate': head})
        db.event(conn, 'integration.candidate' if p.get('integration') else 'task.candidate',
                 {'commit': head, 'workspace': task['workspace'], 'source_task': task_id}, task['project_id'], task_id)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        db.event(conn, 'integration.source_retained', {'reason': str(error)[:1000]}, task['project_id'], task_id)


def worker_admission(conn, task):
    """Recovery/launch cannot bypass a missing integration reservation or shared cwd."""
    if active_workers(conn, task['lease_owner']):
        raise ValueError('Single sequential execution; another worker still owns a live process')
    p = load(conn, 'assignment_policy:' + task['id'], {})
    held = load(conn, INTEGRATION)
    others = conn.execute('SELECT * FROM tasks WHERE project_id=? AND id!=? AND lease_owner IS NOT NULL',
                          (task['project_id'],task['id'])).fetchall()
    if p.get('integration'):
        if not held or held['task_id'] != task['id'] or others:
            raise ValueError('Integration ownership changed; retained workspace requires sequential recovery')
    elif held and held['project_id'] == task['project_id']:
        raise ValueError('Serialized integration owns the project; coding recovery is held')
    elif others:
        for other in others:
            op = load(conn,'assignment_policy:'+other['id'],{})
            if (not task['workspace'] or not other['workspace'] or not p.get('parallel_safe')
                    or not op.get('parallel_safe') or not p.get('change_paths') or not op.get('change_paths')
                    or overlaps(p['change_paths'],op['change_paths'])
                    or not (p.get('permission') or op.get('permission'))):
                raise ValueError('Recovered project writers lack safe isolated per-request parallel ownership')


def active_workers(conn, excluding=None):
    from .recovery import owned_alive
    rows = conn.execute('SELECT e.*,t.lease_owner FROM tasks t LEFT JOIN executions e ON e.id=t.lease_owner WHERE t.lease_owner IS NOT NULL').fetchall()
    return sum(1 for row in rows if row['lease_owner'] != excluding and
               (row['id'] is None or row['status'] != 'recovering' or owned_alive(row)))

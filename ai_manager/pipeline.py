"""Merge into main and the deploy request/result protocol. Contract: README.md.

The worker (core.py) merges a passed branch into main (one merge at a time across projects), then asks the single
deploy script (scripts/deploy.py) to deploy main when the project has a saved setup. The script itself waits for idle, backs up,
restarts, health-checks and rolls back; this module only writes its request file and reports its short result. A pending
request holds new work and merges for at most DEPLOY_WAIT seconds. No AI call decides anything here.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from . import fence
from .deploy_auto import SETUP_FILE, read_proposal
from .deploy_setup import validate_setup
from .fence import Busy, main_branch
from .github_backup import GitHubBackup

RESTRICTED = 'Not available on a restricted instance'
NO_SETUP = 'no deploy setup; a Create deploy setup task is queued automatically'
MERGE_WAIT = 30  # seconds a merge waits for another project's merge to leave the checkout fence
DEPLOY_WAIT = 1800  # a pending deploy request holds the queue at most this long, then it is withdrawn
IDENTITY = ['-c', 'user.name=Linko Bot Boss', '-c', 'user.email=bot-boss@linko.invalid']


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2))
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def git(root, *args, check=True, env=None, input=None):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120,
                            env={**os.environ, 'LC_ALL': 'C', **(env or {})}, input=input)
    if check and result.returncode:
        raise ValueError('git ' + args[0] + ' failed: ' + (result.stderr or result.stdout).strip()[:300])
    return result.stdout.strip() if check else result


def merge_tree(root, head, commit):
    """Preview without changing either checkout; names are NUL-delimited, including Unicode."""
    result = git(root, 'merge-tree', '--write-tree', '--name-only', '-z', head, commit, check=False)
    if result.returncode not in {0, 1}:
        raise ValueError('Could not preview merge: ' + (result.stderr or result.stdout).strip()[:300])
    tree, *fields = result.stdout.split('\0')
    paths = []
    for path in fields:
        if not path:
            break
        paths.append(path)
    return tree, paths


def tree_files(root, tree):
    entries = git(root, 'ls-tree', '-r', '-z', '--full-tree', tree, check=False)
    if entries.returncode:
        raise ValueError('Could not read merge tree')
    files = {}
    for entry in entries.stdout.split('\0'):
        if entry:
            metadata, path = entry.split('\t', 1)
            mode, _, oid = metadata.split()
            files[path] = (mode, oid)
    return files


def overlaps(left, right):
    return left == right or left.startswith(right + '/') or right.startswith(left + '/')


def saved_copies(root, head, commit):
    """Recover the report after a retry/restart between the Git merge and the chat transaction."""
    message = git(root, 'log', '-1', '--format=%B', '--fixed-strings',
                  '--grep=AI task commit: ' + commit, head)
    for line in message.splitlines():
        if line.startswith('AI conflict copies: '):
            return json.loads(line.removeprefix('AI conflict copies: '))
    return []


def merge_into_main(root, commit):
    """Return (head, changed, copies), keeping the folder's conflicting files untouched. Never forces."""
    root = Path(root)
    commit = git(root, 'rev-parse', '--verify', commit + '^{commit}')
    branch = main_branch(root)
    if git(root, 'symbolic-ref', '-q', '--short', 'HEAD', check=False).stdout.strip() != branch:
        git(root, 'checkout', '--quiet', branch)
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        git(root, 'add', '-u')
        git(root, *IDENTITY, 'commit', '-m', 'Save local changes in project folder before merging task')
    head = git(root, 'rev-parse', 'HEAD')
    if git(root, 'merge-base', '--is-ancestor', commit, head, check=False).returncode == 0:
        return head, False, saved_copies(root, head, commit)

    tree, conflicts = merge_tree(root, head, commit)
    main, task, merged = (tree_files(root, ref) for ref in (head, commit, tree))
    # Include ignored paths: Git's ort strategy can overwrite them even with --no-overwrite-ignore.
    local_files = git(root, 'ls-files', '--others', '-z', check=False)
    if local_files.returncode:
        raise ValueError('Could not list local untracked files')
    untracked = [p for p in local_files.stdout.split('\0') if p]
    collisions = {path for path in merged if any(overlaps(path, local) for local in untracked)
                  or (path not in main and os.path.lexists(root / path))}
    conflicts = sorted(set(conflicts) | collisions)
    if not conflicts:
        git(root, *IDENTITY, 'merge', '--no-edit', '--no-overwrite-ignore', commit)
        return git(root, 'rev-parse', 'HEAD'), True, []

    reserved = set(main) | set(task) | set(merged) | set(untracked)
    copies = []
    for path in conflicts:
        merged.pop(path, None)
        if path in main:
            merged[path] = main[path]
        copy = None
        if path in task:
            original = Path(path)
            number = 1
            while True:
                suffix = ' (AI version)' + (f' {number}' if number > 1 else '')
                candidate = str(original.with_name(original.stem + suffix + original.suffix))
                if not any(overlaps(candidate, name) for name in reserved) and not os.path.lexists(root / candidate):
                    break
                number += 1
            # A file obstructing a parent directory has no adjacent child path. Fail before touching the checkout.
            if any(os.path.lexists(parent) and (parent.is_symlink() or not parent.is_dir())
                   for parent in (root / candidate).parents if parent != root and root in parent.parents):
                raise ValueError('Cannot save AI copy below a local file: ' + path)
            copy = candidate
            reserved.add(copy)
            merged[copy] = task[path]
        copies.append({'path': path, 'copy': copy})

    # Resolve in a disposable index. No conflict markers, untracked files or task deletions touch the folder.
    with tempfile.TemporaryDirectory(prefix='linko-merge-') as temp:
        env = {'GIT_INDEX_FILE': str(Path(temp) / 'index')}
        git(root, 'read-tree', '--empty', env=env)
        entries = ''.join(f'{mode} {oid}\t{path}\0' for path, (mode, oid) in sorted(merged.items()))
        git(root, 'update-index', '-z', '--index-info', env=env, input=entries)
        tree = git(root, 'write-tree', env=env)
    listing = '\n'.join(f"- {item['path']} -> {item['copy'] or '(user version kept; AI deleted)'}" for item in copies)
    message = ('Merge task; preserve user files and save AI versions\n\n' + listing +
               '\n\nAI task commit: ' + commit + '\nAI conflict copies: ' + json.dumps(copies, ensure_ascii=False))
    resolved = git(root, *IDENTITY, 'commit-tree', tree, '-p', head, '-p', commit, input=message + '\n')
    git(root, *IDENTITY, 'merge', '--ff-only', '--no-overwrite-ignore', resolved)
    return git(root, 'rev-parse', 'HEAD'), True, copies


class Pipeline:
    def __init__(self, db, registry, restricted=False):
        self.db, self.registry, self.job, self.restricted = db, registry, None, restricted
        self.root = db.path.parent / 'deploy'
        self.backups = GitHubBackup(db, registry, lambda project_id: self.setup(project_id) is not None)

    def setup(self, project_id):
        project = self.registry.get(project_id)
        if self.restricted:
            return None  # restricted instances merge only; they never deploy
        key = 'deploy_setup:' + project_id
        saved = self.db.setting(key, 'unmigrated')
        if saved != 'unmigrated':
            return saved
        # Migrate the existing installation once, without inventing a setup for other repositories.
        installed = read(self.root / 'installed.json') or {}
        if installed.get('repo') and Path(installed['repo']).resolve() == Path(project['path']).resolve():
            saved = {'runtime': installed}
            self.db.run('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(saved)))
            return self.db.setting(key)
        return None

    def save_setup(self, project_id, setup):
        self.registry.get(project_id)
        if self.restricted:
            raise ValueError(RESTRICTED)
        setup = validate_setup(setup) if setup is not None else None
        with fence.hold(self.db.path.parent, exclusive=True):
            if self.pending():
                raise ValueError('Finish or cancel the pending deploy before changing a setup')
            self.db.set_setting('deploy_setup:' + project_id, setup)
            with self.db.transaction() as conn:
                self.db.event(conn, 'deployment.setup_saved', {'configured': setup is not None}, project_id)
        return self.status(project_id)

    def no_setup(self, project_id):
        if self.restricted:
            return 'no deploy setup; merges only'
        none = self.db.setting('deploy_none:' + project_id)
        return 'not deployable: ' + none['reason'] if none else NO_SETUP

    def adopt(self, project_id, root):
        """Seed the project's one saved setup from the merged `.linko-deploy.json`; never overwrites a saved setup.

        Returns None when nothing applies, else {'state': seeded | none | invalid | missing | declined, 'reason': ...}.
        'missing' means the application should queue a Create deploy setup task. The file seeds once: a setup the owner
        removed later is not recreated from the same file.
        """
        if self.restricted or self.setup(project_id) is not None:
            return None
        state, value, digest = read_proposal(root)
        none = self.db.setting('deploy_none:' + project_id)
        seeded = self.db.setting('deploy_seeded:' + project_id)
        if state == 'invalid':
            with self.db.transaction() as conn:
                self.db.event(conn, 'deployment.setup_file_invalid', {'reason': value}, project_id)
            return {'state': 'invalid', 'reason': value}
        if state == 'none':
            if not none or none.get('digest') != digest:
                self.db.set_setting('deploy_none:' + project_id, {'reason': value, 'digest': digest})
            return {'state': 'none', 'reason': value}
        if state == 'setup' and seeded != digest:
            self.db.set_setting('deploy_seeded:' + project_id, digest)
            self.db.set_setting('deploy_none:' + project_id, None)
            self.db.set_setting('deploy_setup:' + project_id, value)
            with self.db.transaction() as conn:
                self.db.event(conn, 'deployment.setup_saved', {'configured': True, 'source': SETUP_FILE}, project_id)
            return {'state': 'seeded', 'reason': 'deploy setup seeded from ' + SETUP_FILE}
        if none:
            return {'state': 'none', 'reason': none['reason']}
        if seeded:
            return {'state': 'declined', 'reason': 'the saved setup was removed after it was seeded from ' + SETUP_FILE}
        return {'state': 'missing', 'reason': NO_SETUP}

    def deployable(self, project_id):
        return self.setup(project_id) is not None

    # ---- deploy protocol (files shared with scripts/deploy.py) ---------------------------------------------
    def installed(self):
        return (self.root / 'installed.json').is_file()

    def pending(self):
        return (self.root / 'request.json').is_file()

    def status(self, project_id=None):
        last = read(self.root / 'result.json')
        if project_id:
            key = hashlib.sha256(project_id.encode()).hexdigest()
            last = read(self.root / 'project-results' / (key + '.json')) or (
                last if last and last.get('project_id') == project_id else None)
        setup = self.setup(project_id) if project_id else None
        return {'installed': self.installed(), 'request': read(self.root / 'request.json'),
                'state': read(self.root / 'state.json') if self.pending() else None,
                'last_result': last,
                **({'setup': setup, 'deployment': 'deploy setup saved' if setup is not None
                    else self.no_setup(project_id)} if project_id else {})}

    def summary(self, project_id=None):
        """Short fact for the manager prompt."""
        value = self.status(project_id)
        if project_id and not value['setup']:
            return self.no_setup(project_id)
        if value['request']:
            if project_id and value['request'].get('project_id') != project_id:
                return "another project's deploy holds the queue"
            return 'deploy requested; ' + ((value['state'] or {}).get('phase') or 'queued')
        last = value['last_result']
        return (last['status'] + ': ' + last['reason'])[:200] if last else 'no deploy yet'

    def request(self, project_id, source='auto', task_id=None, conversation_id=None, reason=''):
        with fence.hold(self.db.path.parent, exclusive=True, wait=5):
            return self._request(project_id, source, task_id, conversation_id, reason)

    def _request(self, project_id, source, task_id, conversation_id, reason):
        project = self.registry.get(project_id)
        setup = self.setup(project_id)
        if setup is None:
            return {'status': 'no deploy setup', 'reason': self.no_setup(project_id), 'project_id': project_id}
        if self.pending():
            pending = read(self.root / 'request.json')
            if pending.get('project_id') != project_id:
                raise ValueError('Another project has a pending deploy; retry after it finishes')
            return pending
        value = {'id': hashlib.sha256(os.urandom(16)).hexdigest()[:16], 'project_id': project_id, 'source': source,
                 'task_id': task_id, 'conversation_id': conversation_id, 'reason': reason[:300],
                 'requested_at': time.time(), 'repo': str(self.registry.allowed_path(project['path'])), 'setup': setup}
        atomic(self.root / 'request.json', value)
        return value

    def cancel(self, project_id=None):
        """Withdraw a request that has not yet reached backup/restart; the script re-checks before it stops anything."""
        try:
            with fence.hold(self.db.path.parent, exclusive=True):
                return self._cancel(project_id)
        except Busy:
            raise ValueError('Deploy holds the checkout; it finishes or rolls back automatically') from None

    def _cancel(self, project_id):
        request = read(self.root / 'request.json')
        if not request or (project_id and request.get('project_id') != project_id):
            return False
        phase = (read(self.root / 'state.json') or {}).get('phase')
        if phase not in {None, 'testing', 'waiting_idle'}:
            raise ValueError('Deploy already started; it finishes or rolls back automatically')
        (self.root / 'request.json').unlink(missing_ok=True)
        return True

    def post(self, conn, conversation_id, key, text):
        if conversation_id and conn.execute('SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone():
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                         "VALUES(?,?,'assistant',?,'completed',?)",
                         ('pipeline-' + hashlib.sha256(key.encode()).hexdigest()[:24], conversation_id, text, time.time()))

    def report(self):
        """Tell the originating conversation about a finished deploy exactly once; returns the receipt when it was new."""
        result = read(self.root / 'result.json')
        if not result or self.pending():
            return None
        with self.db.transaction() as conn:
            key = 'deploy_reported:' + result['id']
            if conn.execute('SELECT 1 FROM settings WHERE key=?', (key,)).fetchone():
                return None
            payload = {k: result.get(k) for k in ('id', 'status', 'reason', 'commit', 'previous', 'repairable', 'source')}
            self.db.event(conn, 'deployment.result', payload, result.get('project_id'), result.get('task_id'))
            text = {'success': 'Deploy succeeded', 'failed': 'Deploy failed; nothing changed',
                    'rolled_back': 'Deploy failed and was rolled back',
                    'rollback_failed': 'Deploy failed and could not be rolled back; the service needs attention',
                    'cancelled': 'Deploy cancelled'}.get(result['status'], 'Deploy ' + result['status'])
            self.post(conn, result.get('conversation_id'), key, f"{text}: {result['reason']}"[:600])
            if result['status'] == 'success' and result.get('commit') and result.get('project_id'):
                self.backups.request(result['project_id'], result['commit'], conn=conn)
            conn.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(payload)))
        return result

    # ---- merge to main ----------------------------------------------------------------------------------------
    def report_merge_copies(self, commit, copies, deploy):
        """Use the normal chat transaction, with one stable message key per task commit."""
        if not copies or not deploy or not deploy[2]:
            return
        project_id, _, task_id, conversation_id, _ = deploy
        key = 'merge-copies:' + task_id + ':' + commit
        message_id = 'pipeline-' + hashlib.sha256(key.encode()).hexdigest()[:24]
        text = '\n'.join(
            f"Both you and the AI changed {item['path']}; your version is kept, " +
            (f"the AI's is saved as {item['copy']}." if item['copy'] else 'the AI deleted it, so no copy is needed.')
            for item in copies)
        with self.db.transaction() as conn:
            if conn.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone():
                return
            self.db.event(conn, 'task.merge_copies', {'commit': commit, 'copies': copies}, project_id, task_id)
            self.post(conn, conversation_id, key, text)

    def merge(self, project_path, commit, deploy=None):
        """The only path into main: exclusive checkout fence, re-checked pending deploy, then the merge.

        Tasks of different projects run in parallel and share this one fence, so a merge waits briefly for another's.
        When the merge changed main and `deploy` (the arguments of request()) is given, the deploy is requested under
        the same fence hold: no other project can take the single deploy request between merge and request.
        A merge that brings in a valid `.linko-deploy.json` first seeds the project's missing setup (adopt()), so the same
        hold requests its first deploy. Returns (head, changed, details); details carries the deployment response,
        'copies' for preserved conflict versions and 'setup_file' for the adopt() result, or is None when none apply.
        """
        with fence.hold(self.db.path.parent, exclusive=True, wait=MERGE_WAIT):
            if self.pending():
                raise Busy('a deploy is pending')
            commit = git(project_path, 'rev-parse', '--verify', commit + '^{commit}')
            head, changed, copies = merge_into_main(project_path, commit)
            self.report_merge_copies(commit, copies, deploy)
            project = self.db.one('SELECT id FROM projects WHERE path=?', (str(project_path),))
            note = None
            if project:
                note = self.adopt(project['id'], project_path)
                self.backups.request(project['id'])
            seeded = bool(note) and note['state'] == 'seeded'
            deployment = self._request(*deploy) if deploy and (changed or seeded) else None
            details = dict(deployment or {})
            if note:
                details['setup_file'] = note
            if copies:
                details['copies'] = copies
            return head, changed, details or None

    def expire(self, max_age=DEPLOY_WAIT):
        """A deploy request that has not finished in time (script not installed, stuck unit, endless idle wait) must not
        hold the queue: withdraw it while that is still safe. Past the backup it finishes or rolls back by itself."""
        try:
            with fence.hold(self.db.path.parent, exclusive=True):
                return self._expire(max_age)
        except Busy:
            return False

    def _expire(self, max_age):
        request = read(self.root / 'request.json')
        if not request or time.time() - request.get('requested_at', time.time()) <= max_age:
            return False
        if (read(self.root / 'state.json') or {}).get('phase') not in {None, 'testing', 'waiting_idle'}:
            return False
        (self.root / 'request.json').unlink(missing_ok=True)
        (self.root / 'state.json').unlink(missing_ok=True)
        with self.db.transaction() as conn:
            self.db.event(conn, 'deployment.expired', {'id': request.get('id')}, request.get('project_id'))
            self.post(conn, request.get('conversation_id'), 'expired:' + str(request.get('id')),
                      f'Deploy request withdrawn after {max_age // 60} minutes without finishing so it cannot hold the queue '
                      '(is the deploy unit installed?). Nothing changed; merged work stays on main. Ask again to deploy.')
        return True

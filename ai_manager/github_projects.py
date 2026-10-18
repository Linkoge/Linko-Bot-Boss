"""Create and rename a GitHub repository together with its local project. There is deliberately no delete action.

The token is a GitHub classic personal access token with only the `repo` scope. It lives in one file outside every
repository (mode 0600, directory 0700), is read only at the moment of an API call, and is never stored in SQLite,
events, logs, API responses, tasks or prompts. Pushes keep using the service user's own Git credentials (SSH).
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import fence, secret_check
from .config import CODE_ROOT
from .pipeline import RESTRICTED
from .registry import git, safe_remote

API = 'https://api.github.com'
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
ORG = re.compile(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*')
COMPANY_ORG = 'github_company_org'
REMOTE = re.compile(r'^(?P<prefix>.*?github\.com[:/])(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?P<suffix>\.git)?/?$')
# Fixed allow-list; organization creation below also requires an exact match to the saved, validated org.
# Anything else (in particular DELETE) is refused locally.
ALLOWED = (('GET', re.compile(r'^/user$')), ('POST', re.compile(r'^/user/repos$')),
           ('PATCH', re.compile(r'^/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$')))
log = logging.getLogger(__name__)


class GitHubError(ValueError):
    """A rejected or failed action. `steps` states exactly what was and was not done."""

    def __init__(self, message, steps=None):
        super().__init__(message)
        self.steps = steps or []


def default_token_file():
    return Path(os.getenv('AI_MANAGER_GITHUB_TOKEN_FILE') or Path.home() / '.config' / 'linko-bot-manager' / 'github-token')


class TokenStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()

    def configured(self):
        return self.path.is_file()

    def read(self):
        try:
            if self.path.stat().st_mode & 0o077:
                raise GitHubError('The GitHub token file must be readable only by the service user (chmod 600)')
            token = self.path.read_text().strip()
        except FileNotFoundError:
            token = ''
        if not token:
            raise GitHubError('No GitHub token is saved. Enter one in Settings first')
        return token

    def write(self, token):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.token-')
        try:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(token + '\n')
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def urllib_fetch(method, url, headers, body, timeout):
    """(status, headers, parsed JSON). HTTP error statuses are returned, not raised."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, received, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, received, raw = error.code, error.headers, error.read()
    try:
        data = json.loads(raw or b'null')
    except ValueError:
        data = None
    return status, {k.lower(): v for k, v in received.items()}, data


def valid_org(name):
    return isinstance(name, str) and 1 <= len(name) <= 39 and ORG.fullmatch(name) is not None


class GitHubApi:
    def __init__(self, store, fetch=None, company_org=None):
        self.store, self.fetch = store, fetch or urllib_fetch
        self.company_org = company_org or (lambda: '')

    def request(self, method, path, payload=None, token=None):
        org = self.company_org()
        org_creation = method == 'POST' and valid_org(org) and path == f'/orgs/{org}/repos'
        if not org_creation and not any(m == method and rule.fullmatch(path) for m, rule in ALLOWED):
            raise GitHubError('This GitHub action is not available')
        token = token or self.store.read()
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'linko-bot-boss',
                   'Content-Type': 'application/json'}
        try:
            status, received, data = self.fetch(method, API + path, headers,
                                                json.dumps(payload).encode() if payload is not None else None, 20)
        except Exception:
            # Transport errors can echo request details. Never keep their text.
            raise GitHubError('GitHub could not be reached') from None
        if status >= 400:
            if org_creation and status in (401, 403, 404):
                raise GitHubError(f"GitHub refused organization creation ({status}). The token's account needs "
                                  f'permission to create repositories in the organization {org}')
            raise GitHubError(self.explain(status, data, token))
        return received, data or {}

    @staticmethod
    def explain(status, data, token):
        text = ''
        if isinstance(data, dict):
            text = str(data.get('message') or '')
            errors = data.get('errors')
            if isinstance(errors, list):
                text += ' ' + ' '.join(str(e.get('message', '')) for e in errors if isinstance(e, dict))
        text = text.replace(token, '***').strip()[:200]
        if status in (401, 403) and not text:
            text = 'token rejected or not permitted'
        return f'GitHub refused the request ({status}){": " + text if text else ""}'


def step(steps, name, status, detail=''):
    steps.append({'step': name, 'status': status, **({'detail': detail} if detail else {})})


class GitHubProjects:
    def __init__(self, db, registry, settings, pipeline, store=None, fetch=None):
        self.db, self.registry, self.settings, self.pipeline = db, registry, settings, pipeline
        self.store = store or TokenStore(settings.github_token_file or default_token_file())
        self.api = GitHubApi(self.store, fetch, self.company_org)

    def company_org(self):
        return self.db.setting(COMPANY_ORG, '')

    def save_company_org(self, org):
        self.guard()
        org = org.strip()
        if org and not valid_org(org):
            raise GitHubError('Use a GitHub organization name: 1-39 letters or digits with single hyphens '
                              'between them (no spaces, underscores or URL)')
        self.db.set_setting(COMPANY_ORG, org)
        return self.status()

    def guard(self):
        if self.settings.restricted:
            raise ValueError(RESTRICTED)

    @staticmethod
    def check_name(name):
        if not isinstance(name, str) or not NAME.match(name) or name.endswith('.git') or '..' in name:
            raise GitHubError('Use 1-100 letters, digits, dots, hyphens or underscores, starting with a letter or digit')
        return name

    def status(self):
        self.guard()
        return {'configured': self.store.configured(), 'company_org': self.company_org()}

    def save_token(self, token):
        """Accept only a classic token with `repo` and without `delete_repo`, then store it."""
        self.guard()
        token = (token or '').strip()
        if not 20 <= len(token) <= 255 or re.search(r'\s', token):
            raise GitHubError('That does not look like a GitHub personal access token')
        received, data = self.api.request('GET', '/user', token=token)
        scopes = {s.strip() for s in received.get('x-oauth-scopes', '').split(',') if s.strip()}
        if 'x-oauth-scopes' not in received:
            raise GitHubError('Use a classic personal access token, not a fine-grained one')
        if 'repo' not in scopes:
            raise GitHubError('The token needs the repo scope')
        if scopes - {'repo'}:
            raise GitHubError('The token must have only the repo scope; remove every other scope, delete_repo especially')
        self.store.write(token)
        return {'configured': True, 'login': str(data.get('login', ''))}

    # ---- create -----------------------------------------------------------------------------------------------

    def create(self, name, tier='personal', public_confirmed=False):
        self.guard()
        self.check_name(name)
        if tier not in {'personal', 'company'}:
            raise GitHubError('Choose Personal or Company')
        company = tier == 'company'
        org = self.company_org()
        if company and not valid_org(org):
            raise GitHubError('Company is unavailable. Configure the company organization in Settings → GitHub')
        if company and public_confirmed is not True:
            raise GitHubError('Confirm that the Company repository will be public before creating it')
        private = not company
        root = self.settings.project_roots[0]
        folder = root / name
        if folder.exists() or folder.is_symlink() or self.db.one('SELECT 1 FROM projects WHERE path=?', (str(folder),)):
            raise GitHubError(f'The folder {name} already exists; nothing was created')
        steps = []
        self.store.read()
        if company:
            # Prepare the actual initial project and scan all its history before creating anything public.
            # No registration means the backup worker cannot push before this check and confirmation.
            folder.mkdir(parents=True)
            try:
                self.initialize(folder, name, None)
                step(steps, 'create local folder and git init', 'done', str(folder))
                if secret_check.scan(folder, None, 'HEAD'):
                    raise GitHubError('Secret check failed. Company creation was blocked; '
                                      'remove credentials before creating a public repository')
                step(steps, 'secret check', 'done')
            except Exception as error:
                message = str(error) if isinstance(error, GitHubError) else 'Could not prepare and secret-check the project'
                step(steps, 'prepare and secret-check project', 'failed', message)
                self.remove_new_folder(folder, steps)
                raise GitHubError(message + '. No GitHub repository was created', steps) from None
        try:
            _, repo = self.api.request('POST', f'/orgs/{org}/repos' if company else '/user/repos',
                                       {'name': name, 'private': private})
        except GitHubError as error:
            step(steps, 'create GitHub repository', 'failed', str(error))
            if company:
                self.remove_new_folder(folder, steps)
            raise GitHubError(f'{error}. Nothing was created', steps) from None
        remote = repo.get('ssh_url') or ''
        step(steps, 'create GitHub repository', 'done', repo.get('full_name', name))
        try:
            if not REMOTE.match(remote):
                raise ValueError('GitHub returned no usable repository address')
            if company:
                self.set_remote(folder, remote)
            else:
                folder.mkdir(parents=True)
                self.initialize(folder, name, remote)
                step(steps, 'create local folder and git init', 'done', str(folder))
        except Exception as error:
            step(steps, 'create local folder and git init', 'failed', self.safe_text(error))
            self.remove_new_folder(folder, steps)
            step(steps, 'register in manager', 'not run')
            raise GitHubError(self.leftover(repo, steps), steps) from None
        try:
            project = self.registry.add(folder, github_tier=tier)
            step(steps, 'register in manager', 'done')
        except Exception as error:
            step(steps, 'register in manager', 'failed', self.safe_text(error))
            self.remove_new_folder(folder, steps)
            raise GitHubError(self.leftover(repo, steps), steps) from None
        self.db.run('DELETE FROM settings WHERE key=?', ('github_backup:' + project['id'],))  # fresh backup receipt
        return {'result': 'done', 'message': f'Created {repo.get("full_name", name)} and registered the project',
                'project': self.registry.get(project['id']), 'repository': repo.get('full_name', name),
                'private': bool(repo.get('private', private)), 'tier': tier, 'steps': steps}

    @staticmethod
    def initialize(folder, name, remote):
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_GLOBAL': os.devnull}
        identity = ['-c', 'user.name=Linko Bot Boss', '-c', 'user.email=linko-bot-boss@localhost',
                    '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgsign=false']
        (folder / 'README.md').write_text(f'# {name}\n')
        for args in (['init', '-q', '-b', 'main'], *([['remote', 'add', 'origin', remote]] if remote else []), ['add', 'README.md'],
                     [*identity, 'commit', '-q', '-m', 'Initial commit']):
            subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, timeout=60, env=env)

    @staticmethod
    def remove_new_folder(folder, steps):
        """Only ever the folder this very action created."""
        try:
            shutil.rmtree(folder)
            step(steps, 'remove the new local folder', 'rolled back')
        except OSError:
            step(steps, 'remove the new local folder', 'failed', f'{folder} must be removed manually')

    @staticmethod
    def leftover(repo, steps):
        return (f'Stopped. The GitHub repository {repo.get("full_name", "")} WAS created and was not deleted: '
                'this application cannot delete repositories. Delete or reuse it on GitHub. '
                + '; '.join(f'{s["step"]}: {s["status"]}' for s in steps))

    # ---- rename -----------------------------------------------------------------------------------------------

    def rename(self, project_id, new_name):
        self.guard()
        self.check_name(new_name)
        project = self.registry.get(project_id)
        if project['status'] == 'removed':
            raise GitHubError('Unknown or removed project')
        old_path = Path(project['path'])
        new_path = old_path.parent / new_name
        if old_path.resolve() == CODE_ROOT:
            raise GitHubError("The manager cannot rename its own live checkout; nothing was changed")
        if old_path.name == new_name:
            raise GitHubError('The project already has that name')
        if new_path.exists() or new_path.is_symlink() or self.db.one('SELECT 1 FROM projects WHERE path=?', (str(new_path),)):
            raise GitHubError(f'The folder {new_name} already exists; nothing was changed')
        remote = git(old_path, 'remote', 'get-url', 'origin')
        match = REMOTE.match(remote)
        if not match:
            raise GitHubError('The project has no github.com origin; nothing was changed')
        self.store.read()
        try:
            with fence.hold(self.db.path.parent, exclusive=True, wait=5):
                # Held for the whole rename: no task can start, nothing merges and nothing deploys meanwhile.
                self.ensure_idle(project_id)
                return self.rename_locked(project, old_path, new_path, new_name, match)
        except fence.Busy:
            raise GitHubError('A merge or deploy is using the checkout; try again shortly. Nothing was changed') from None

    def ensure_idle(self, project_id):
        if self.db.one("SELECT 1 FROM tasks WHERE project_id=? AND state='running'", (project_id,)) or self.db.one(
                'SELECT 1 FROM executions WHERE project_id=? AND finished_at IS NULL', (project_id,)):
            raise GitHubError('This project has a running task. Rename after it finishes; nothing was changed')
        if self.pipeline.setup(project_id) or self.pipeline.pending():
            raise GitHubError('This project has a saved deploy setup or a pending deployment tied to its folder path; '
                              'nothing was changed')

    def rename_locked(self, project, old_path, new_path, new_name, match):
        steps, owner, old_repo = [], match['owner'], match['repo']
        try:
            self.api.request('PATCH', f'/repos/{owner}/{old_repo}', {'name': new_name})
            step(steps, 'rename GitHub repository', 'done', f'{owner}/{old_repo} -> {owner}/{new_name}')
        except GitHubError as error:
            step(steps, 'rename GitHub repository', 'failed', str(error))
            raise GitHubError(f'{error}. Nothing was changed', steps) from None
        undo = [('rename GitHub repository back', lambda: self.api.request(
            'PATCH', f'/repos/{owner}/{new_name}', {'name': old_repo}))]
        new_remote = f'{match["prefix"]}{owner}/{new_name}{match["suffix"] or ""}'
        stage = 'rename local folder'
        try:
            os.rename(old_path, new_path)
            undo.append(('rename local folder back', lambda: os.rename(new_path, old_path)))
            step(steps, stage, 'done', f'{old_path.name} -> {new_name}')
            stage = 'update git remote'
            self.set_remote(new_path, new_remote)
            undo.append(('restore git remote', lambda: self.set_remote(new_path, match.string)))
            step(steps, stage, 'done')
            stage = 'update project in manager'
            self.repair_worktrees(new_path)
            updated = self.update_project(project, new_path, new_name, old_path.name, new_remote)
            step(steps, stage, 'done')
        except Exception as error:
            step(steps, stage, 'failed', self.safe_text(error))
            for label, action in reversed(undo):
                try:
                    action()
                    step(steps, label, 'rolled back')
                except Exception:
                    step(steps, label, 'failed', 'could not be undone; fix manually')
            if old_path.is_dir():
                self.repair_worktrees(old_path)
            raise GitHubError('Rename stopped. ' + '; '.join(f'{s["step"]}: {s["status"]}' for s in steps),
                              steps) from None
        return {'result': 'done', 'message': f'Renamed to {new_name} (GitHub, folder, remote and manager)',
                'project': updated, 'steps': steps}

    @staticmethod
    def set_remote(path, url):
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}

        def run(*args, check=True):
            return subprocess.run(['git', '-C', str(path), *args], check=check, capture_output=True, timeout=30, env=env)

        if run('remote', 'get-url', 'origin', check=False).returncode:
            run('remote', 'add', 'origin', url)
        else:
            run('remote', 'set-url', 'origin', url)
        if run('config', '--get', 'remote.origin.pushurl', check=False).returncode == 0:
            run('remote', 'set-url', '--push', 'origin', url)

    @staticmethod
    def repair_worktrees(path):
        """Retained task workspaces point back at the checkout; best effort, a failure leaves them recoverable."""
        listing = subprocess.run(['git', '-C', str(path), 'worktree', 'list', '--porcelain'], capture_output=True,
                                 text=True, timeout=30)
        others = [line[9:] for line in listing.stdout.splitlines() if line.startswith('worktree ')][1:]
        if others:
            subprocess.run(['git', '-C', str(path), 'worktree', 'repair', *others], capture_output=True, timeout=60)

    def update_project(self, project, new_path, new_name, old_name, new_remote):
        with self.db.transaction() as conn:
            display = project.get('display_name')
            conn.execute(
                'UPDATE projects SET name=?,path=?,repository=?,display_name=?,revision=revision+1,updated_at=? WHERE id=?',
                (new_name, str(new_path), safe_remote(new_remote), None if display == old_name else display,
                 time.time(), project['id']))
            self.db.event(conn, 'project.renamed', {'from': old_name, 'to': new_name}, project['id'])
        return self.registry.get(project['id'])

    def safe_text(self, error):
        # Git and OS errors may carry paths or URLs; the type alone is enough for the owner.
        return type(error).__name__

"""Owner edits of registered instances' existing model and harness records, without starting or migrating them.

The main instance reads and writes another instance's own manager.sqlite3 (mode=rw, never created or migrated); the
instance reads those records at its next manager turn, so nothing has to be restarted or reached over HTTP."""
import json
import sqlite3
from pathlib import Path

from . import model_settings
from .db import Database
from .providers import provider_of


class ExistingDatabase(Database):
    def __init__(self, data):
        # Do not call Database.__init__: only the instance itself may initialize/migrate its database.
        self.path = Path(data).expanduser().resolve() / 'manager.sqlite3'

    def connect(self):
        # mode=rw refuses missing databases. A bounded busy timeout makes a locked instance a visible error.
        conn = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        return conn


class InstanceCatalog:
    """Use the shared catalog with the target's default provider, without changing the main server's routing state."""
    def __init__(self, db, server):
        self.server, self.catalog = server, server.catalog
        self.default_provider = model_settings.active(db)

    def route(self, model=None, effort=None):
        if backends := getattr(self.server, 'backends', None):
            return backends[provider_of(model) if model else self.default_provider].route(model, effort)
        return self.server.route(model, effort)


def instances(data):
    path = data / 'deploy' / 'installed.json'
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return []
    try:
        config = json.loads(raw)
        entries = config.get('instances', [])
        if (not isinstance(entries, list) or any(not isinstance(e, dict) or not isinstance(e.get('name'), str)
                or not e['name'] or not isinstance(e.get('data'), str) or not Path(e['data']).is_absolute()
                for e in entries) or len({e['name'] for e in entries}) != len(entries)):
            raise ValueError('Invalid instances list')
        return entries
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError(f'Cannot read Users from {path}: invalid instance registry') from error


def access_error(name, error, what='model settings'):
    return (f'{name}: cannot access {what}: {error}. The main service needs read/write access to the '
            'existing manager.sqlite3, its SQLite WAL/SHM files and data directory, and traversal of parent directories. '
            'Check the registered data path and permissions; no settings were confirmed.')


def panel(entry, server):
    db = ExistingDatabase(entry['data'])
    settings = model_settings.current(db, InstanceCatalog(db, server))
    keep = model_settings.selection_models(settings)
    return {'name': entry['name'], 'settings': settings,
            'providers': model_settings.picker_providers(server, keep)}


def registered(data, name):
    entry = next((e for e in instances(data) if e['name'] == name), None)
    if not entry:
        raise ValueError('Unknown instance; reopen the panel before editing')
    return entry


def read(data, server, name):
    try:
        return panel(registered(data, name), server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error)) from error


def save(data, server, name, value):
    entry = registered(data, name)
    try:
        # The same validation, revision check, record and audit event as the instance's own picker.
        db = ExistingDatabase(entry['data'])
        model_settings.save(db, InstanceCatalog(db, server), value)
        return panel(entry, server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error)) from error


def environments(data):
    """The harness panel's and model picker's environment selector: Main, then every registered instance
    (deploy/doctor registry)."""
    return [{'name': 'Main', 'main': True}] + [{'name': e['name']} for e in instances(data)]


def harness_scopes(db):
    """Where an instance's manager reads its harness: All Projects plus each live project (a restricted instance's
    manager runs in its one fixed project's scope). The environment setting is written to all of them."""
    return [None] + [row['id'] for row in db.all("SELECT id FROM projects WHERE status!='removed' ORDER BY id")]


def harness_panel(entry, server=None):
    """The same harness controls as Main: manager harness, autonomy and (when the instance has a project) the default
    worker harness. Models are chosen in the model picker, never here."""
    from . import harness
    db = ExistingDatabase(entry['data'])
    scopes = harness_scopes(db)
    state = harness.state(db, scopes[1] if len(scopes) == 2 else None)
    if len(scopes) > 1 and 'worker' not in state:
        state['worker'] = harness.state(db, scopes[1])['worker']
    return {'name': entry['name'], 'harness': {**state, 'project_id': None, 'has_projects': len(scopes) > 1}}


def read_harness(data, server, name):
    entry = registered(data, name)
    try:
        return harness_panel(entry, server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error, 'its settings (unreachable)')) from error


def save_harness(data, server, name, body):
    """Set the instance's manager harness, autonomy and/or default worker harness with the instance's own validation.
    Manager and autonomy go to every chat scope; the worker harness to every project (All Projects has none)."""
    from . import harness
    entry = registered(data, name)
    if not isinstance(body, dict) or not body or body.keys() - {'manager', 'autonomy', 'worker'}:
        raise ValueError('Send manager, autonomy and/or worker')
    try:
        db = ExistingDatabase(entry['data'])
        scopes = harness_scopes(db)
        if 'worker' in body:
            if len(scopes) == 1:
                raise ValueError(f'{name} has no project; the default worker harness is chosen per project')
            harness.get(db, 'worker', body['worker'])  # validate before writing any scope
        for scope in scopes:
            part = body if scope else {k: v for k, v in body.items() if k != 'worker'}
            if part:
                harness.save(db, scope, part)
        return harness_panel(entry, server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error, 'its settings (unreachable)')) from error

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


def listing(data, server):
    result = []
    for entry in instances(data):
        try:
            result.append(panel(entry, server))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            result.append({'name': entry['name'], 'error': access_error(entry['name'], error)})
    return result


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
    """The harness panel's environment picker: Main, then every registered instance (deploy/doctor registry)."""
    return [{'name': 'Main', 'main': True}] + [{'name': e['name']} for e in instances(data)]


def harness_scopes(db):
    """Where an instance's manager reads its harness: All Projects plus each live project (a restricted instance's
    manager runs in its one fixed project's scope). The environment setting is written to all of them."""
    return [None] + [row['id'] for row in db.all("SELECT id FROM projects WHERE status!='removed' ORDER BY id")]


def harness_panel(entry, server):
    from . import harness
    db = ExistingDatabase(entry['data'])
    scopes = harness_scopes(db)
    state = harness.state(db, scopes[1] if len(scopes) == 2 else None)
    state.pop('worker', None)  # the environment picker sets the manager only; workers keep their project defaults
    try:
        models = panel(entry, server)
    except (ValueError, KeyError, TypeError) as error:
        models = {'name': entry['name'], 'error': f'{entry["name"]}: cannot read model settings: {error}'}
    return {'name': entry['name'], 'harness': {**state, 'project_id': None}, 'models': models}


def read_harness(data, server, name):
    entry = registered(data, name)
    try:
        return harness_panel(entry, server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error, 'its settings (unreachable)')) from error


def save_harness(data, server, name, body):
    """Set the instance's manager harness and/or autonomy with the instance's own validation."""
    from . import harness
    entry = registered(data, name)
    if not isinstance(body, dict) or not body or body.keys() - {'manager', 'autonomy'}:
        raise ValueError('Send manager and/or autonomy')
    try:
        db = ExistingDatabase(entry['data'])
        for scope in harness_scopes(db):
            harness.save(db, scope, body)
        return harness_panel(entry, server)
    except (OSError, sqlite3.Error) as error:
        raise ValueError(access_error(name, error, 'its settings (unreachable)')) from error

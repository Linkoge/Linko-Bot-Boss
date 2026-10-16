"""One manager choice and one worker choice, any provider, model and effort the catalog offers, independent of each other.
What the panel shows is what is saved and dispatched: manager turns use the saved manager pair; a new task runs on the saved
worker pair or, when the worker is Automatic (null), on whatever the manager picks for it. `manual` remembers the last fixed
worker so switching Automatic off restores it. A repo lock stores the entire choice; absent locks follow global.
A task keeps the pair it first ran on."""
import copy
import json
import re

from .providers import provider_of

KEY = 'model_choice'


class RouteBlocked(ValueError):
    """No provider turn may start until the retained choice is usable."""


DEFAULT = {'manager': {'effort': 'low'}, 'worker': {'effort': 'medium'}}  # no model yet: the provider's default model


def active(db, project_id=None):
    """The manager's provider: account usage and the provider-less default follow it."""
    return provider_of(saved(db, project_id)['manager'].get('model'))


def saved(db, project_id=None):
    """One resolution rule for both roles: the repo record if present, otherwise global."""
    if project_id:
        repo_key(db, project_id)
        if value := db.setting(KEY + ':' + project_id):
            return value
    return global_choice(db.setting)


def global_choice(setting):
    """Global defaults, including adoption of settings from before the unified picker."""
    if value := setting(KEY):
        return value
    olds = [v for v in (setting('model_settings'), setting('model_settings:claude')) if v]
    if olds:
        old = max(olds, key=lambda v: v['revision'])
        return {'revision': 0, 'manager': old['manager'], 'worker': old['bot']['manual']}
    routes = setting('model_routes') or {}
    return {'revision': 0, **{k: routes.get(r, DEFAULT[k]) for k, r in (('manager', 'interactive'), ('worker', 'execution'))}}


def migrate_repo_locks(conn):
    """Schema 16: consume worker-only overrides once, preserving explicit Automatic and Manual."""
    def setting(key):
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    global_settings = global_choice(setting)
    for key, raw in conn.execute("SELECT key,value FROM settings WHERE key GLOB 'project_worker:*'").fetchall():
        project_id = key.split(':', 1)[1]
        override = json.loads(raw).get('override')
        if override is not None and conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            choice = {**global_settings, **override, 'revision': 0}
            conn.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (KEY + ':' + project_id, json.dumps(choice)))
        conn.execute('DELETE FROM settings WHERE key=?', (key,))


FAMILIES = {'codex': ('Luna', 'Sol', 'Astra'), 'claude': ('Haiku', 'Sonnet', 'Opus')}
PICKER_LEVELS = ('low', 'medium', 'high')
_NAME = re.compile(r'^(?:gpt-(\d+(?:\.\d+)*)-(luna|sol|astra)|claude-(haiku|sonnet|opus)-(\d+(?:-\d+)*))$')


def _family(model):
    if match := _NAME.fullmatch(model):
        return (match[2] or match[3]).title(), tuple(int(n) for n in re.split(r'[.-]', match[1] or match[4]))


def _levels(entry):
    return [e['reasoningEffort'] for e in entry['supportedReasoningEfforts']]


def choices(server, provider, extra=()):
    """What the picker shows (presentation only; dispatch accepts any catalog model): the provider's three families in
    their fixed order, each as its newest installed model with Low/Medium/High, then any other model that is currently
    saved or favorited in `extra`, so a selection made outside the families stays visible and pressed."""
    visible = [m for m in server.catalog if not m.get('hidden') and provider_of(m['model']) == provider]
    result = []
    for name in FAMILIES[provider]:
        found = [(f[1], m) for m in visible if (f := _family(m['model'])) and f[0] == name
                 and 'text' in m.get('inputModalities', ['text', 'image']) and set(_levels(m)) & set(PICKER_LEVELS)]
        best = max(found, key=lambda item: item[0])[1] if found else None  # numeric version, never catalog order
        result.append({'name': name, 'model': best['model'] if best else name,
                       'levels': [e for e in _levels(best) if e in PICKER_LEVELS] if best else []})
    shown = {c['model'] for c in result}
    for m in visible:
        if m['model'] in extra and m['model'] not in shown:
            shown.add(m['model'])
            result.append({'name': m.get('displayName') or m['model'], 'model': m['model'], 'levels': _levels(m)})
    return result


def repo_key(db, project_id):
    if not db.one('SELECT id FROM projects WHERE id=?', (project_id,)):
        raise ValueError('Unknown project')
    return KEY + ':' + project_id


def locked(db, project_id):
    return bool(project_id and db.setting(repo_key(db, project_id)) is not None)


def set_lock(db, project_id, value, server=None):
    """Existence is the toggle: ON copies global; OFF deletes every repo choice."""
    if (not isinstance(value, dict) or set(value) != {'revision', 'locked'}
            or type(value['locked']) is not bool or type(value['revision']) is not int):
        raise ValueError('Invalid repo lock')
    key = repo_key(db, project_id)
    with db.transaction() as conn:
        if saved(db, project_id)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        if value['locked'] and not locked(db, project_id):
            conn.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(current(db, server))))
        elif not value['locked']:
            conn.execute('DELETE FROM settings WHERE key=?', (key,))
        db.event(conn, 'models.repo_lock', value, project_id)


def current(db, server=None, project_id=None):
    """The saved choice; a role saved without a model shows the provider's default model. `manual` is the fixed worker
    Automatic is switched back to."""
    value = copy.deepcopy(saved(db, project_id))
    value['manual'] = value.get('manual') or value['worker'] or DEFAULT['worker']
    for role in ('manager', 'worker', 'manual'):
        if server and value[role] and not value[role].get('model'):
            try:
                value[role] = server.route(None, value[role]['effort'])
            except ValueError:
                pass
    return value


def validate_pair(server, pair):
    if not isinstance(pair, dict) or set(pair) != {'model', 'effort'} or not pair['model'] or not isinstance(pair['model'], str):
        raise ValueError('Choose an exact model and reasoning effort')
    return server.route(**pair)


def save(db, server, value, project_id=None, expected_locked=None):
    if (not isinstance(value, dict) or set(value) - {'manual'} != {'revision', 'manager', 'worker'}
            or type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    new = {'revision': value['revision'] + 1, 'manager': validate_pair(server, value['manager']),
           'worker': None if value['worker'] is None else validate_pair(server, value['worker'])}
    with db.transaction() as conn:
        is_locked = locked(db, project_id)
        if expected_locked is not None and is_locked != expected_locked:
            raise ValueError('Settings scope changed; reopen the panel before editing')
        before = saved(db, project_id)
        if before['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        manual = new['worker'] or before.get('manual') or before['worker'] or DEFAULT['worker']
        try:
            new['manual'] = manual if manual.get('model') else server.route(None, manual['effort'])
        except ValueError:
            new['manual'] = manual
        key = repo_key(db, project_id) if is_locked else KEY
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(new)))
        db.event(conn, 'models.saved', new, project_id if is_locked else None)
    return new


def manager_route(db, project_id=None):
    return saved(db, project_id)['manager']


def delegation(db, server, model=None, effort=None, project_id=None):
    """The pair a new task runs on: the saved worker, or, when the worker is Automatic, the manager's pick
    (the provider's default model and effort for whatever it leaves out)."""
    if worker := current(db, project_id=project_id)['worker']:
        model, effort = worker.get('model'), worker['effort']
    return server.route(model, effort)


def worker_context(db, server, project_id=None):
    """What the manager is told about the worker: the fixed pair, or the catalog to pick from when Automatic."""
    return current(db, server, project_id)['worker'] or {
        'automatic': 'pass model and effort to delegate; leave them out for the provider default',
        'available': {m['model']: [e['reasoningEffort'] for e in m['supportedReasoningEfforts']]
                      for m in server.catalog if not m.get('hidden')}}


def assignment_route(db, server, task):
    """The pair this task runs on, recorded when it is first resolved; a continued task keeps it."""
    row = db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected' ORDER BY seq DESC LIMIT 1", (task['id'],))
    if row:
        selected = json.loads(row['payload'])
    else:
        prior = db.one("SELECT model,effort FROM executions WHERE task_id=? AND model IS NOT NULL ORDER BY started_at LIMIT 1", (task['id'],))
        try:
            selected = prior or delegation(db, server, project_id=task['project_id'])
        except ValueError as error:
            raise RouteBlocked(str(error)) from error
        with db.transaction() as conn:
            db.event(conn, 'task.model_selected', selected, task['project_id'], task['id'])
    pair = {k: selected[k] for k in ('model', 'effort')}
    try:
        server.route(**pair)
    except ValueError as error:
        raise RouteBlocked(str(error)) from error
    return pair


def favorites(db, server):
    """Saved manager favorites projected to the current catalog without silently replacing unavailable pairs."""
    result = []
    for slot in (db.setting('manager_favorites', [None, None]) + [None, None])[:2]:
        item = copy.deepcopy(slot)
        if item:
            try:
                item['pair'] = validate_pair(server, item['pair'])
                item['available'] = True
            except (ValueError, KeyError):
                item['available'] = False
        result.append(item)
    return result


def insert_favorite(db, server, slot, project_id=None):
    if slot not in (0, 1):
        raise ValueError('Choose favorite slot 1 or 2')
    pair = validate_pair(server, current(db, server, project_id)['manager'])
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='manager_favorites'").fetchone()
        slots = json.loads(row[0]) if row else [None, None]
        slots[slot] = {'provider': provider_of(pair['model']), 'pair': pair}
        conn.execute("INSERT INTO settings VALUES('manager_favorites',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(slots),))
        db.event(conn, 'models.favorite_inserted', {'slot': slot, **slots[slot]})
    return favorites(db, server)


def recall_favorite(db, server, slot, project_id=None, expected_locked=None):
    if slot not in (0, 1):
        raise ValueError('Choose favorite slot 1 or 2')
    item = favorites(db, server)[slot]
    if not item or not item['available']:
        raise ValueError('Favorite unavailable; insert a supported Manager selection to replace it')
    return save(db, server, {**current(db, server, project_id), 'manager': item['pair']}, project_id, expected_locked)

"""Exact manager/manual pairs and provider-scoped automatic worker pools.

The repo record, when locked, overrides the global record. Automatic pools contain explicit model and effort
lists, so catalog additions never opt themselves in. A task keeps the pair it first ran on.
"""
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
        return {'revision': 0, 'manager': old['manager'],
                'worker': None if old['bot'].get('mode') == 'automatic' else old['bot']['manual'],
                'manual': old['bot']['manual']}
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
    their fixed order, each as its newest installed model with Low/Medium/High, then other families and models currently
    saved, pooled or favorited in `extra`, so a selection made outside the families stays visible and pressed."""
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
        if (m['model'] in extra or not _family(m['model'])) and m['model'] not in shown:
            shown.add(m['model'])
            name = m.get('displayName') or m['model']
            if name in FAMILIES[provider]:
                name = m['model']  # An older saved version must be distinguishable from its newer family button.
            result.append({'name': name, 'model': m['model'], 'levels': _levels(m)})
    for model in sorted(set(extra) - shown):
        if model and provider_of(model) == provider:
            result.append({'name': model, 'model': model, 'levels': []})
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
    # Resolve legacy Automatic before taking the write lock: catalog-backed migration also writes settings.
    global_settings = current(db, server) if value['locked'] else None
    with db.transaction() as conn:
        if saved(db, project_id)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        if value['locked'] and not locked(db, project_id):
            conn.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(global_settings)))
        elif not value['locked']:
            conn.execute('DELETE FROM settings WHERE key=?', (key,))
        db.event(conn, 'models.repo_lock', value, project_id)


def automatic(worker):
    return isinstance(worker, dict) and worker.get('mode') == 'automatic'


def provider_catalog(server, provider):
    return [m for m in server.catalog if not m.get('hidden') and provider_of(m['model']) == provider
            and 'text' in m.get('inputModalities', ['text', 'image'])]


def initial_pool(server, provider):
    entries = provider_catalog(server, provider)
    return {'mode': 'automatic', 'provider': provider,
            'allowed_models': list(dict.fromkeys(m['model'] for m in entries if _levels(m))),
            'allowed_efforts': list(dict.fromkeys(e for m in entries for e in _levels(m)))}


def current(db, server=None, project_id=None):
    """Resolve exact pairs and freeze legacy null Automatic once a provider catalog is available."""
    value = copy.deepcopy(saved(db, project_id))
    value['manual'] = value.get('manual') or (None if automatic(value['worker']) else value['worker']) or DEFAULT['worker']
    legacy_provider = provider_of(value['manual'].get('model') or value['manager'].get('model'))
    for role in ('manager', 'worker', 'manual'):
        if server and value[role] and not automatic(value[role]) and not value[role].get('model'):
            try:
                value[role] = server.route(None, value[role]['effort'])
            except ValueError:
                pass
    if value['worker'] is None and server:
        value['worker'] = initial_pool(server, legacy_provider)
        # Persist only the migration, without adopting implicit defaults or widening this pool on later reads.
        if value['worker']['allowed_models']:
            with db.transaction() as conn:
                before = saved(db, project_id)
                if before['worker'] is None:
                    provider = provider_of((before.get('manual') or {}).get('model') or before['manager'].get('model'))
                    pool = initial_pool(server, provider)
                    if not pool['allowed_models']:
                        return {**value, 'worker': pool}
                    key = repo_key(db, project_id) if locked(db, project_id) else KEY
                    migrated = {**before, 'worker': pool}
                    conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                 (key, json.dumps(migrated)))
            return current(db, server, project_id)
    return value


def validate_pool(server, worker):
    if (not isinstance(worker, dict) or set(worker) != {'mode', 'provider', 'allowed_models', 'allowed_efforts'}
            or worker['mode'] != 'automatic' or not isinstance(worker['provider'], str)
            or worker['provider'] not in FAMILIES):
        raise ValueError('Choose an automatic worker provider and pool')
    for key in ('allowed_models', 'allowed_efforts'):
        values = worker[key]
        if (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('Automatic worker pool needs at least one model and one effort, without duplicates')
    entries = provider_catalog(server, worker['provider'])
    models = {m['model'] for m in entries}
    efforts = {e for m in entries for e in _levels(m)}
    if set(worker['allowed_models']) - models:
        raise ValueError('Automatic worker pool models must be available from the selected provider')
    if set(worker['allowed_efforts']) - efforts:
        raise ValueError('Automatic worker pool efforts must be available from the selected provider')
    return copy.deepcopy(worker)


def selection_models(settings):
    """Keep every saved model visible, including older automatic pool members."""
    result = {p['model'] for k in ('manager', 'worker', 'manual') if (p := settings.get(k)) and p.get('model')}
    if automatic(settings['worker']):
        result.update(settings['worker']['allowed_models'])
    return result


def picker_providers(server, extra=()):
    """Exact-model choices plus the independent provider effort toggles, including unlit catalog additions."""
    return {p: {'choices': choices(server, p, extra), 'efforts': initial_pool(server, p)['allowed_efforts']}
            for p in FAMILIES}


def validate_pair(server, pair):
    if not isinstance(pair, dict) or set(pair) != {'model', 'effort'} or not pair['model'] or not isinstance(pair['model'], str):
        raise ValueError('Choose an exact model and reasoning effort')
    return server.route(**pair)


def save(db, server, value, project_id=None, expected_locked=None):
    if (not isinstance(value, dict) or set(value) - {'manual'} != {'revision', 'manager', 'worker'}
            or type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    new = {'revision': value['revision'] + 1, 'manager': validate_pair(server, value['manager']),
           'worker': validate_pool(server, value['worker']) if automatic(value['worker']) else validate_pair(server, value['worker'])}
    with db.transaction() as conn:
        is_locked = locked(db, project_id)
        if expected_locked is not None and is_locked != expected_locked:
            raise ValueError('Settings scope changed; reopen the panel before editing')
        before = saved(db, project_id)
        if before['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        manual = (before.get('manual') or (None if automatic(before['worker']) else before['worker']) or DEFAULT['worker']
                  if automatic(new['worker']) else new['worker'])
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
    """Validate explicit picks; deterministically fill omissions from the saved pool only."""
    worker = current(db, server, project_id)['worker']
    if not automatic(worker):
        return server.route(worker.get('model'), worker['effort'])
    models, efforts = worker['allowed_models'], worker['allowed_efforts']
    pool = f"{worker['provider']} models: {', '.join(models)}; efforts: {', '.join(efforts)}"
    if not models or not efforts:
        raise ValueError('Automatic worker pool is empty; choose at least one model and one effort in settings')
    if model is not None and model not in models:
        raise ValueError(f"{model} is not in the automatic worker pool: {pool}")
    if effort is not None and effort not in efforts:
        raise ValueError(f"{effort} is not in the automatic worker pool: {pool}")
    for candidate in ([model] if model is not None else models):
        for level in ([effort] if effort is not None else efforts):
            try:
                return server.route(candidate, level)
            except ValueError:
                if model is not None and effort is not None:
                    raise  # Explicit unsupported combinations are never silently replaced.
    raise ValueError(f'No supported execution pair in the automatic worker pool: {pool}')


def worker_context(db, server, project_id=None):
    worker = current(db, server, project_id)['worker']
    if automatic(worker):
        return {**worker, 'automatic': 'Pick the cheapest fit from this provider and allowed pool only; pass model and '
                'effort to delegate. Omitted picks are filled deterministically from this pool.'}
    return worker


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

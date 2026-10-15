"""One manager choice and one worker choice, any provider, model and effort the catalog offers. What the panel shows is
what is saved and dispatched: manager turns use the saved manager pair; a new task runs on the saved worker pair, or, when
the worker is Automatic (null), on whatever the manager picks for it. A task keeps the pair it first ran on."""
import copy
import json

from .providers import provider_of

KEY = 'model_choice'


class RouteBlocked(ValueError):
    """No provider turn may start until the retained choice is usable."""


DEFAULT = {'manager': {'effort': 'low'}, 'worker': {'effort': 'medium'}}  # no model yet: the provider's default model


def active(db):
    """The manager's provider: account usage and the provider-less default follow it."""
    return provider_of(saved(db)['manager'].get('model'))


def saved(db):
    """The saved choice; first run after the one-choice change adopts the most recently edited old per-provider store."""
    if value := db.setting(KEY):
        return value
    olds = [v for v in (db.setting('model_settings'), db.setting('model_settings:claude')) if v]
    if olds:
        old = max(olds, key=lambda v: v['revision'])
        return {'revision': 0, 'manager': old['manager'], 'worker': old['bot']['manual']}
    routes = db.setting('model_routes', {})
    return {'revision': 0, **{k: routes.get(r, DEFAULT[k]) for k, r in (('manager', 'interactive'), ('worker', 'execution'))}}


def choices(server, provider):
    """Every visible catalog model of the provider with the efforts it supports."""
    return [{'name': m.get('displayName') or m['model'], 'model': m['model'],
             'levels': [e['reasoningEffort'] for e in m['supportedReasoningEfforts']]}
            for m in server.catalog if not m.get('hidden') and provider_of(m['model']) == provider]


def current(db, server=None):
    """The saved choice; a role saved without a model shows the provider's default model."""
    value = copy.deepcopy(saved(db))
    for role in ('manager', 'worker'):
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


def save(db, server, value):
    if (not isinstance(value, dict) or set(value) != {'revision', 'manager', 'worker'}
            or type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    new = {'revision': value['revision'] + 1, 'manager': validate_pair(server, value['manager']),
           'worker': None if value['worker'] is None else validate_pair(server, value['worker'])}
    with db.transaction() as conn:
        if saved(db)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, json.dumps(new)))
        db.event(conn, 'models.saved', new)
    return new


def manager_route(db):
    return saved(db)['manager']


def delegation(db, server, model=None, effort=None):
    """The pair a new task runs on: the saved worker, or, when the worker is Automatic, the manager's pick
    (the provider's default model and effort for whatever it leaves out)."""
    if worker := saved(db)['worker']:
        model, effort = worker.get('model'), worker['effort']
    return server.route(model, effort)


def worker_context(db, server):
    """What the manager is told about the worker: the fixed pair, or the catalog to pick from when Automatic."""
    return current(db, server)['worker'] or {
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
            selected = prior or delegation(db, server)
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


def insert_favorite(db, server, slot):
    if slot not in (0, 1):
        raise ValueError('Choose favorite slot 1 or 2')
    pair = validate_pair(server, current(db, server)['manager'])
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='manager_favorites'").fetchone()
        slots = json.loads(row[0]) if row else [None, None]
        slots[slot] = {'provider': provider_of(pair['model']), 'pair': pair}
        conn.execute("INSERT INTO settings VALUES('manager_favorites',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(slots),))
        db.event(conn, 'models.favorite_inserted', {'slot': slot, **slots[slot]})
    return favorites(db, server)


def recall_favorite(db, server, slot):
    if slot not in (0, 1):
        raise ValueError('Choose favorite slot 1 or 2')
    item = favorites(db, server)[slot]
    if not item or not item['available']:
        raise ValueError('Favorite unavailable; insert a supported Manager selection to replace it')
    return save(db, server, {**current(db, server), 'manager': item['pair']})

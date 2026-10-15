"""One manager choice and one worker choice, any provider/model/effort. What the panel shows is what is saved and
dispatched: new tasks pin the saved worker pair, manager turns use the saved manager pair."""
import copy
import json

from .model_families import PROVIDER_FAMILIES, family, latest, resolve
from .providers import provider_of

KEY = 'model_choice'


class RouteBlocked(ValueError):
    """No provider turn may start until the retained choice is usable."""


LEVELS = ('low', 'medium', 'high')
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
    result = []
    for name in PROVIDER_FAMILIES[provider]:
        try:
            model = latest(server, name)
        except ValueError:
            model = name  # Stable unavailable family label; never a version fallback.
        levels = [e['reasoningEffort'] for e in next(
            (m['supportedReasoningEfforts'] for m in server.catalog if m['model'] == model and not m.get('hidden')), [])
            if e['reasoningEffort'] in LEVELS]
        result.append({'name': name, 'model': model, 'levels': levels})
    return result


def current(db, server=None):
    """The saved choice with families projected to the newest installed model (kept visible when unavailable)."""
    value = copy.deepcopy(saved(db))
    for role in ('manager', 'worker'):
        pair = value[role]
        if not server:
            continue
        if not pair.get('model'):
            try:
                value[role] = server.route(None, pair['effort'])
            except ValueError:
                pass
        elif name := family(pair['model']):
            try:
                pair['model'] = latest(server, name)
            except ValueError:
                pair['model'] = name
    return value


def validate_pair(server, pair):
    if not isinstance(pair, dict) or set(pair) != {'model', 'effort'}:
        raise ValueError('Choose an exact model and reasoning pair')
    if not isinstance(pair['model'], str) or not family(pair['model']) or pair['effort'] not in LEVELS:
        raise ValueError('Choose a Codex or Claude model and Low, Medium or High')
    return resolve(server, pair)


def save(db, server, value):
    if (not isinstance(value, dict) or set(value) != {'revision', 'manager', 'worker'}
            or type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    new = {'revision': value['revision'] + 1, 'manager': validate_pair(server, value['manager']),
           'worker': validate_pair(server, value['worker'])}
    with db.transaction() as conn:
        if saved(db)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, json.dumps(new)))
        # Other readers (chat routes, memory review) take the two roles from here.
        routes = {'interactive': new['manager'], 'execution': new['worker']}
        conn.execute("INSERT INTO settings VALUES('model_routes',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(routes),))
        db.event(conn, 'models.saved', new)
    return new


def manager_route(db):
    return saved(db)['manager']


def delegation(db, server):
    """The worker pair a new task pins: exactly the saved worker."""
    return {**resolve(server, saved(db)['worker']), 'mode': 'manual', 'settings_revision': saved(db)['revision']}


def assignment_route(db, server, task):
    """The pair pinned to this task (its first dispatch decides); a continued task keeps it."""
    row = db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected' ORDER BY seq DESC LIMIT 1", (task['id'],))
    if row:
        selected = json.loads(row['payload'])
    else:
        prior = db.one("SELECT model,effort FROM executions WHERE task_id=? AND model IS NOT NULL ORDER BY started_at LIMIT 1", (task['id'],))
        try:
            selected = {**prior, 'mode': 'manual', 'settings_revision': 0} if prior else delegation(db, server)
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

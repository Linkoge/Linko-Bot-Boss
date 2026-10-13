"""Global role choices and bot policy, using existing settings and task audit events."""
import json


class RouteBlocked(ValueError):
    """No provider turn may start until the retained choice is usable."""


LEVELS = ('low', 'medium', 'high')
# Exact official IDs; availability and efforts still come from model/list.
MODELS = {'Luna': 'gpt-6-luna', 'Sol': 'gpt-6.1-sol', 'Astra': 'gpt-6-astra'}


def choices(server):
    return [{'name': name, 'model': model, 'levels': [e['reasoningEffort'] for e in
             next((m['supportedReasoningEfforts'] for m in server.catalog if m['model'] == model), [])
             if e['reasoningEffort'] in LEVELS]} for name, model in MODELS.items()]


def current(db, server):
    saved = db.setting('model_settings')
    if saved:
        return saved
    routes = db.setting('model_routes', {})
    return {'revision': 0, 'manager': server.route(**routes.get('interactive', {'effort': 'low'})),
            'bot': {'mode': 'manual', 'manual': server.route(**routes.get('execution', {'effort': 'medium'})),
                    'policy': {m['model']: {'allowed': m['name'] != 'Astra',
                               'levels': {e: e in m['levels'] for e in LEVELS}} for m in choices(server)}}}


def validate_pair(server, pair):
    if not isinstance(pair, dict) or set(pair) != {'model', 'effort'}:
        raise ValueError('Choose an exact model and reasoning pair')
    if pair['model'] not in MODELS.values() or pair['effort'] not in LEVELS:
        raise ValueError('Choose Luna, Sol or Astra and Low, Medium or High')
    return server.route(**pair)


def permitted(config, server):
    policy = config['bot']['policy']
    return [{'model': m['model'], 'effort': e} for m in choices(server) for e in m['levels']
            if policy[m['model']]['allowed'] and policy[m['model']]['levels'][e]]


def save(db, server, value):
    if (set(value) != {'revision', 'manager', 'bot'} or not isinstance(value['bot'], dict)
            or set(value['bot']) != {'mode', 'manual', 'policy'} or type(value['revision']) is not int
            or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    validate_pair(server, value['manager'])
    validate_pair(server, value['bot']['manual'])
    if value['bot']['mode'] not in {'manual', 'automatic'}:
        raise ValueError('Choose Manual or Automatic')
    policy = value['bot']['policy']
    if not isinstance(policy, dict) or set(policy) != set(MODELS.values()):
        raise ValueError('Policy must include every model')
    for item in policy.values():
        if (not isinstance(item, dict) or set(item) != {'allowed', 'levels'} or not isinstance(item['levels'], dict)
                or type(item['allowed']) is not bool
                or set(item['levels']) != set(LEVELS) or any(type(v) is not bool for v in item['levels'].values())):
            raise ValueError('Each model and level needs an allow or deny state')
    if not permitted(value, server):
        raise ValueError('Allow at least one supported model and level combination')
    with db.transaction() as conn:
        if current(db, server)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        value = {**value, 'revision': value['revision'] + 1}
        conn.execute("INSERT INTO settings VALUES('model_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(value),))
        # Keep historical integrations reading the role routes compatible.
        routes = {'interactive': value['manager'], 'execution': value['bot']['manual']}
        conn.execute("INSERT INTO settings VALUES('model_routes',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(routes),))
        db.event(conn, 'models.saved', value)
    return value


def delegation(db, server, requested=None):
    if not db.setting('model_settings'):
        pair = server.route(**db.setting('model_routes', {}).get('execution', {'effort': 'medium'}))
        return {**pair, 'mode': 'manual', 'settings_revision': 0}
    config = current(db, server)
    if config['bot']['mode'] == 'manual':
        pair = config['bot']['manual']
        server.route(**pair)  # Retained legacy model is never silently replaced.
    else:
        pair = validate_pair(server, requested)
        if pair not in permitted(config, server):
            raise ValueError('Bot pair is denied; choose from permitted pairs: ' + json.dumps(permitted(config, server)))
    return {**pair, 'mode': config['bot']['mode'], 'settings_revision': config['revision']}


def assignment_route(db, server, task):
    saved = db.one("SELECT payload FROM events WHERE task_id=? AND kind='task.model_selected' ORDER BY seq DESC LIMIT 1",
                   (task['id'],))
    if saved:
        selected = json.loads(saved['payload'])
    else:
        # Pre-feature/manual API assignments pin their first actual dispatch. Automatic
        # work lacking a manager selection waits rather than inventing a route.
        prior = db.one("SELECT model,effort FROM executions WHERE task_id=? AND model IS NOT NULL ORDER BY started_at LIMIT 1",
                       (task['id'],))
        if prior:
            selected = {**prior, 'mode': 'manual', 'settings_revision': 0}
        else:
            try:
                selected = delegation(db, server)
            except ValueError as error:
                raise RouteBlocked(str(error)) from error
        with db.transaction() as conn:
            db.event(conn, 'task.model_selected', selected, task['project_id'], task['id'])
    pair = {k: selected[k] for k in ('model', 'effort')}
    try:
        server.route(**pair)
    except ValueError as error:
        raise RouteBlocked(str(error)) from error
    if selected['mode'] == 'automatic' and pair not in permitted(current(db, server), server):
        raise RouteBlocked('Saved bot pair is now denied or unavailable; enable it before resuming this assignment')
    return pair

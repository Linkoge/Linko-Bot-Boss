"""Global role choices and bot policy, using existing settings and task audit events."""
import copy
import json

from .model_families import PROVIDER_FAMILIES, family, latest, policy_item, resolve


class RouteBlocked(ValueError):
    """No provider turn may start until the retained choice is usable."""


LEVELS = ('low', 'medium', 'high')
PROVIDERS = tuple(PROVIDER_FAMILIES)
# Routing guidance per provider: cheapest, workhorse, strongest reasoning.
GUIDES = {'codex': ('Luna', 'Sol', 'Astra'), 'claude': ('Haiku', 'Sonnet', 'Opus')}
# Legacy IDs for compatibility consumers only. Runtime selection uses authenticated catalog families.
MODELS = {'Luna': 'gpt-6-luna', 'Sol': 'gpt-6.1-sol', 'Astra': 'gpt-6-astra'}


def active(db):
    provider = db.setting('model_provider', 'codex')
    return provider if provider in PROVIDERS else 'codex'


def provider_of(model):
    name = family(model)
    return next((p for p, names in PROVIDER_FAMILIES.items() if name in names), 'codex')


def key(provider):
    return 'model_settings' if provider == 'codex' else 'model_settings:' + provider


def set_active(db, provider):
    if provider not in PROVIDERS:
        raise ValueError('Choose Codex or Claude')
    with db.transaction() as conn:
        conn.execute("INSERT INTO settings VALUES('model_provider',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(provider),))
        db.event(conn, 'models.provider', {'provider': provider})
    return provider


def manager_route(db):
    provider = active(db)
    saved = db.setting(key(provider))
    if saved:
        return saved['manager']
    if provider == 'claude':
        return {'model': 'Sonnet', 'effort': 'low'}
    return db.setting("model_routes", {}).get("interactive", {"effort": "low"})


def choices(server, provider='codex'):
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


def current(db, server, provider=None):
    provider = provider or active(db)
    saved = db.setting(key(provider))
    if saved:
        saved = copy.deepcopy(saved)
        for pair in (saved['manager'], saved['bot']['manual']):
            name = family(pair['model'])
            if name:
                try:
                    pair['model'] = latest(server, name)
                except ValueError:
                    pair['model'] = name  # Retain the chosen family visibly unavailable.
        policy = saved['bot']['policy']
        saved['bot']['policy'] = {m['model']: policy_item(policy, m['model']) for m in choices(server, provider)}
        return saved
    if provider == 'claude':
        # Claude starts on Automatic with every supported pair available.
        def pair(effort):
            try:
                return resolve(server, {'model': 'Sonnet', 'effort': effort})
            except ValueError:
                return {'model': 'Sonnet', 'effort': effort}  # Visible but unavailable until Claude starts.
        return {'revision': 0, 'manager': pair('low'),
                'bot': {'mode': 'automatic', 'manual': pair('medium'),
                        'policy': {m['model']: {'allowed': True, 'levels': {e: e in m['levels'] for e in LEVELS}}
                                   for m in choices(server, provider)}}}
    routes = db.setting('model_routes', {})
    return {'revision': 0, 'manager': resolve(server, routes.get('interactive', {'effort': 'low'})),
            'bot': {'mode': 'manual', 'manual': resolve(server, routes.get('execution', {'effort': 'medium'})),
                    'policy': {m['model']: {'allowed': m['name'] != 'Astra',
                               'levels': {e: e in m['levels'] for e in LEVELS}} for m in choices(server)}}}


def validate_pair(server, pair, provider='codex'):
    if not isinstance(pair, dict) or set(pair) != {'model', 'effort'}:
        raise ValueError('Choose an exact model and reasoning pair')
    names = PROVIDER_FAMILIES[provider]
    if not isinstance(pair['model'], str) or family(pair['model']) not in names or pair['effort'] not in LEVELS:
        raise ValueError(f'Choose {", ".join(names[:-1])} or {names[-1]} and Low, Medium or High')
    return resolve(server, pair)


def permitted(config, server, provider=None):
    provider = provider or provider_of(config['manager']['model'])
    policy = config['bot']['policy']
    return [{'model': m['model'], 'effort': e} for m in choices(server, provider) for e in m['levels']
            if policy_item(policy, m['model'])['allowed'] and policy_item(policy, m['model'])['levels'][e]]


def guidelines(provider):
    small, work, strong = GUIDES[provider]
    return ('Before delegate in Automatic choose worker_route from permitted_pairs by difficulty. '
            f'Simple: {small} or {work} Low. Ordinary: {work} Medium. Hard: {work} High or {strong}. '
            'Manual uses the saved pair; never override it. No extra routing call. '
            'Existing assignments retain their selected pair on continue.')


def save(db, server, value):
    value = dict(value)
    provider = value.pop('provider', 'codex')
    if provider not in PROVIDERS:
        raise ValueError('Choose Codex or Claude')
    families = PROVIDER_FAMILIES[provider]
    if (set(value) != {'revision', 'manager', 'bot'} or not isinstance(value['bot'], dict)
            or set(value['bot']) != {'mode', 'manual', 'policy'} or type(value['revision']) is not int
            or value['revision'] < 0):
        raise ValueError('Invalid model settings')
    validate_pair(server, value['manager'], provider)
    validate_pair(server, value['bot']['manual'], provider)
    if value['bot']['mode'] not in {'manual', 'automatic'}:
        raise ValueError('Choose Manual or Automatic')
    policy = value['bot']['policy']
    if not isinstance(policy, dict) or len(policy) != len(families) or {family(k) for k in policy} != set(families):
        raise ValueError('Policy must include every model')
    for item in policy.values():
        if (not isinstance(item, dict) or set(item) != {'allowed', 'levels'} or not isinstance(item['levels'], dict)
                or type(item['allowed']) is not bool
                or set(item['levels']) != set(LEVELS) or any(type(v) is not bool for v in item['levels'].values())):
            raise ValueError('Each model and level needs an allow or deny state')
    if not permitted(value, server, provider):
        raise ValueError('Allow at least one supported model and level combination')
    with db.transaction() as conn:
        if current(db, server, provider)['revision'] != value['revision']:
            raise ValueError('Settings changed; reopen the panel before editing')
        value = {**value, 'revision': value['revision'] + 1}
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key(provider), json.dumps(value)))
        if provider == 'codex':
            # Keep historical integrations reading the role routes compatible.
            routes = {'interactive': value['manager'], 'execution': value['bot']['manual']}
            conn.execute("INSERT INTO settings VALUES('model_routes',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps(routes),))
        db.event(conn, 'models.saved', {**value, 'provider': provider})
    return value


def delegation(db, server, requested=None):
    provider = active(db)
    if provider == 'codex' and not db.setting('model_settings'):
        pair = resolve(server, db.setting('model_routes', {}).get('execution', {'effort': 'medium'}))
        return {**pair, 'mode': 'manual', 'settings_revision': 0}
    config = current(db, server, provider)
    if config['bot']['mode'] == 'manual':
        pair = resolve(server, config['bot']['manual'])
    else:
        pair = validate_pair(server, requested, provider)
        allowed = permitted(config, server, provider)
        if pair not in allowed:
            raise ValueError('Bot pair is denied; choose from permitted pairs: ' + json.dumps(allowed))
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
    if selected['mode'] == 'automatic':
        item = policy_item(current(db, server, provider_of(pair['model']))['bot']['policy'], pair['model'])
        if not item['allowed'] or not item['levels'].get(pair['effort'], False):
            raise RouteBlocked('Saved bot pair is now denied or unavailable; enable it before resuming this assignment')
    return pair

"""Resolve only recognized, visible provider families; never guess an alias."""
import re

FAMILIES = ('Luna', 'Sol', 'Astra')
_PATTERN = re.compile(r'^gpt-(\d+(?:\.\d+)*)-(luna|sol|astra)$')


def identity(model):
    match = _PATTERN.fullmatch(model or '')
    return (match[2].title(), tuple(int(n) for n in match[1].split('.'))) if match else None


def family(model):
    if model in FAMILIES:
        return model
    found = identity(model)
    return found[0] if found else None


def latest(server, name):
    candidates = [m for m in server.catalog if not m.get('hidden') and identity(m['model'])
                  and family(m['model']) == name and 'text' in m.get('inputModalities', ['text', 'image'])
                  and any(e['reasoningEffort'] in ('low', 'medium', 'high') for e in m['supportedReasoningEfforts'])]
    if not candidates:
        raise ValueError(f'{name} model is not in the installed Codex catalog')
    # Numeric ordering, never catalog order, lexicographic sorting or cross-family fallback.
    return max(candidates, key=lambda m: identity(m['model'])[1])['model']


def resolve(server, pair):
    name = family(pair.get('model'))
    # Unrecognized legacy routes stay exact, including fixture/custom models.
    return server.route(model=latest(server, name) if name else pair.get('model'), effort=pair.get('effort'))


def policy_item(policy, model):
    name = family(model)
    items = [item for key, item in policy.items() if family(key) == name and name]
    if not items:
        return {'allowed': False, 'levels': {e: False for e in ('low', 'medium', 'high')}}
    # Conflicting legacy keys cannot erase a deny during migration.
    return {'allowed': all(i['allowed'] for i in items),
            'levels': {e: all(i['levels'][e] for i in items) for e in ('low', 'medium', 'high')}}

"""Saved machines (other Bot Boss instances) for the header menu's one-click instance switcher.

The list lives in this instance's settings table; each instance keeps its own login and its own list.
"""
import re
import uuid
from urllib.parse import urlsplit

KEY = 'machines'
MAX_MACHINES = 20
_CONTROL = re.compile(r'[\x00-\x20\x7f]')


def clean_url(value) -> str:
    """Normalise an http(s) URL to scheme://host[:port][/path]; reject anything else."""
    if not isinstance(value, str):
        raise ValueError('URL must be text')
    value = value.strip()
    if not value or len(value) > 300 or _CONTROL.search(value) or '\\' in value:
        raise ValueError('Enter a valid http or https URL')
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError:
        raise ValueError('Enter a valid http or https URL') from None
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Enter a valid http or https URL')
    if parts.fragment or parts.query:
        raise ValueError('URL must not contain a query or fragment')
    return f'{parts.scheme}://{parts.netloc}{parts.path.rstrip("/")}'


def clean(items) -> list[dict]:
    """Validate a full replacement list; ids are kept or assigned, names and URLs must be unique."""
    if not isinstance(items, list) or len(items) > MAX_MACHINES:
        raise ValueError(f'Provide a list of at most {MAX_MACHINES} machines')
    out, names, urls = [], set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Each machine needs a name and a URL')
        name = item.get('name')
        if not isinstance(name, str) or not (name := ' '.join(name.split())) or len(name) > 60:
            raise ValueError('Name must be 1-60 characters')
        url = clean_url(item.get('url'))
        if name.lower() in names or url.lower() in urls:
            raise ValueError('Each machine needs a unique name and URL')
        names.add(name.lower())
        urls.add(url.lower())
        ident = item.get('id')
        out.append({'id': ident if isinstance(ident, str) and re.fullmatch(r'[0-9a-f]{12}', ident) else uuid.uuid4().hex[:12],
                    'name': name, 'url': url})
    return out


def read(db) -> list[dict]:
    return db.setting(KEY, [])


def save(db, items) -> list[dict]:
    machines = clean(items)
    db.set_setting(KEY, machines)
    return machines

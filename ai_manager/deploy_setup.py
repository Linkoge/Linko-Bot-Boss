"""Saved deploy setup contract, also used by the standalone host consumer (stdlib only)."""
from urllib.parse import urlsplit


def validate_setup(value):
    if not isinstance(value, dict):
        raise ValueError('Deploy setup must be an object')
    allowed = {'deploy_command', 'service', 'backup_command', 'health_url', 'health_command', 'rollback_command'}
    if set(value) - allowed:
        raise ValueError('Unsupported deploy setup fields')
    for field in ('deploy_command', 'backup_command', 'health_command', 'rollback_command'):
        if field in value and (not isinstance(value[field], list) or not value[field]
                               or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in value[field])):
            raise ValueError(field + ' must be a nonempty command argument array')
    if bool(value.get('deploy_command')) == bool(value.get('service')):
        raise ValueError('Provide one deploy_command or service')
    if 'service' in value and (not isinstance(value['service'], str) or not value['service'].strip()
                               or value['service'].startswith('-') or any(c.isspace() for c in value['service'])):
        raise ValueError('service must be a user systemd service name')
    if bool(value.get('health_url')) == bool(value.get('health_command')):
        raise ValueError('Provide one mandatory health_url or health_command')
    if 'health_url' in value:
        if not isinstance(value['health_url'], str):
            raise ValueError('health_url must be HTTP(S)')
        url = urlsplit(value['health_url'])
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            raise ValueError('health_url must be HTTP(S) without credentials')
    for field in ('backup_command', 'rollback_command'):
        if not value.get(field):
            raise ValueError(field + ' is mandatory')
    return value.copy()

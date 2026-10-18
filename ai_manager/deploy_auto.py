"""Automatic deploy setup: the repo file `.linko-deploy.json` seeds a project's one saved setup exactly once.

The saved setup stays the single source of truth. A project without one gets a "Create deploy setup" worker task after
a passing merge; the worker commits `.linko-deploy.json`, and the merge that brings it in validates and saves it. The
file is either a setup in the schema of deploy_setup.validate_setup or {"none": true, "reason": "..."}.
"""
import hashlib
import json
from pathlib import Path

from .deploy_setup import validate_setup

SETUP_FILE = '.linko-deploy.json'
TITLE = 'Create deploy setup'
MAX_BYTES = 20000
OBJECTIVE = TITLE + '''

This project has no saved deploy setup, so passing work only merges and never goes live. Make deployment automatic.
Inspect how the project actually runs (systemd user units, release directories, ports, static sites, scheduled jobs)
from the repository and its documentation. Then:
- Add backup, deploy, health-check and rollback scripts in the repository, with permanent tests that use disposable
  directories and fake systemctl/curl shims. Never touch the live host, restart services or deploy from here.
- Never write credential values into any file; read them from environment variables or ignored files.
- Write `.linko-deploy.json` at the repository root: one JSON object with argv arrays (lists of strings) for
  `deploy_command` (or `service`, a user systemd unit name), `backup_command`, `health_command` (or `health_url`, an
  HTTP(S) URL without credentials) and `rollback_command`. Backup, health and rollback are mandatory. Relative script
  paths run from the repository root. Example shape:
  {"deploy_command": ["scripts/deploy.sh"], "backup_command": ["scripts/backup.sh"],
   "health_command": ["scripts/health.sh"], "rollback_command": ["scripts/rollback.sh"]}
- If the project has nothing deployable (a library, docs-only or empty repository), write instead
  {"none": true, "reason": "<one plain sentence>"} and nothing else is needed.
The application validates the file when this work merges, saves it as the project's deploy setup and deploys.'''
ACCEPTANCE = ('`.linko-deploy.json` exists and is valid, scripts have permanent tests on disposable directories, '
              'no credential values were written.')


def read_proposal(root):
    """What the merged main checkout proposes: (state, value, digest); state is absent | invalid | none | setup."""
    path = Path(root) / SETUP_FILE
    if not path.is_file():
        return 'absent', None, None
    try:
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError(SETUP_FILE + ' is larger than ' + str(MAX_BYTES) + ' bytes')
        value = json.loads(data)
        digest = hashlib.sha256(data).hexdigest()
        if isinstance(value, dict) and 'none' in value:
            if value['none'] is not True or set(value) - {'none', 'reason'}:
                raise ValueError('none must be true with only an optional reason')
            reason = value.get('reason')
            if reason is not None and not isinstance(reason, str):
                raise ValueError('reason must be a string')
            return 'none', (reason or 'no reason given').strip()[:300], digest
        return 'setup', validate_setup(value), digest
    except (OSError, ValueError) as error:
        return 'invalid', str(error)[:300], None

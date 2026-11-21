"""Main's global settings are the settings of every instance.

Main is the only place the owner edits the global model choice, the favorites, the shown providers and the chat memory
(compaction model, effort, context threshold). An instance (a restricted one, usually another Linux user) rejects those
writes and cannot read Main's database, so Main copies the values into each registered instance's own manager.sqlite3
(the access Main already has for the Environment picker): after every Main save and once at Main's startup. An
instance reads its own copy, so an unreachable or stopped Main leaves the last copy (or the defaults) in effect."""
import json
import logging
import sqlite3

from . import model_settings
from .instance_models import ExistingDatabase, instances

KEYS = ('manager_favorites', model_settings.VISIBILITY, 'chat_compaction')
log = logging.getLogger(__name__)


def values(db):
    """What Main resolves now; None means "unset on Main": the instance's copy is removed so its defaults apply."""
    return {k: db.setting(k) for k in (model_settings.KEY, *KEYS)}


def apply(target, wanted):
    with target.transaction() as conn:
        for key, value in wanted.items():
            if value is None:
                conn.execute('DELETE FROM settings WHERE key=?', (key,))
            else:
                conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (key, json.dumps(value)))
        # A per-project lock would override the global choice Main sets.
        conn.execute("DELETE FROM settings WHERE key LIKE 'model\\_choice:%' ESCAPE '\\'")


def push(main_db, data_dir):
    """Copy Main's values into every registered instance; returns the names that could not be reached."""
    failed = []
    try:
        entries, wanted = instances(data_dir), values(main_db)
    except (ValueError, sqlite3.Error) as error:
        log.warning('instance_sync.skipped reason=%s', error)
        return failed
    for entry in entries:
        try:
            apply(ExistingDatabase(entry['data']), wanted)
        except (OSError, sqlite3.Error) as error:
            failed.append(entry['name'])
            log.warning('instance_sync.unreachable instance=%s reason=%s', entry['name'], error)
    return failed

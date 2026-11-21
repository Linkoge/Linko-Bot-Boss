"""Access switch of a non-main instance ("Disable access" in Main's harness panel).

Main writes the flag into the instance's own settings table (instance_models.set_access); the instance reads it in its
per-request check, cached for at most TTL seconds. While it is set, every request of that instance gets only
"Error 69" (HTTP 503): sessions are deleted and running chat replies are stopped. Password, chats, memory and files are
never touched; clearing the flag makes the normal login work again at once. Main ignores the flag."""
import sqlite3
import time

from starlette.responses import PlainTextResponse

KEY = 'access_disabled'
BODY = 'Error 69'
TTL = 2.0


def blocked():
    return PlainTextResponse(BODY, status_code=503, headers={'Cache-Control': 'no-store'})


def is_disabled(db):
    return db.setting(KEY, False) is True


def write(db, disabled):
    """Set or clear the flag; disabling also signs everyone out at once (sessions deleted in the same transaction)."""
    with db.transaction() as conn:
        if disabled:
            conn.execute("INSERT INTO settings VALUES(?, 'true') ON CONFLICT(key) DO UPDATE SET value='true'", (KEY,))
            conn.execute('DELETE FROM sessions')
        else:
            conn.execute('DELETE FROM settings WHERE key=?', (KEY,))
        db.event(conn, 'access.disabled' if disabled else 'access.enabled', {})


class Gate:
    """The instance's cached view of its flag. A failed read keeps the last known value (a locked DB never opens
    a disabled instance, nor closes an enabled one)."""
    def __init__(self, db, ttl=TTL, clock=time.monotonic):
        self.db, self.ttl, self.clock = db, ttl, clock
        self.value, self.read_at = False, None

    def disabled(self):
        now = self.clock()
        if self.read_at is None or now - self.read_at >= self.ttl:
            try:
                self.value = is_disabled(self.db)
            except sqlite3.Error:
                pass
            self.read_at = now
        return self.value

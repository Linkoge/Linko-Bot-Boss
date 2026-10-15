"""Lossless cold payloads in SQLite: reference replacement and compression commit together.

Metadata stays in its original row, so IDs, foreign references, ordering and cursors
never change. The archive is part of the normal SQLite backup, including its WAL.
"""
import asyncio
import hashlib
import logging
import sqlite3
import time
import zlib

CHAT_WINDOW, EVENT_WINDOW, AGE_SECONDS, SUMMARY_CHARS = 50, 20, 7 * 86400, 1200
REFERENCE = b'\x00linko-history-v1:'
SCHEMA = """
CREATE TABLE IF NOT EXISTS history_archive(
 digest TEXT PRIMARY KEY, compressed BLOB NOT NULL, preview TEXT,
 original_bytes INTEGER NOT NULL, archived_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_task_history ON events(task_id,seq);
CREATE INDEX IF NOT EXISTS messages_history ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS executions_task_history ON executions(task_id);
CREATE INDEX IF NOT EXISTS executions_agent_history ON executions(agent_id,project_id);
"""


def install(conn):
    """Restore only selected cells; SQL predicates explicitly use history_text()."""
    def archived(value, column):
        if not isinstance(value, bytes) or not value.startswith(REFERENCE):
            return None
        digest = value[len(REFERENCE):].decode('ascii')
        row = conn.execute(f'SELECT {column} FROM history_archive WHERE digest=?', (digest,)).fetchone()
        if row is None:
            raise sqlite3.DatabaseError(f'Missing history archive: {digest}')
        return digest, row[0]

    def restore(value):
        item = archived(value, 'compressed')
        if item is None:
            return value
        digest, compressed = item
        raw = zlib.decompress(compressed)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise sqlite3.DatabaseError(f'Corrupt history archive: {digest}')
        return raw.decode('utf-8')

    def preview(value):
        item = archived(value, 'preview')
        return item[1] if item else (value or '')[-SUMMARY_CHARS:]

    conn.create_function('history_text', 1, restore)
    conn.create_function('history_preview', 1, preview)
    conn.row_factory = lambda cursor, row: sqlite3.Row(cursor, tuple(restore(v) for v in row))


def pack(conn, value, now, summary=False):
    raw = value.encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    conn.execute('''INSERT INTO history_archive VALUES(?,?,?,?,?) ON CONFLICT(digest)
        DO UPDATE SET preview=COALESCE(history_archive.preview,excluded.preview)''',
                 (digest, zlib.compress(raw), value[-SUMMARY_CHARS:] if summary else None, len(raw), now))
    return REFERENCE + digest.encode('ascii')


def maintain(db, now=None, batch=250):
    """One bounded, retryable transaction; never remove records or archive payloads.

    Unfinished turns are exempt. Even pending result deliveries remain hot. Each
    category gets a batch so a large chat cannot starve worker/event retention.
    """
    now = time.time() if now is None else now
    cutoff = now - AGE_SECONDS
    changed = 0
    with db.transaction() as conn:
        messages = conn.execute('''SELECT m.id,m.content FROM messages m
            LEFT JOIN executions e ON e.id=m.execution_id
            WHERE typeof(m.content)='text' AND (m.execution_id IS NULL OR e.id IS NULL
                OR (e.finished_at IS NOT NULL AND COALESCE(e.delivery,'')!='pending'))
            AND m.status NOT IN ('queued','streaming','waiting')
            AND (m.created_at<? OR m.rowid < (SELECT rowid FROM messages
                WHERE conversation_id=m.conversation_id ORDER BY rowid DESC LIMIT 1 OFFSET ?))
            ORDER BY m.rowid LIMIT ?''', (cutoff, CHAT_WINDOW - 1, batch)).fetchall()
        for row in messages:
            conn.execute('UPDATE messages SET content=? WHERE id=?', (pack(conn, row['content'], now), row['id']))
        changed += len(messages)
        events = conn.execute('''SELECT v.seq,v.payload FROM events v
            LEFT JOIN executions e ON e.id=v.execution_id
            WHERE typeof(v.payload)='text' AND (e.id IS NULL OR
                (e.finished_at IS NOT NULL AND COALESCE(e.delivery,'')!='pending'))
            AND (v.created_at<? OR (
                (v.execution_id IS NULL OR v.seq < (SELECT seq FROM events
                    WHERE execution_id=v.execution_id ORDER BY seq DESC LIMIT 1 OFFSET ?))
                AND (v.task_id IS NULL OR v.seq < (SELECT seq FROM events
                    WHERE task_id=v.task_id ORDER BY seq DESC LIMIT 1 OFFSET ?))
                AND (v.execution_id IS NOT NULL OR v.task_id IS NOT NULL OR v.seq <
                    (SELECT seq FROM events ORDER BY seq DESC LIMIT 1 OFFSET ?))))
            ORDER BY v.seq LIMIT ?''', (cutoff, EVENT_WINDOW - 1, EVENT_WINDOW - 1, EVENT_WINDOW - 1, batch)).fetchall()
        for row in events:
            conn.execute('UPDATE events SET payload=? WHERE seq=?', (pack(conn, row['payload'], now), row['seq']))
        changed += len(events)
        executions = conn.execute('''SELECT e.id,e.input,e.launch_input,e.output,e.error FROM executions e
            WHERE e.finished_at IS NOT NULL AND COALESCE(e.delivery,'')!='pending'
            AND ((typeof(e.output)='text' AND e.output!='') OR (typeof(e.input)='text' AND e.input!='')
                OR (typeof(e.launch_input)='text' AND e.launch_input!='') OR (typeof(e.error)='text' AND e.error!=''))
            AND (e.finished_at<? OR (e.rowid < (SELECT rowid FROM executions WHERE agent_id=e.agent_id
                ORDER BY rowid DESC LIMIT 1 OFFSET ?) AND (e.task_id IS NULL OR e.rowid <
                (SELECT rowid FROM executions WHERE task_id=e.task_id ORDER BY rowid DESC LIMIT 1 OFFSET ?))))
            AND (length(e.output)>0 OR length(e.input)>0 OR length(e.launch_input)>0 OR length(e.error)>0)
            ORDER BY e.rowid LIMIT ?''', (cutoff, EVENT_WINDOW - 1, EVENT_WINDOW - 1, batch)).fetchall()
        for row in executions:
            # A partially archived row can be revisited after a legitimate update.
            for column in ('input', 'launch_input', 'output', 'error'):
                value = row[column]
                if value:
                    conn.execute(f'UPDATE executions SET {column}=? WHERE id=?', (pack(conn, value, now, summary=column in ('output', 'error')), row['id']))
        changed += len(executions)
    return changed


class Maintenance:
    """A single background loop, separate from the sequential task queue."""
    def __init__(self, db):
        self.db, self.job = db, None

    async def run(self):
        while True:
            work = asyncio.create_task(asyncio.to_thread(maintain, self.db))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                await work  # finish/rollback the atomic batch before shutdown
                raise
            except Exception:
                logging.getLogger(__name__).exception('History maintenance failed; retained data will retry')
            await asyncio.sleep(60)

    def start(self):
        if self.job is None or self.job.done():
            self.job = asyncio.create_task(self.run())

    async def close(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

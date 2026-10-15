"""Read-only execution presentation from durable turn and question state."""

from .history import EVENT_WINDOW

# Capacity reservations and recovery timers are not active work. Both native
# workers and interactive manager turns share this predicate, across all scopes.
ACTIVE = """e.status='running' AND e.finished_at IS NULL AND e.turn_id IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM questions q WHERE q.execution_id=e.id AND q.status='pending')
    AND (e.task_id IS NULL OR EXISTS(SELECT 1 FROM tasks t WHERE t.id=e.task_id
        AND t.lease_owner=e.id AND t.state='running'))"""


# Keep prompt bodies and full results out of polled/default responses.
def brief_columns(alias='e'):
    names = ('id', 'task_id', 'project_id', 'agent_id', 'thread_id', 'turn_id', 'pid', 'process_start',
             'worktree', 'branch', 'commit_ref', 'model', 'effort', 'status', 'started_at', 'finished_at',
             'latency_ms', 'usage', 'delivery', 'delivery_execution', 'conversation_id', 'retry_at',
             'retry_count', 'recovery_reason', 'stop_requested')
    return ','.join(f'{alias}.{name}' for name in names) + (
        f',history_preview({alias}.output) AS output,history_preview({alias}.error) AS error')


def event_page(db, *, task_id=None, execution_id=None, before=None):
    column, value = ('task_id', task_id) if task_id else ('execution_id', execution_id)
    if before is not None and before < 1:
        raise ValueError('Event cursor must be positive')
    rows = db.all(f'SELECT * FROM events WHERE {column}=? AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT ?',
                  (value, before, before, EVENT_WINDOW + 1))
    page = rows[:EVENT_WINDOW]
    return {'events': list(reversed(page)), 'has_more_events': len(rows) > EVENT_WINDOW,
            'before_event': page[-1]['seq'] if len(rows) > EVENT_WINDOW else None}


def execution_page(db, *, task_id=None, project_id=None, offset=0, before=None):
    if offset < 0:
        raise ValueError('Offset must be nonnegative')
    column, value = ('task_id', task_id) if task_id else ('project_id', project_id)
    scope = f' AND e.{column}=?' if value else ''
    boundary = None
    if before:
        boundary = db.one(f'SELECT e.rowid AS position FROM executions e WHERE e.id=?{scope}',
                          (before, *((value,) if value else ())))
        if not boundary:
            raise ValueError('Execution was not found in this history')
    position = boundary['position'] if boundary else None
    rows = db.all(f"""SELECT {brief_columns()} FROM executions e
        WHERE (? IS NULL OR e.rowid<?){scope} ORDER BY e.rowid DESC LIMIT ? OFFSET ?""",
                  (position, position, *((value,) if value else ()), EVENT_WINDOW + 1, offset))
    page = rows[:EVENT_WINDOW]
    return {'executions': page,
            'next_offset': offset + EVENT_WINDOW if len(rows) > EVENT_WINDOW else None,
            'before_execution': page[-1]['id'] if len(rows) > EVENT_WINDOW else None}


def execution_summary(db, project_id=None):
    scope = " AND e.project_id=?" if project_id else ""
    params = (project_id,) if project_id else ()
    rows = db.all(
        f"""SELECT {brief_columns()},({ACTIVE}) AS is_working,
        (SELECT MIN(v.created_at) FROM events v WHERE v.execution_id=e.id
            AND v.kind IN ('worker.started','chat.started','turn/started')) AS work_started_at
        FROM executions e WHERE (e.finished_at IS NULL OR e.rowid IN
            (SELECT rowid FROM executions recent WHERE recent.agent_id=e.agent_id
                AND recent.project_id IS e.project_id AND recent.finished_at IS NOT NULL ORDER BY rowid DESC LIMIT {EVENT_WINDOW})){scope}
        ORDER BY is_working DESC,CASE WHEN e.finished_at IS NULL THEN 0 ELSE 1 END,e.started_at DESC,e.id LIMIT 100""", params,
    )
    count = db.one(f"SELECT count(*) AS n FROM executions e WHERE {ACTIVE}{scope}", params)["n"]
    return {"executions": rows, "active_agents": count}

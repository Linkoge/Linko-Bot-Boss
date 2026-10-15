"""Read-only execution presentation from durable turn and question state."""

# Capacity reservations and recovery timers are not active work. Both native
# workers and interactive manager turns share this predicate, across all scopes.
ACTIVE = """e.status='running' AND e.finished_at IS NULL AND e.turn_id IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM questions q WHERE q.execution_id=e.id AND q.status='pending')
    AND (e.task_id IS NULL OR EXISTS(SELECT 1 FROM tasks t WHERE t.id=e.task_id
        AND t.lease_owner=e.id AND t.state='running'))"""


def execution_summary(db, project_id=None):
    scope = " AND e.project_id=?" if project_id else ""
    params = (project_id,) if project_id else ()
    rows = db.all(
        f"""SELECT e.*,({ACTIVE}) AS is_working,
        (SELECT MIN(v.created_at) FROM events v WHERE v.execution_id=e.id
            AND v.kind IN ('worker.started','chat.started','turn/started')) AS work_started_at
        FROM executions e WHERE 1=1{scope}
        ORDER BY is_working DESC,CASE WHEN e.finished_at IS NULL THEN 0 ELSE 1 END,e.started_at DESC,e.id LIMIT 100""", params,
    )
    count = db.one(f"SELECT count(*) AS n FROM executions e WHERE {ACTIVE}{scope}", params)["n"]
    return {"executions": rows, "active_agents": count}

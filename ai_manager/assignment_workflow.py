"""Job follow-up on existing assignments/settings, separate from worker outcome."""
import json
import time

GRACE = 30
CHECK_INTERVAL = 15
REPAIR_LIMIT = 3
ACTIVE = {'actionable', 'review'}


def key(task_id):
    return 'assignment_workflow:' + task_id


def get(conn, task_id):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key(task_id),)).fetchone()
    return json.loads(row[0]) if row else None


def put(conn, task_id, value):
    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key(task_id), json.dumps(value)))


def update(db, conn, task_id, state, reason, execution_id=None, reset_retry=False, automatic=False):
    previous = get(conn, task_id) or {}
    preserve_retry = not reset_retry and state in ACTIVE and previous.get('state') in ACTIVE
    value = {**previous, 'state': state, 'automatic_completion': automatic, 'reason': reason[:2000], 'updated_at': time.time(),
             'idle_since': None, 'next_at': max(time.time() + GRACE, previous.get('next_at', 0) if preserve_retry else 0),
             'attempts': previous.get('attempts', 0)}
    lifecycle = conn.execute('SELECT lifecycle FROM tasks WHERE id=?', (task_id,)).fetchone()
    if (state in ACTIVE and lifecycle and lifecycle[0] in {'completed', 'failed'}
            and (previous.get('followup_rounds', 0) >= REPAIR_LIMIT
                 or previous.get('repair_rounds', 0) >= REPAIR_LIMIT)):
        value.update(state='owner_wait', reason='Three repair/follow-up rounds exhausted; report remaining failure')
    put(conn, task_id, value)
    db.event(conn, 'assignment.followup', {'state': value['state'], 'reason': value['reason']},
             task_id=task_id, execution_id=execution_id)
    return value


def allowed(conn, task, workflow):
    if not workflow or workflow.get('followup_rounds', 0) >= REPAIR_LIMIT or workflow.get('repair_rounds', 0) >= REPAIR_LIMIT or workflow['state'] not in ACTIVE or task['legacy'] or not task['origin_conversation']:
        return False
    # Queued/running work belongs to existing dispatch/native recovery. Deliberate
    # waiting/paused/cancelled work can never be manufactured into runnable work.
    if task['lifecycle'] not in {'completed', 'failed'} or task['lease_owner']:
        return False
    if not conn.execute("SELECT 1 FROM projects WHERE id=? AND status='active'", (task['project_id'],)).fetchone():
        return False
    if not conn.execute('SELECT 1 FROM conversations WHERE id=?', (task['origin_conversation'],)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM settings s,json_each(s.value) a WHERE s.key='archived_tasks' AND a.key=?",
                    (task['id'],)).fetchone():
        return False
    if conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND lease_owner IS NOT NULL', (task['project_id'],)).fetchone():
        return False  # Includes uncertain leases; existing recovery accounts for ownership.
    if conn.execute("SELECT 1 FROM executions WHERE finished_at IS NULL AND (conversation_id=? OR task_id=?)",
                    (task['origin_conversation'], task['id'])).fetchone():
        return False
    if conn.execute("SELECT 1 FROM questions q JOIN executions e ON e.id=q.execution_id WHERE q.status='pending' AND (e.project_id=? OR e.conversation_id=?)",
                    (task['project_id'], task['origin_conversation'])).fetchone():
        return False
    if conn.execute("SELECT 1 FROM executions WHERE task_id=? AND delivery IN ('pending','running')",
                    (task['id'],)).fetchone():
        return False  # Existing result delivery is already a durable manager wakeup.
    wake = workflow.get('wake_execution')
    if wake and conn.execute('SELECT 1 FROM executions WHERE id=? AND finished_at IS NULL', (wake,)).fetchone():
        return False
    return True


def due(db, now=None):
    """SQLite only. No provider/AI calls; BEGIN IMMEDIATE serializes checkers."""
    now = time.time() if now is None else now
    candidates = []
    with db.transaction() as conn:
        rows = conn.execute("SELECT t.* FROM tasks t JOIN settings s ON s.key='assignment_workflow:'||t.id WHERE json_extract(s.value,'$.state') IN ('actionable','review')").fetchall()
        for row in rows:
            task = dict(row)
            workflow = get(conn, task['id'])
            if not allowed(conn, task, workflow):
                if workflow.get('idle_since') is not None:
                    workflow['idle_since'] = None
                    put(conn, task['id'], workflow)
                continue
            if workflow.get('idle_since') is None:
                workflow['idle_since'] = now
                put(conn, task['id'], workflow)
            if now >= max(workflow['idle_since'] + GRACE, workflow.get('next_at', 0)):
                candidates.append(task)
    return candidates


def claim(conn, task_id, execution_id, now, project_id, conversation_id):
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    workflow = get(conn, task_id)
    if (not task or task['project_id'] != project_id or task['origin_conversation'] != conversation_id
            or not allowed(conn, dict(task), workflow) or workflow.get('idle_since') is None):
        raise ValueError('Follow-up is no longer actionable or conversation is busy')
    if now < max(workflow['idle_since'] + GRACE, workflow.get('next_at', 0)):
        raise ValueError('Follow-up grace/backoff has not elapsed')
    workflow['wake_execution'] = execution_id
    workflow['followup_rounds'] = workflow.get('followup_rounds', 0) + 1
    workflow['attempts'] = min(workflow.get('attempts', 0) + 1, 16)
    workflow['next_at'] = now + min(1800, GRACE * 2 ** workflow['attempts'])
    workflow['idle_since'] = None
    put(conn, task_id, workflow)
    conn.execute('INSERT INTO settings VALUES(?,?)', ('execution_recovery:' + execution_id, json.dumps(task_id)))
    return workflow



def ready_to_run(db, task_id):
    """Recheck owner transitions after asynchronous provider preparation."""
    with db.transaction() as conn:
        task = conn.execute('SELECT t.*,p.status AS project_status FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?', (task_id,)).fetchone()
        workflow = get(conn, task_id)
        if (not task or task['project_status'] != 'active' or task['lifecycle'] not in {'completed', 'failed'}
                or task['legacy'] or not workflow or workflow['state'] not in ACTIVE):
            return False
        if not conn.execute('SELECT 1 FROM conversations WHERE id=?', (task['origin_conversation'],)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM settings s,json_each(s.value) a WHERE s.key='archived_tasks' AND a.key=?", (task_id,)).fetchone():
            return False
        if conn.execute('SELECT 1 FROM tasks WHERE project_id=? AND lease_owner IS NOT NULL', (task['project_id'],)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM questions q JOIN executions e ON e.id=q.execution_id WHERE q.status='pending' AND (e.project_id=? OR e.conversation_id=?)",
                        (task['project_id'],task['origin_conversation'])).fetchone():
            return False
        return True

def hold_conversation(db, conn, conversation_id, execution_id):
    for row in conn.execute('SELECT id FROM tasks WHERE origin_conversation=?', (conversation_id,)).fetchall():
        workflow = get(conn, row['id'])
        if workflow and workflow['state'] in ACTIVE:
            update(db, conn, row['id'], 'owner_wait', 'Manager stopped by owner; explicit follow-up authorization required', execution_id)


def consume_repair(conn, task):
    """Charge a committed continuation, not a transport retry or paused resume.

    Both manager and UI continuations use this inside their existing transaction;
    replay/revision guards run first. No transition/reset can erase this counter.
    """
    if task['lifecycle'] not in {'completed', 'failed'}:
        return
    value = get(conn, task['id']) or {}
    used = value.get('repair_rounds', 0)
    if used >= REPAIR_LIMIT:
        raise ValueError('Three repair rounds exhausted; retain work and report remaining failure')
    value['repair_rounds'] = used + 1
    put(conn, task['id'], value)


def finish_wake(db, conn, execution_id):
    row = conn.execute('SELECT value FROM settings WHERE key=?', ('execution_recovery:' + execution_id,)).fetchone()
    if not row:
        return
    task_id = json.loads(row[0])
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    value = get(conn, task_id) or {}
    if (not task or task['lease_owner'] or task['lifecycle'] not in {'completed', 'failed'}
            or value.get('state') not in ACTIVE or value.get('followup_rounds', 0) < REPAIR_LIMIT):
        return  # Third wake may have queued a valid last repair; do not hold that worker.
    update(db, conn, task_id, 'owner_wait', 'Three follow-up rounds exhausted; retained failure needs owner attention', execution_id)
    conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,status,created_at) "
                 "VALUES(?,?,'assistant',?,'completed',?)",
                 ('repair-limit:' + task_id, task['origin_conversation'],
                  'Automatic repair stopped after three follow-up rounds. Work is retained; '
                  'see the task result for the remaining failure.', time.time()))

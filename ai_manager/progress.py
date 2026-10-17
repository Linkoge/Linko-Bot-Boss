"""One read-only answer to "is something still in progress?", derived from state that already exists.

The owner must never see empty space while work continues after a worker's turn: checks, repair rounds, merge, the
deploy request and the hand-off of the result are all still running. This module only reads task rows, execution
rows, events and the deploy request/state files (README.md, "Progress indicator"); it adds no state, gate or wait.
Every phase ends by itself through the existing recovery paths (lease reconciliation, the 30 minute deploy
withdrawal), plus the age bounds below for the two phases that have no row to expire.
"""
import os
import time

from .core import REPAIR_ROUNDS
from .execution_ui import ACTIVE
from .pipeline import DEPLOY_WAIT, read

REPORT_WINDOW = 300  # a finished result normally reaches the chat within a minute; later it is not "in progress"
DEPLOY_MAX_AGE = DEPLOY_WAIT + 2 * 3600  # past the backup a deploy finishes or rolls back; this bounds a dead script
DEAD_SCRIPT_GRACE = 120  # a deploy state written by a process that is gone and silent this long is stale

DEPLOY_LABELS = {None: 'Deploy requested', 'testing': 'Deploying: running tests',
                 'waiting_idle': 'Deploying: waiting for idle', 'backing_up': 'Deploying: backing up',
                 'emergency_backing_up': 'Deploying: backing up', 'restarting': 'Deploying: restarting',
                 'updating': 'Deploying: updating', 'verifying': 'Health check', 'rolling_back': 'Rolling back'}
IDLE = {'active': False, 'phase': None, 'label': '', 'project_id': None, 'task_id': None, 'since': None}


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError, OverflowError):
        return True
    return True


def phase(name, label, project_id=None, task_id=None, since=None):
    return {'active': True, 'phase': name, 'label': label, 'project_id': project_id, 'task_id': task_id, 'since': since}


def task_phase(db, task):
    """Phase of one running task: agent, checks, repair, merge (or a pending question)."""
    rounds = task['rounds']
    repair = f'Repair round {rounds} of {REPAIR_ROUNDS}'
    if db.one("SELECT 1 FROM questions WHERE execution_id=? AND status='pending'", (task['lease_owner'],)):
        return 'question', 'Waiting for your answer'
    if task['stage'] == 'merge':
        return 'merge', 'Merging'
    last = db.one("SELECT kind FROM events WHERE execution_id=? AND kind IN ('task.check_started','task.check') "
                  'ORDER BY seq DESC LIMIT 1', (task['lease_owner'],))
    if last and last['kind'] == 'task.check_started':
        return 'checks', 'Running checks' + (f' · repair round {rounds} of {REPAIR_ROUNDS}' if rounds else '')
    if rounds:
        return 'repair', repair
    return 'agent', 'Working' if task['turn_id'] else 'Starting'


def deploy_phase(pipeline, project_id, now):
    request = read(pipeline.root / 'request.json')
    if not request or (project_id and request.get('project_id') != project_id):
        return None
    if now - request.get('requested_at', now) > DEPLOY_MAX_AGE:
        return None
    state = read(pipeline.root / 'state.json') or {}
    if state.get('pid') and not alive(state['pid']) and now - state.get('at', now) > DEAD_SCRIPT_GRACE:
        return None
    name = state.get('phase')
    return phase('deploy', DEPLOY_LABELS.get(name, 'Deploying'), request.get('project_id'), request.get('task_id'),
                 state.get('at') or request.get('requested_at'))


def progress(db, pipeline, project_id=None, now=None):
    """The single phase to show for a project (or all projects): the most advanced work wins. Not in progress -> IDLE."""
    now = now or time.time()
    scope = ' AND t.project_id=?' if project_id else ''
    args = (project_id,) if project_id else ()
    for task in db.all(f"""SELECT t.id,t.project_id,t.stage,t.rounds,t.lease_owner,e.turn_id,e.started_at FROM tasks t
            JOIN executions e ON e.id=t.lease_owner AND e.finished_at IS NULL
            WHERE t.state='running'{scope} ORDER BY t.queued_at,t.rowid""", args):
        name, label = task_phase(db, task)
        return phase(name, label, task['project_id'], task['id'], task['started_at'])
    if found := deploy_phase(pipeline, project_id, now):
        return found
    # An interactive manager turn: the message already shows it, but this keeps one source of truth.
    scope = ' AND e.project_id=?' if project_id else ''
    if row := db.one(f'SELECT e.project_id,e.started_at FROM executions e WHERE {ACTIVE} AND e.task_id IS NULL{scope} '
                     'ORDER BY e.started_at LIMIT 1', args):
        return phase('agent', 'Responding', row['project_id'], None, row['started_at'])
    scope = ' AND t.project_id=?' if project_id else ''
    if row := db.one(f"""SELECT t.id,t.project_id,t.stage,t.retry_at,t.queued_at FROM tasks t
            JOIN projects p ON p.id=t.project_id WHERE t.state='queued' AND t.held=0 AND p.status='active'{scope}
            ORDER BY t.queued_at,t.rowid LIMIT 1""", args):
        label = ('Waiting to retry' if row['retry_at'] and row['retry_at'] > now
                 else 'Queued to merge' if row['stage'] == 'merge' else 'Queued')
        return phase('queued', label, row['project_id'], row['id'], row['queued_at'])
    scope = ' AND e.project_id=?' if project_id else ''
    if row := db.one(f"""SELECT e.project_id,e.task_id,e.finished_at FROM executions e JOIN tasks t ON t.id=e.task_id
            WHERE e.delivery='pending' AND e.finished_at>? AND t.lease_owner IS NULL{scope}
            ORDER BY e.finished_at LIMIT 1""", (now - REPORT_WINDOW, *args)):
        return phase('report', 'Reporting result', row['project_id'], row['task_id'], row['finished_at'])
    return dict(IDLE)

"""Owner-sourced, one-release deployment intent. No permission is read from worker prose.

Existing SQLite settings/events provide transactional durability without a migration.
The independent helper protocol remains the only activation path. Auto intents (kind='auto',
docs/AUTO_DEPLOY.md) bind one owner work request to its assignment's exact verified release and
are consumed only by the backend auto-deploy pipeline, never by manager tool calls.
"""
import hashlib
import json
import time

from .deployment import ID, Handoff, read, sha

PREFIX = 'deployment_intent:'


class Held(ValueError):
    """A newer owner turn must review the unused intent, or the project is paused."""


class Superseded(ValueError):
    """The assignment changed after verification; its prepared release is never used."""


def rows(conn):
    return [json.loads(r[0]) for r in conn.execute('SELECT value FROM settings WHERE key LIKE ?', (PREFIX+'%',))]


def assignment_evidence(conn, task, version=2):
    value = {k: task[k] for k in ('id', 'project_id', 'description', 'acceptance', 'request_key')}
    if version == 1:
        value['context'] = [r[0] for r in conn.execute("SELECT payload FROM events WHERE task_id=? AND kind IN ('task.context','manager.continued') ORDER BY seq", (task['id'],))]
    elif version == 2:
        # Scope changes invalidate evidence; resume receipts and repeated snapshots do not.
        row = conn.execute("SELECT payload FROM events WHERE task_id=? AND kind='task.context' ORDER BY seq DESC LIMIT 1", (task['id'],)).fetchone()
        value['context'] = json.loads(row[0]) if row else {}
    else:
        raise ValueError('Unsupported assignment evidence version')
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(db, conn, value, event):
    value['updated_at'] = time.time()
    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (PREFIX+value['id'], json.dumps(value)))
    db.event(conn, 'deployment.intent.'+event, value, value['project_id'], execution_id=value.get('review_execution'))
    return value


def revoke(db, conn, reason, project_id=None, conversation_id=None, task_id=None, intent_id=None, manual_only=False):
    for value in rows(conn):
        if manual_only and value.get('kind') == 'auto':
            continue
        if value['status'] in {'revoked', 'failed'} or (value['status'] == 'dispatched' and value.get('kind') != 'auto'):
            continue
        if intent_id and value['id'] != intent_id and value.get('manual_intent_id') != intent_id:
            continue
        if project_id and value['project_id'] != project_id:
            continue
        if conversation_id and value['conversation_id'] != conversation_id:
            continue
        if task_id:
            task = conn.execute('SELECT request_key FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task_id not in value['task_ids'] + value.get('review_task_ids', []) and (not task or task[0] != value.get('task_request')):
                continue
        # Reconcile the file/SQL crash gap before claiming an unused intent was
        # cancelled. A protocol request can already exist while SQL says reviewed.
        handoff = Handoff(db.path.parent)
        active = handoff.status()
        if (active.get('origin') or {}).get('intent_id') == value['id']:
            if active['phase'] == 'waiting_idle':
                handoff.cancel(active['nonce'])
            elif handoff.ready_to_stop():
                raise ValueError('Deployment is already acknowledged; external inspection is required')
        value.update(status='revoked', reason=reason)
        save(db, conn, value, 'revoked')


class Intents:
    def __init__(self, db):
        self.db, self.handoff = db, Handoff(db.path.parent)

    def latest(self, conn, value):
        # Include accepted queued requests, not only turns already processed. Global
        # corrections can constrain this manager project even from All Projects.
        return conn.execute("""SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.role='user' AND (c.project_id=? OR c.project_id IS NULL OR c.id=?)
            ORDER BY m.rowid DESC LIMIT 1""", (value['project_id'], value['conversation_id'])).fetchone()

    def owner(self, conn, params):
        source = conn.execute("""SELECT m.* FROM messages m JOIN executions e ON e.id=m.execution_id
            WHERE m.role='user' AND m.execution_id=? AND m.conversation_id=? AND e.agent_id='manager'
            AND e.finished_at IS NULL AND e.stop_requested=0 AND e.mode IN ('auto','execute')""",
            (params['manager_execution_id'], params['conversation_id'])).fetchone()
        if params.get('internal') or not source:
            raise ValueError('Only a current Auto/Execute owner message can record or revise deployment intent')
        return source

    def candidate(self, release_id):
        if not isinstance(release_id, str) or not ID.fullmatch(release_id):
            raise ValueError('Use an exact prepared release ID')
        root = self.handoff.root / 'releases' / release_id
        manifest = read(root / 'manifest.json')
        if not manifest or manifest.get('id') != release_id:
            raise ValueError('Release is not prepared')
        for name, field in [('release.bundle', 'bundle_sha256'), ('validation.log', 'validation_sha256')]:
            if not manifest.get(field) or sha(root / name) != manifest[field]:
                raise ValueError('Prepared candidate evidence changed or is incomplete')
        if not manifest.get('commit') or not manifest.get('base'):
            raise ValueError('Prepared candidate evidence is incomplete')
        candidate = {'release_id': release_id, 'manifest_sha256': sha(root / 'manifest.json'),
                     'commit': manifest['commit'], 'base': manifest['base']}
        if (root / 'evidence-index.json').exists():
            from .release_publication import inventory
            candidate['index_sha256'] = sha(root / 'evidence-index.json')
            inventory(root, candidate['manifest_sha256'], candidate['index_sha256'])
        return candidate, manifest

    def record(self, project_id, params, quote, release_id=None, task_ids=None, task_request=None):
        # Manager interpretation of explicit deployment language is the same trust
        # boundary as other owner controls; source IDs and scope are enforced here.
        with self.db.transaction() as conn:
            source = self.owner(conn, params)
            if not quote or quote not in source['content']:
                raise ValueError('Quote the actual owner deployment instruction')
            if bool(release_id) == bool(task_ids or task_request):
                raise ValueError('Choose one fixed prepared release or assignment scope')
            scope = {'release_id': release_id, 'task_ids': sorted(set(task_ids or [])), 'task_request': task_request}
            for task_id in scope['task_ids']:
                task = conn.execute('SELECT * FROM tasks WHERE id=? AND project_id=?', (task_id, project_id)).fetchone()
                if not task or task['created_at'] > source['created_at']:
                    raise ValueError('Assignment was not in this owner request scope')
            candidate = self.candidate(release_id)[0] if release_id else None
            identifier = hashlib.sha256((source['id']+project_id).encode()).hexdigest()[:32]
            previous = next((v for v in rows(conn) if v['id'] == identifier), None)
            if previous:
                if previous['scope'] != scope:
                    raise ValueError('Owner intent already recorded with a different scope')
                return previous
            value = {'id': identifier, 'owner_id': 1, 'source_message_id': source['id'],
                     'source_execution_id': source['execution_id'], 'source_sha256': hashlib.sha256(source['content'].encode()).hexdigest(),
                     'conversation_id': source['conversation_id'], 'project_id': project_id, 'quote': quote,
                     'scope': scope, 'task_ids': scope['task_ids'], 'task_request': task_request,
                     'candidate': candidate, 'status': 'recorded', 'created_at': time.time(),
                     'evidence_version': 2,
                     'assignment_hashes': {t: assignment_evidence(conn, conn.execute('SELECT * FROM tasks WHERE id=?', (t,)).fetchone(), 2) for t in scope['task_ids']},
                     'checked_owner_message': source['id'],
                     'checked_owner_sha256': hashlib.sha256(source['content'].encode()).hexdigest()}
            from .auto_deploy import enabled
            revoke(self.db, conn, 'Superseded by a new scoped owner deployment intent', project_id=project_id,
                   manual_only=enabled(conn, project_id))
            return save(self.db, conn, value, 'recorded')

    def attach(self, project_id, params, intent_id, task_id):
        with self.db.transaction() as conn:
            source = self.owner(conn, params)
            value = self.get(conn, intent_id, project_id, params)
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if value['source_message_id'] != source['id'] or not task or task['request_key'] != source['execution_id']+':'+project_id:
                raise ValueError('Deployment audit/preparation must belong to the recorded owner assignment')
            if value['status'] == 'superseded':
                return value  # Replay must not reseal the preserved manual source mapping.
            if value.get('task_request'):
                value['assignment_hashes'] = {task_id: assignment_evidence(conn, task, value.get('evidence_version', 1))}
            else:
                value['review_task_ids'] = [task_id]
            return save(self.db, conn, value, 'assignment_linked')

    def record_auto(self, conn, project_id, params, task_id):
        """The current owner work request is the deployment intent for this assignment's exact release only."""
        source = self.owner(conn, params)
        if not conn.execute('SELECT 1 FROM tasks WHERE id=? AND project_id=?', (task_id, project_id)).fetchone():
            raise ValueError('Assignment belongs to another project or is missing')
        digest = hashlib.sha256(source['content'].encode()).hexdigest()
        identifier = hashlib.sha256((source['id'] + project_id + task_id).encode()).hexdigest()[:32]
        replay = next((v for v in rows(conn) if v['id'] == identifier), None)
        if replay:
            return replay
        existing = next((v for v in rows(conn) if v.get('kind') == 'auto' and v['task_ids'] == [task_id]
                         and v['status'] == 'recorded'), None)
        if existing:
            # The same owner extended or batched into this assignment: still unused, reaffirmed by the latest turn.
            if existing['checked_owner_message'] != source['id']:
                existing.update(checked_owner_message=source['id'], checked_owner_sha256=digest,
                                reason='Owner request extended this assignment')
                save(self.db, conn, existing, 'owner_reviewed')
            return existing
        value = {'id': identifier,
                 'kind': 'auto', 'owner_id': 1, 'source_message_id': source['id'],
                 'source_execution_id': source['execution_id'], 'source_sha256': digest,
                 'conversation_id': source['conversation_id'], 'project_id': project_id,
                 'quote': source['content'][:2000],
                 'scope': {'release_id': None, 'task_ids': [task_id], 'task_request': None, 'auto': True},
                 'task_ids': [task_id], 'task_request': None, 'candidate': None, 'status': 'recorded',
                 'created_at': time.time(), 'evidence_version': 2, 'assignment_hashes': {}, 'auto': {'state': 'waiting_result'},
                 'checked_owner_message': source['id'], 'checked_owner_sha256': digest}
        return save(self.db, conn, value, 'recorded')

    def consume_auto(self, intent_id, release_id, commit, execution_id):
        """Pipeline-only: dispatch exactly the release prepared from this assignment's verified commit."""
        with self.db.transaction() as conn:
            value = next((v for v in rows(conn) if v['id'] == intent_id), None)
            if not value or value.get('kind') != 'auto' or value['status'] != 'recorded':
                raise ValueError('Auto-deploy intent is no longer unused')
            try:
                self.current(conn, value)
            except ValueError as error:
                raise Held(str(error)) from error
            if (value['auto'].get('release_id') != release_id or value['auto'].get('commit') != commit
                    or value['auto'].get('execution_id') != execution_id):
                raise ValueError('Release differs from the pipeline verified candidate')
            candidate, manifest = self.candidate(release_id)
            task_id = value['task_ids'][0]
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            from .auto_deploy import git
            project = conn.execute('SELECT path FROM projects WHERE id=?', (value['project_id'],)).fetchone()
            if candidate['commit'] != commit or candidate['base'] != git(project['path'], 'rev-parse', 'HEAD'):
                raise ValueError('Prepared release is not the exact verified assignment commit')
            source = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_source:' + task_id,)).fetchone()
            latest = conn.execute('SELECT id FROM executions WHERE task_id=? ORDER BY started_at DESC LIMIT 1',
                                  (task_id,)).fetchone()
            try:
                self.tasks(conn, value)
            except ValueError as error:
                raise Superseded(str(error)) from error
            if (not source or json.loads(source[0]).get('commit') != commit or not latest or latest['id'] != execution_id
                    or manifest.get('assignment_evidence_version') != 2
                    or manifest.get('assignments') != {task_id: assignment_evidence(conn, task)}):
                raise Superseded('Assignment changed after verification')
            from .auto_deploy import verified
            if verified(conn, task, execution_id, commit):
                raise ValueError('Assignment verification no longer passes')
            from .deployment import release_attested
            if (not release_attested(manifest, execution_id)
                    or not manifest.get('restore_database')):
                raise ValueError('Release lacks the exact release-check auto-deploy attestation')
            if git(task['workspace'], 'rev-parse', 'HEAD') != commit or git(task['workspace'], 'status', '--porcelain'):
                raise Superseded('Assignment workspace changed after verification')
            origin = {'project_id': value['project_id'], 'conversation_id': value['conversation_id'],
                      'execution_id': execution_id, 'task_id': task_id, 'intent_id': intent_id, 'auto': True,
                      'owner_message_id': value['source_message_id'], 'candidate': candidate}
            receipt = self.handoff.request(release_id, origin)
            if (receipt.get('origin') or {}).get('intent_id') != intent_id:
                raise ValueError('Existing release request has a different authorization source; inspect status')
            value.update(candidate=candidate, status='dispatched', nonce=receipt['nonce'],
                         auto={**value['auto'], 'state': 'dispatched', 'updated_at': time.time()})
            save(self.db, conn, value, 'consumed')
            return receipt

    def get(self, conn, intent_id, project_id, params, owner_revision=False):
        value = next((v for v in rows(conn) if v['id'] == intent_id), None)
        if not value or value['project_id'] != project_id or (not owner_revision and value['conversation_id'] != params['conversation_id']):
            raise ValueError('Deployment intent belongs to another project/conversation or is missing')
        return value

    def current(self, conn, value):
        source = conn.execute('SELECT content FROM messages WHERE id=? AND role=\'user\'',
                              (value['source_message_id'],)).fetchone()
        if value.get('kind') != 'auto' and (not source or hashlib.sha256(source[0].encode()).hexdigest() != value['source_sha256']):
            raise ValueError('Original owner source is missing or changed')
        latest = self.latest(conn, value)
        if value.get('kind') == 'auto':
            from .auto_deploy import enabled
            if not enabled(conn, value['project_id']):
                raise ValueError('Auto-deploy is off')
        elif (not latest or latest['id'] != value['checked_owner_message'] or
                hashlib.sha256(latest['content'].encode()).hexdigest() != value['checked_owner_sha256']):
            raise ValueError('A newer owner request or edited correction needs review before deployment')
        project = conn.execute('SELECT status FROM projects WHERE id=?', (value['project_id'],)).fetchone()
        if not project or project[0] != 'active':
            raise ValueError('Deployment intent cannot resume a paused/inactive project')
        if value['status'] == 'revoked':
            raise ValueError('Deployment intent was revoked; a new owner request is required')

    def revise(self, project_id, params, intent_id, decision, reason):
        with self.db.transaction() as conn:
            source = self.owner(conn, params)
            value = self.get(conn, intent_id, project_id, params, owner_revision=True)
            context = conn.execute('SELECT project_id FROM conversations WHERE id=?', (source['conversation_id'],)).fetchone()
            if not context or context[0] not in {None, project_id}:
                raise ValueError('Owner correction belongs to another project')
            if not reason.strip() or decision not in {'preserve', 'revoke'}:
                raise ValueError('Review needs preserve/revoke and the owner correction reason')
            if decision == 'revoke':
                if value['status'] == 'dispatched':
                    raise ValueError('Cancel the exact pending deployment request; intent is already dispatched')
                revoke(self.db, conn, reason, intent_id=intent_id)
            else:
                if value['status'] not in {'recorded', 'reviewed'} or self.latest(conn, value)['id'] != source['id']:
                    raise ValueError('Only the latest owner turn can preserve an unused intent')
                value.update(checked_owner_message=source['id'],
                             checked_owner_sha256=hashlib.sha256(source['content'].encode()).hexdigest(), reason=reason)
                save(self.db, conn, value, 'owner_reviewed')
            return next(v for v in rows(conn) if v['id'] == intent_id)

    def source_tasks(self, conn, value):
        """Only the owner's fixed source scope; audit tasks never become release sources."""
        tasks = [conn.execute('SELECT * FROM tasks WHERE id=?', (t,)).fetchone() for t in value['task_ids']]
        if value.get('task_request'):
            source = conn.execute('SELECT * FROM tasks WHERE request_key=?', (value['task_request'],)).fetchone()
            if not source or not any(t and t['id'] == source['id'] for t in tasks):
                tasks.append(source)
        return tasks

    def tasks(self, conn, value):
        sources = self.source_tasks(conn, value)
        tasks = sources + [conn.execute('SELECT * FROM tasks WHERE id=?', (t,)).fetchone()
                           for t in value.get('review_task_ids', []) if t not in {s['id'] for s in sources if s}]
        for task in tasks:
            workflow = conn.execute('SELECT value FROM settings WHERE key=?', ('assignment_workflow:'+task['id'],)).fetchone() if task else None
            if (not task or task['project_id'] != value['project_id'] or task['lifecycle'] != 'completed'
                    or task['lease_owner'] or (workflow and json.loads(workflow[0]).get('state') in {'owner_wait', 'deferred'})):
                raise ValueError('Scoped preparation is incomplete, failed or held by the owner')
            if task['id'] in value['assignment_hashes'] and assignment_evidence(conn, task, value.get('evidence_version', 1)) != value['assignment_hashes'][task['id']]:
                raise ValueError('Assignment scope changed after owner authorization')
        return tasks

    def review(self, project_id, params, intent_id, release_id, reason):
        with self.db.transaction() as conn:
            value = self.get(conn, intent_id, project_id, params)
            if value.get('kind') == 'auto':
                raise ValueError('Auto-deploy intents are consumed only by the verified auto-deploy pipeline')
            self.current(conn, value)
            candidate, manifest = self.candidate(release_id)
            if value['candidate'] and candidate != value['candidate']:
                raise ValueError('Different release or immutable candidate evidence')
            tasks = self.tasks(conn, value)
            if tasks and not value['scope']['release_id']:
                version = value.get('evidence_version', 1)
                if manifest.get('assignment_evidence_version', 1) != version:
                    raise ValueError('Prepared release does not match the fixed assignment evidence version; legacy evidence is not migrated')
                expected = {t['id']: assignment_evidence(conn, t, version) for t in self.source_tasks(conn, value)}
                if expected != value['assignment_hashes']:
                    raise ValueError('Assignment scope has not been sealed')
                if manifest.get('assignments') != expected:
                    raise ValueError('Prepared release does not match the fixed assignment scope/evidence')
                for task in tasks:
                    if not conn.execute("SELECT 1 FROM executions WHERE task_id=? AND status='completed' AND finished_at IS NOT NULL",
                                        (task['id'],)).fetchone():
                        raise ValueError('Completed preparation result is required')
            if not reason.strip() or value['status'] not in {'recorded', 'reviewed'}:
                raise ValueError('Unused intent and evidence review are required')
            value.update(candidate=candidate, status='reviewed', review_execution=params['manager_execution_id'], review=reason)
            return save(self.db, conn, value, 'reviewed')

    def dispatch(self, project_id, params, intent_id, release_id):
        # BEGIN IMMEDIATE serializes owner revocation, dispatch and concurrent calls.
        # A crash after protocol write but before SQL commit is recovered from the
        # durable protocol origin; failed/rollback receipts are returned, never retried.
        with self.db.transaction() as conn:
            value = self.get(conn, intent_id, project_id, params)
            paths = sorted((self.handoff.root / 'results').glob('*.json')) + [self.handoff.root / 'active.json']
            for path in paths:
                receipt = read(path)
                if receipt and (receipt.get('origin') or {}).get('intent_id') == intent_id:
                    if release_id != receipt['release_id']:
                        raise ValueError('Different release for consumed intent')
                    if value['status'] != 'revoked' and (value['status'] != 'dispatched' or value.get('nonce') != receipt['nonce']):
                        value.update(status='dispatched', nonce=receipt['nonce'])
                        save(self.db, conn, value, 'consumed')
                    return receipt
            if value.get('kind') == 'auto':
                raise ValueError('Auto-deploy intents are consumed only by the verified auto-deploy pipeline')
            self.current(conn, value)
            if value['status'] != 'reviewed' or not value['candidate']:
                raise ValueError('Recorded owner intent and exact prepared evidence review are required')
            candidate, _ = self.candidate(release_id)
            if candidate != value['candidate']:
                raise ValueError('Different release or immutable candidate evidence')
            self.tasks(conn, value)
            origin = {'project_id': project_id, 'conversation_id': value['conversation_id'],
                      'execution_id': params['manager_execution_id'], 'intent_id': intent_id,
                      'owner_message_id': value['source_message_id'], 'candidate': candidate}
            try:
                receipt = self.handoff.request(release_id, origin)
                if (receipt.get('origin') or {}).get('intent_id') != intent_id:
                    raise ValueError('Existing release request has a different authorization source; inspect status')
            except (ValueError, OSError) as error:
                value.update(status='dispatch_failed', reason=str(error))
                save(self.db, conn, value, 'dispatch_failed')
                return {'phase': 'dispatch_failed', 'intent_id': intent_id, 'error': str(error)}
            value.update(status='dispatched', nonce=receipt['nonce'])
            save(self.db, conn, value, 'consumed')
            return receipt

    def list(self, project_id=None):
        with self.db.connect() as conn:
            result = []
            for value in rows(conn):
                if project_id and value['project_id'] != project_id:
                    continue
                latest = self.latest(conn, value)
                summary = {k: value[k] for k in ('id', 'project_id', 'conversation_id', 'source_message_id',
                           'scope', 'candidate', 'status')}
                if value.get('kind') == 'auto':
                    summary.update(kind='auto', task_ids=value['task_ids'], auto=value['auto'])
                summary['needs_owner_review'] = bool(value.get('kind') != 'auto' and latest and (latest['id'] != value['checked_owner_message'] or
                    hashlib.sha256(latest['content'].encode()).hexdigest() != value.get('checked_owner_sha256')))
                if value.get('nonce'):
                    path = self.handoff.root / 'results' / (value['nonce']+'.json')
                    summary.update(nonce=value['nonce'], receipt=str(path), receipt_phase=(read(path) or {}).get('phase'))
                result.append(summary)
            return result

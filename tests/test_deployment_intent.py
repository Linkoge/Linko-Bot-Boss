"""Real owner queue/assignment/result routing with disposable storage, no activation."""
import asyncio
import json

import pytest
from fake_codex import FakeServer
from test_minimal_flow import system as system_fixture

from ai_manager.db import Database
from ai_manager.deployment import Handoff, atomic, read, sha
from ai_manager.deployment_intent import Intents, assignment_evidence
from ai_manager.orchestration import Scheduler
from ai_manager.workers import Workers

system = system_fixture


def prepared(handoff, release='candidate-1', assignments=None):
    root = handoff.root / 'releases' / release
    root.mkdir(parents=True, exist_ok=True)
    (root / 'release.bundle').write_bytes(b'immutable bundle '+release.encode())
    (root / 'validation.log').write_text('validated '+release)
    atomic(root / 'manifest.json', {'id': release, 'commit': 'a'*40, 'base': 'b'*40,
           'bundle_sha256': sha(root/'release.bundle'), 'validation_sha256': sha(root/'validation.log'),
           **({'assignments': assignments, 'assignment_evidence_version': 2} if assignments is not None else {})})


async def owner(chat, project, text='deploy pending candidate after recheck', mode='auto'):
    receipt = await chat.submit(project, text, mode=mode)
    row = chat.db.one('SELECT * FROM executions WHERE id=?', (receipt['execution_id'],))
    return {'tool': 'manager_action', 'manager_execution_id': row['id'], 'conversation_id': row['conversation_id'],
            'mode': mode, 'internal': False, 'callId': row['id']}


def finish_owner(db, params):
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET status='completed',finished_at=2 WHERE id=?", (params['manager_execution_id'],))
        conn.execute("UPDATE messages SET status='completed' WHERE execution_id=?", (params['manager_execution_id'],))


@pytest.fixture
def flow(system):
    db, registry, chat, settings = system
    chat.launch = lambda *args: None
    workers = Workers(db, registry, settings, lambda s, native=False: FakeServer(s))
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    project = registry.list()[0]['id']
    atomic(scheduler.deployment.root/'config.json', {'repo': registry.get(project)['path']})
    prepared(scheduler.deployment)
    prepared(scheduler.deployment, 'candidate-2')
    return db, registry, chat, settings, workers, scheduler, project


async def call(scheduler, project, params, action, **args):
    return await scheduler.manager_action(project, {**params, 'arguments': {'action': action, **args}})


async def recorded(flow):
    _, _, chat, _, _, scheduler, project = flow
    params = await owner(chat, project)
    intent = await call(scheduler, project, params, 'record_deployment_intent', release_id='candidate-1',
                        owner_deployment_quote='deploy pending candidate after recheck')
    return params, intent


async def completed_audit(flow, params, intent):
    db, _, chat, _, workers, scheduler, project = flow
    task = await call(scheduler, project, params, 'delegate', objective='Audit exact pending release; no feature work', intent_id=intent['id'])
    finish_owner(db, params)
    claim = scheduler.tasks.claim(2)
    workers.finish(claim['lease_owner'], 'completed', 'Reviewed candidate-1 with exact coverage report', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
    await chat.deliver_results()
    source = db.one('SELECT * FROM executions WHERE id=?', (claim['lease_owner'],))
    internal = {**params, 'manager_execution_id': source['delivery_execution'], 'internal': True, 'internal_task': task['id']}
    assert internal['manager_execution_id']
    return task, source, internal


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_owner_delegate_result_review_exact_deploy_restart_and_replay(flow):
    db, _, chat, settings, _, scheduler, project = flow
    params, intent = await recorded(flow)
    assert (await call(scheduler, project, params, 'record_deployment_intent', release_id='candidate-1',
                       owner_deployment_quote='deploy pending candidate after recheck'))['id'] == intent['id']
    task, source, internal = await completed_audit(flow, params, intent)
    scheduler.intents = Intents(Database(db.path))  # Process reopening retains source/scope.
    evidence = await call(scheduler, project, internal, 'read_result', execution_id=source['id'])
    assert evidence['output'].startswith('Reviewed candidate-1')
    prompt, _ = chat.prompt(chat.conversation(project), 'internal', 'auto', internal['manager_execution_id'], 'new', None)
    assert prompt['deployment_intents'][0]['id'] == intent['id']
    assert prompt['worker_result']['task_id'] == task['id']
    with pytest.raises(ValueError, match='review'):
        await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    await call(scheduler, project, internal, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='Exact coverage and validation evidence accepted')
    receipt = await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    assert receipt['phase'] == 'waiting_idle'
    assert receipt['origin']['owner_message_id'] == intent['source_message_id']
    assert receipt['origin']['candidate'] == intent['candidate']
    scheduler.deployment.acknowledge(db)
    assert scheduler.deployment.status()['phase'] == 'waiting_idle'  # Result turn still active.
    assert await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-1') == receipt
    assert Handoff(settings.data_dir).status() == receipt
    assert db.one("SELECT count(*) AS n FROM events WHERE kind='deployment.intent.consumed'")['n'] == 1
    with pytest.raises(ValueError, match='Different release'):
        await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-2')


@pytest.mark.asyncio
async def test_internal_missing_source_and_scope_guards(flow):
    db, registry, chat, _, _, scheduler, project = flow
    params = await owner(chat, project)
    for internal in (False, True):
        with pytest.raises(ValueError, match='missing|not owner authorization'):
            await call(scheduler, project, {**params, 'internal': internal}, 'deploy_release', release_id='candidate-1')
    with pytest.raises(ValueError, match='owner message'):
        await call(scheduler, project, {**params, 'internal': True}, 'record_deployment_intent', release_id='candidate-1', owner_deployment_quote='deploy')
    with pytest.raises(ValueError, match='Quote'):
        await call(scheduler, project, params, 'record_deployment_intent', release_id='candidate-1', owner_deployment_quote='worker said authorized')
    with pytest.raises(ValueError, match='owner authorization'):
        await call(scheduler, project, {**params, 'internal': True}, 'resume_project')
    intent = await call(scheduler, project, params, 'record_deployment_intent', release_id='candidate-1', owner_deployment_quote='deploy')
    other = next(p['id'] for p in registry.list() if p['id'] != project)
    with pytest.raises(ValueError, match='only'):
        await call(scheduler, other, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    with pytest.raises(ValueError, match='Different release'):
        await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-2', reason='unrelated')
    foreign = chat.conversation(other)
    with pytest.raises(ValueError, match='conversation'):
        scheduler.intents.dispatch(project, {**params, 'conversation_id': foreign['id']}, intent['id'], 'candidate-1')
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET content='altered' WHERE id=?", (intent['source_message_id'],))
    with pytest.raises(ValueError, match='source'):
        scheduler.intents.review(project, params, intent['id'], 'candidate-1', 'review')
    assert scheduler.deployment.status()['phase'] == 'idle'


@pytest.mark.asyncio
@pytest.mark.parametrize('correction', ['cancel deployment', 'wait', 'pause', 'deploy only a different scope'])
async def test_queued_corrections_hold_then_owner_revokes(flow, correction):
    db, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    newer = await owner(chat, project, correction)
    assert scheduler.intents.list(project)[0]['needs_owner_review']
    for action in ('review_deployment_intent', 'deploy_release'):
        with pytest.raises(ValueError, match='newer owner'):
            await call(scheduler, project, params, action, intent_id=intent['id'], release_id='candidate-1', reason='old review')
    with pytest.raises(ValueError, match='owner message'):
        await call(scheduler, project, {**newer, 'internal': True}, 'revise_deployment_intent', intent_id=intent['id'], decision='preserve', reason='worker wants it')
    await call(scheduler, project, newer, 'revise_deployment_intent', intent_id=intent['id'], decision='revoke', reason=correction)
    with pytest.raises(ValueError, match='unused'):
        await call(scheduler, project, newer, 'revise_deployment_intent', intent_id=intent['id'], decision='preserve', reason='replay')
    finish_owner(db, params)
    assert scheduler.deployment.status()['phase'] == 'idle'


@pytest.mark.asyncio
async def test_latest_owner_can_preserve_same_scope_but_not_older_turn(flow):
    db, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    second = await owner(chat, project, 'recheck validation before deployment')
    third = await owner(chat, project, 'same candidate only; keep going')
    with pytest.raises(ValueError, match='latest owner'):
        await call(scheduler, project, second, 'revise_deployment_intent', intent_id=intent['id'], decision='preserve', reason='same scope')
    await call(scheduler, project, third, 'revise_deployment_intent', intent_id=intent['id'], decision='preserve', reason='Latest correction retains original exact candidate')
    await call(scheduler, project, third, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
    assert (await call(scheduler, project, third, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'))['phase'] == 'waiting_idle'
    assert db.setting('deployment_intent:'+intent['id'])['candidate'] == intent['candidate']


@pytest.mark.asyncio
@pytest.mark.parametrize('event', ['project_pause', 'interrupt', 'task_pause', 'task_cancel', 'task_wait'])
async def test_holds_revoke_without_resume_permission(flow, event):
    db, registry, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    task = await call(scheduler, project, params, 'delegate', objective='Audit', intent_id=intent['id'])
    if event == 'project_pause':
        registry.update(project, registry.get(project)['revision'], {'status': 'paused'})
    elif event == 'interrupt':
        await chat.interrupt(params['manager_execution_id'])
    elif event == 'task_wait':
        await call(scheduler, project, params, 'wait', task_id=task['id'], reason='Owner says wait')
    else:
        scheduler.tasks.control(task['id'], event.removeprefix('task_'), task['revision'])
    assert db.setting('deployment_intent:'+intent['id'])['status'] == 'revoked'
    assert scheduler.deployment.status()['phase'] == 'idle'
    assert registry.get(project)['status'] == ('paused' if event == 'project_pause' else 'active')


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_scope', [False, True])
async def test_assignment_preparation_seals_exact_scope_and_rejects_wrong_manifest(flow, changed_scope):
    db, _, chat, _, workers, scheduler, project = flow
    params = await owner(chat, project, 'implement this and deploy after validation')
    task = await call(scheduler, project, params, 'delegate', objective='Implement scoped update', owner_deployment_quote='deploy after validation')
    intent_id = task['deployment_intent_id']
    intent = db.setting('deployment_intent:'+intent_id)
    assert intent['status'] == 'recorded' and intent['candidate'] is None
    finish_owner(db, params)
    claim = scheduler.tasks.claim(2)
    with db.connect() as conn:
        assignments = {task['id']: assignment_evidence(conn, conn.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone())}
    prepared(scheduler.deployment, 'prepared-from-assignment', assignments)
    workers.finish(claim['lease_owner'], 'completed', 'prepared-from-assignment', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
    await chat.deliver_results()
    source = db.one('SELECT * FROM executions WHERE id=?', (claim['lease_owner'],))
    internal = {**params, 'internal': True, 'manager_execution_id': source['delivery_execution']}
    with pytest.raises(ValueError, match='fixed assignment'):
        await call(scheduler, project, internal, 'review_deployment_intent', intent_id=intent_id, release_id='candidate-1', reason='unrelated')
    await call(scheduler, project, internal, 'review_deployment_intent', intent_id=intent_id, release_id='prepared-from-assignment', reason='Exact assignment, code and validation reviewed')
    if changed_scope:
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET description='new scope' WHERE id=?", (task['id'],))
        with pytest.raises(ValueError, match='scope changed'):
            await call(scheduler, project, internal, 'deploy_release', intent_id=intent_id, release_id='prepared-from-assignment')
    else:
        assert (await call(scheduler, project, internal, 'deploy_release', intent_id=intent_id, release_id='prepared-from-assignment'))['phase'] == 'waiting_idle'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['failed_preparation', 'bundle', 'manifest', 'dispatch'])
async def test_failed_preparation_mutation_dispatch_do_not_authorize_retries(flow, fault, monkeypatch):
    db, _, _, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    task, _, internal = await completed_audit(flow, params, intent)
    root = scheduler.deployment.root/'releases/candidate-1'
    if fault == 'failed_preparation':
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET lifecycle='failed' WHERE id=?", (task['id'],))
        with pytest.raises(ValueError, match='failed'):
            await call(scheduler, project, internal, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
        return
    await call(scheduler, project, internal, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
    if fault == 'bundle':
        (root/'release.bundle').write_bytes(b'changed')
    elif fault == 'manifest':
        value = read(root/'manifest.json')
        value['commit'] = 'c'*40
        atomic(root/'manifest.json', value)
    else:
        def rejected(*args):
            raise ValueError('Another deployment is pending')
        monkeypatch.setattr(scheduler.intents.handoff, 'request', rejected)
        assert (await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'))['phase'] == 'dispatch_failed'
        assert db.setting('deployment_intent:'+intent['id'])['status'] == 'dispatch_failed'
    with pytest.raises(ValueError):
        await call(scheduler, project, internal, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    assert scheduler.deployment.status()['phase'] == 'idle'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['failed', 'rolled_back', 'rollback_failed', 'cancelled', 'succeeded'])
async def test_crash_gap_and_terminal_replay_never_dispatches_again(flow, phase, monkeypatch):
    db, _, _, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
    receipt = await call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    # Model a crash after protocol write before SQL consumption commit, then a
    # later unrelated active protocol file. The retained nonce remains decisive.
    value = db.setting('deployment_intent:'+intent['id'])
    value['status'] = 'reviewed'
    value.pop('nonce')
    db.set_setting('deployment_intent:'+intent['id'], value)
    receipt['phase'] = phase
    atomic(scheduler.deployment.root/'results'/(receipt['nonce']+'.json'), receipt)
    atomic(scheduler.deployment.root/'active.json', {'phase': 'waiting_idle', 'release_id': 'candidate-2', 'nonce': 'other'})
    scheduler.intents = Intents(Database(db.path))
    monkeypatch.setattr(scheduler.intents.handoff, 'request', lambda *args: pytest.fail('Replay attempted a deployment'))
    first, second = await asyncio.gather(
        call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'),
        call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'))
    assert first == second == receipt
    assert db.setting('deployment_intent:'+intent['id'])['nonce'] == receipt['nonce']


@pytest.mark.asyncio
async def test_recording_precedes_async_delegate_failure(flow, monkeypatch):
    db, _, chat, _, _, scheduler, project = flow
    params = await owner(chat, project, 'prepare this update and deploy it')
    async def failed_start():
        assert any(v['source_message_id'] for v in scheduler.intents.list(project))
        raise RuntimeError('transport unavailable')
    monkeypatch.setattr(chat.server, 'start', failed_start)
    with pytest.raises(RuntimeError):
        await call(scheduler, project, params, 'delegate', objective='Scoped preparation', owner_deployment_quote='deploy it')
    assert not db.all('SELECT * FROM tasks')
    intent = scheduler.intents.list(project)[0]
    with pytest.raises(ValueError, match='incomplete'):
        await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='missing preparation')


@pytest.mark.asyncio
async def test_global_queued_correction_blocks_project_intent(flow):
    _, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    await owner(chat, None, 'wait for all deployments')
    with pytest.raises(ValueError, match='newer owner'):
        await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')


@pytest.mark.asyncio
async def test_native_manager_tool_routing_preserves_owner_intent_on_result_turn(flow):
    import json

    from test_recovery import settle

    db, registry, chat, settings, workers, scheduler, project = flow

    class RoutingServer(FakeServer):
        intent = None
        task = None
        receipt = None

        async def request(self, method, params, timeout=30):
            if method == 'turn/start':
                prompt = json.loads(params['input'][0]['text'])
                handler = self.tool_handlers[params['threadId']]

                async def routed(action, **args):
                    return await handler({'tool': 'manager_action', 'callId': action,
                                          'arguments': {'action': action, **args}})

                if 'worker_result' not in prompt:
                    self.intent = await routed('record_deployment_intent', release_id='candidate-1', owner_deployment_quote='deploy candidate after audit')
                    self.task = await routed('delegate', objective='Audit the exact release', intent_id=self.intent['id'])
                else:
                    with pytest.raises(ValueError, match='owner message'):
                        await routed('record_deployment_intent', release_id='candidate-2', owner_deployment_quote='deploy')
                    await routed('read_result', execution_id=prompt['worker_result']['execution_id'])
                    await routed('review_deployment_intent', intent_id=self.intent['id'], release_id='candidate-1', reason='Exact candidate evidence accepted after native result retrieval')
                    self.receipt = await routed('deploy_release', intent_id=self.intent['id'], release_id='candidate-1')
            return await super().request(method, params, timeout)

    chat.server = RoutingServer(settings)
    chat.launch = type(chat).launch.__get__(chat)
    await chat.submit(project, 'deploy candidate after audit')
    await settle(chat)
    assert chat.server.task
    claim = scheduler.tasks.claim(2)
    workers.finish(claim['lease_owner'], 'completed', 'candidate-1 exact coverage', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (chat.server.task['id'],))
    await chat.deliver_results()
    await settle(chat)
    assert chat.server.receipt['phase'] == 'waiting_idle'
    assert chat.server.receipt['origin']['intent_id'] == chat.server.intent['id']
    assert db.one('SELECT delivery FROM executions WHERE id=?', (claim['lease_owner'],))['delivery'] == 'delivered'
    assert registry.get(project)['status'] == 'active'


def test_intent_settings_revocation_auth_csrf_restart(tmp_path):
    from fastapi.testclient import TestClient
    from test_web import sign_in

    from ai_manager.config import Settings
    from ai_manager.web import create_app

    settings = Settings(data_dir=tmp_path/'data', project_roots=(tmp_path,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as client:
        identifier = 'a'*32
        assert client.post('/api/deployment/intents/revoke', json={'intent_id': identifier}).status_code == 401
        sign_in(client, app, settings)
        assert client.post('/api/deployment/intents/revoke', headers={'X-CSRF-Token': 'invalid'}, json={'intent_id': identifier}).status_code == 403
        assert client.post('/api/deployment/intents/revoke', json={'intent_id': '../arbitrary'}).status_code == 422
        assert client.post('/api/deployment/intents/revoke', json={'intent_id': identifier}).status_code == 409
        app.state.db.set_setting('deployment_intent:'+identifier, {'id': identifier, 'status': 'recorded', 'project_id': 'fixture', 'conversation_id': 'fixture', 'task_ids': []})
        assert client.post('/api/deployment/intents/revoke', json={'intent_id': identifier}).status_code == 200
        assert Database(settings.db_path).setting('deployment_intent:'+identifier)['status'] == 'revoked'
        assert app.state.scheduler.deployment.status()['phase'] == 'idle'
        app.state.db.set_setting('deployment_intent:'+identifier, {'id': identifier, 'status': 'dispatched'})
        assert client.post('/api/deployment/intents/revoke', json={'intent_id': identifier}).status_code == 409


@pytest.mark.asyncio
async def test_owner_revocation_recovers_dispatch_crash_gap_before_idle_ack(flow):
    from ai_manager.deployment_intent import revoke

    db, _, _, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
    receipt = await call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    value = db.setting('deployment_intent:'+intent['id'])
    value['status'] = 'reviewed'  # SQL commit lost after durable protocol request.
    db.set_setting('deployment_intent:'+intent['id'], value)
    with db.transaction() as conn:
        revoke(db, conn, 'Owner cancelled from Settings', intent_id=intent['id'])
    assert scheduler.deployment.status()['phase'] == 'cancelled'
    assert read(scheduler.deployment.root/'results'/(receipt['nonce']+'.json'))['phase'] == 'cancelled'
    assert db.setting('deployment_intent:'+intent['id'])['status'] == 'revoked'
    scheduler.deployment.acknowledge(db)
    assert scheduler.deployment.status()['phase'] == 'cancelled'
    assert (await call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'))['phase'] == 'cancelled'
    assert db.setting('deployment_intent:'+intent['id'])['status'] == 'revoked'


@pytest.mark.asyncio
async def test_recovery_review_turn_consumes_retained_exact_intent(flow):
    from test_assignment_workflow import make_due

    db, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    task, source, internal = await completed_audit(flow, params, intent)
    finish_owner(db, internal)
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET delivery='delivered' WHERE id=?", (source['id'],))
    # Manual OFF flow may have concrete unfinished activation follow-up. Success itself no longer schedules review.
    from ai_manager.assignment_workflow import update
    with db.transaction() as conn:
        update(db, conn, task['id'], 'actionable', 'Retained explicit manual activation obligation')
    assert make_due(db, task)[0]['id'] == task['id']
    receipt = await chat.submit(project, 'internal recovery', params['conversation_id'], recovery_task=task['id'])
    recovery = {**params, 'internal': True, 'internal_task': task['id'], 'manager_execution_id': receipt['execution_id']}
    prompt, _ = chat.prompt(chat.conversation(project), 'recovery', 'auto', receipt['execution_id'], 'new', None)
    assert 'No new deployment authorization' in prompt['message']
    assert prompt['deployment_intents'][0]['id'] == intent['id']
    await call(scheduler, project, recovery, 'read_result', execution_id=source['id'])
    await call(scheduler, project, recovery, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='Recovered reviewed preparation evidence')
    assert (await call(scheduler, project, recovery, 'deploy_release', intent_id=intent['id'], release_id='candidate-1'))['phase'] == 'waiting_idle'


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['preserve', 'revoke'])
async def test_latest_global_owner_can_revise_project_intent_without_changing_origin(flow, decision):
    db, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    global_owner = await owner(chat, None, 'keep the same exact candidate' if decision == 'preserve' else 'cancel pending deployment')
    revised = await call(scheduler, None, global_owner, 'revise_deployment_intent', project_id=project,
                         intent_id=intent['id'], decision=decision, reason='Actual latest All Projects owner correction')
    assert revised['conversation_id'] == params['conversation_id']
    assert revised['source_message_id'] == intent['source_message_id']
    if decision == 'revoke':
        assert revised['status'] == 'revoked'
    else:
        await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='Same scope reviewed after global owner correction')
        receipt = await call(scheduler, project, params, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
        assert receipt['origin']['conversation_id'] == params['conversation_id']
    assert db.setting('deployment_intent:'+intent['id'])['source_execution_id'] == params['manager_execution_id']


@pytest.mark.asyncio
async def test_editing_preserved_latest_correction_holds_dispatch(flow):
    db, _, chat, _, _, scheduler, project = flow
    params, intent = await recorded(flow)
    latest = await owner(chat, project, 'same exact scope; keep deploying')
    revised = await call(scheduler, project, latest, 'revise_deployment_intent', intent_id=intent['id'], decision='preserve', reason='Same scope')
    await call(scheduler, project, latest, 'review_deployment_intent', intent_id=intent['id'], release_id='candidate-1', reason='checked')
    with db.transaction() as conn:
        conn.execute("UPDATE messages SET content='wait' WHERE id=?", (revised['checked_owner_message'],))
    assert scheduler.intents.list(project)[0]['needs_owner_review']
    with pytest.raises(ValueError, match='edited correction'):
        await call(scheduler, project, latest, 'deploy_release', intent_id=intent['id'], release_id='candidate-1')
    assert scheduler.deployment.status()['phase'] == 'idle'


@pytest.mark.asyncio
async def test_unchanged_resume_keeps_v2_owner_scope_and_prepared_binding(flow):
    db, _, chat, _, workers, scheduler, project = flow
    params = await owner(chat, project, 'implement this and deploy after validation')
    task = await call(scheduler, project, params, 'delegate', objective='Implement scoped update',
                      owner_deployment_quote='deploy after validation')
    intent_id = task['deployment_intent_id']
    claim = scheduler.tasks.claim(2)
    workers.finish(claim['lease_owner'], 'completed', 'initial validation', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
    with db.connect() as conn:
        bound = assignment_evidence(conn, conn.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone())
    prepared(scheduler.deployment, 'stable-release', {task['id']: bound})
    finish_owner(db, params)
    resume = await owner(chat, project, 'Resume unchanged work and preserve existing deployment scope')
    await call(scheduler, project, resume, 'continue', task_id=task['id'])
    await call(scheduler, project, resume, 'revise_deployment_intent', intent_id=intent_id,
               decision='preserve', reason='Same requirements, lifecycle-only continuation')
    claim = scheduler.tasks.claim(2)
    workers.finish(claim['lease_owner'], 'completed', 'same candidate evidence', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (task['id'],))
    result = await call(scheduler, project, resume, 'review_deployment_intent', intent_id=intent_id,
                        release_id='stable-release', reason='Stable scope and candidate checked')
    assert result['status'] == 'reviewed'
    assert result['assignment_hashes'] == {task['id']: bound}
    assert scheduler.deployment.status()['phase'] == 'idle'  # Review is not activation.


@pytest.mark.parametrize('version', [1, 2])
async def test_source_scope_remains_immutable_when_review_task_is_attached(flow, version):
    db, _, chat, _, workers, scheduler, project = flow
    initial = await owner(chat, project, 'Implement source')
    source_task = await call(scheduler, project, initial, 'delegate', objective='Source work')
    finish_owner(db, initial)
    claim = scheduler.tasks.claim(2)
    workers.finish(claim['lease_owner'], 'completed', 'Source complete', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (source_task['id'],))
    params = await owner(chat, project, 'Deploy this source after audit')
    intent = await call(scheduler, project, params, 'record_deployment_intent', task_ids=[source_task['id']],
                        owner_deployment_quote='Deploy this source after audit')
    if version == 1:
        with db.transaction() as conn:
            intent['evidence_version'] = 1
            source = conn.execute('SELECT * FROM tasks WHERE id=?', (source_task['id'],)).fetchone()
            intent['assignment_hashes'] = {source_task['id']: assignment_evidence(conn, source, 1)}
            conn.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(intent), 'deployment_intent:' + intent['id']))
    fixed_hashes = dict(intent['assignment_hashes'])
    prepared(scheduler.deployment, assignments=fixed_hashes)
    manifest_path = scheduler.deployment.root / 'releases/candidate-1/manifest.json'
    manifest = read(manifest_path)
    manifest['assignment_evidence_version'] = version
    atomic(manifest_path, manifest)
    # ON must not substitute this audit branch for the owner's explicitly fixed source branch.
    scheduler.auto_deploy.set(project, True)
    review_task = await call(scheduler, project, params, 'delegate', objective='Audit requested source', intent_id=intent['id'])
    assert db.setting('deployment_intent:' + intent['id'])['assignment_hashes'] == fixed_hashes
    assert not [v for v in scheduler.intents.list(project) if v.get('kind') == 'auto']
    with pytest.raises(ValueError, match='incomplete'):
        await call(scheduler, project, params, 'review_deployment_intent', intent_id=intent['id'],
                   release_id='candidate-1', reason='Cannot accept unfinished audit')
    finish_owner(db, params)
    claim = scheduler.tasks.claim(2)
    assert claim['id'] == review_task['id']
    workers.finish(claim['lease_owner'], 'completed', 'Audit complete', '')
    with db.transaction() as conn:
        conn.execute('UPDATE tasks SET lease_owner=NULL WHERE id=?', (review_task['id'],))
    # Audit readiness is required, but adding it to the immutable source map is rejected.
    expanded = {**manifest, 'assignments': {**fixed_hashes, review_task['id']: '0' * 64}}
    atomic(manifest_path, expanded)
    with pytest.raises(ValueError, match='fixed assignment scope'):
        scheduler.intents.review(project, params, intent['id'], 'candidate-1', 'Audit is not a source')
    atomic(manifest_path, manifest)
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET status='failed' WHERE task_id=?", (review_task['id'],))
    with pytest.raises(ValueError, match='Completed preparation result'):
        scheduler.intents.review(project, params, intent['id'], 'candidate-1', 'A failed audit is not ready')
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET status='completed' WHERE task_id=?", (review_task['id'],))
    # The fixed mapping never includes the audit even once both tasks complete.
    reviewed = scheduler.intents.review(project, params, intent['id'], 'candidate-1', 'Completed audit accepted')
    assert reviewed['assignment_hashes'] == fixed_hashes
    assert reviewed['candidate']['release_id'] == 'candidate-1'
    assert read(manifest_path)['assignments'] == fixed_hashes
    with db.transaction() as conn:
        from ai_manager.assignment_workflow import update
        update(db, conn, review_task['id'], 'owner_wait', 'Owner holds the audit')
    with pytest.raises(ValueError, match='held'):
        scheduler.intents.dispatch(project, params, intent['id'], 'candidate-1')
    with db.transaction() as conn:
        update(db, conn, review_task['id'], 'complete', 'Owner released the hold')
    receipt = scheduler.intents.dispatch(project, params, intent['id'], 'candidate-1')
    assert receipt['phase'] == 'waiting_idle'
    assert receipt['origin']['owner_message_id'] == intent['source_message_id']

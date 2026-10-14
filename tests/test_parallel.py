"""Real Git worktrees plus disposable native/process fixtures. No production writes."""
import asyncio
from pathlib import Path

import pytest
from test_agent_controls import ControlledServer, Controls
from test_minimal_flow import NativeFixture, settled
from test_minimal_flow import system as system_fixture

from ai_manager.chat import Chat
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.parallel import INTEGRATION, git, policy, preparation_reason, reserve_integration, retain_source, save
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers

system = system_fixture


@pytest.fixture
def repo(system):
    db, registry, chat, settings = system
    project = registry.list()[0]
    root = Path(project['path'])
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Disposable')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root/'base.txt').write_text('original\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'base')
    return db, registry, chat, settings, project


def p(paths, permission=False, safe=True, dependencies=()):
    return {'permission': {'source': 'fixture'} if permission else {}, 'parallel_safe': safe,
            'change_paths': paths, 'depends_on': list(dependencies), 'reason': 'Assessment says sequence' if not safe else ''}


def done(db, task, filename=None, text='changed\n'):
    if filename:
        root = Path(task['workspace'])
        (root/filename).write_text(text)
        git(root, 'add', filename)
        git(root, 'commit', '-m', filename)
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='completed',lease_owner=NULL WHERE id=?", (task['id'],))
        conn.execute("UPDATE executions SET status='completed',finished_at=1 WHERE id=?", (task['lease_owner'],))
        retain_source(db, conn, task['id'])


def test_default_fifo_optin_capacity_real_worktrees_and_combined_merge(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    first = tasks.add(project['id'], 'first', execution_policy=p(['first.txt']))
    a = tasks.claim(2)
    queued = tasks.add(project['id'], 'ordinary next', execution_policy=p(['third.txt']))
    opted = tasks.add(project['id'], 'independent opt in', execution_policy=p(['second.txt'], True))
    b = tasks.claim(2)
    assert a['id'] == first['id'] and b['id'] == opted['id']
    assert tasks.claim(2) is None
    assert tasks.get(queued['id'])['status'] == 'queued'
    assert a['workspace'] != b['workspace'] != project['path']
    assert a['workspace_branch'] != b['workspace_branch']
    done(db, a, 'first.txt')
    assert tasks.claim(2) is None  # ordinary waits for B, even after A completed
    done(db, b, 'second.txt')
    assert tasks.claim(2) is None  # committed sources need combined integration
    with db.transaction() as conn:
        reserve_integration(db, conn, tasks.get(a['id']), [b['id']])
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?", (a['id'],))
    integrated = tasks.claim(2)
    assert integrated['id'] == a['id'] and integrated['workspace'] == a['workspace']
    git(a['workspace'], 'merge', '--no-ff', b['workspace_branch'], '-m', 'combined')
    done(db, integrated)
    with db.transaction() as conn:
        from ai_manager.parallel import release_integration
        release_integration(conn, a['id'])
    following = tasks.claim(2)
    assert following['id'] == queued['id']
    assert (Path(following['workspace'])/'first.txt').read_text() == 'changed\n'
    assert (Path(following['workspace'])/'second.txt').read_text() == 'changed\n'
    assert not (Path(project['path'])/'first.txt').exists()
    assert db.setting('assignment_source:' + b['id'])['state'] == 'integrated'


@pytest.mark.parametrize('other_paths,safe,reason', [(['src/a'], True, 'overlapping'), ([], True, 'incomplete'), (['other'], False, 'Assessment')])
def test_conflict_unknown_scope_assessment_queue_with_reason(repo, other_paths, safe, reason):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'current', execution_policy=p(['src']))
    tasks.claim(3)
    queued = tasks.add(project['id'], 'opt in', execution_policy=p(other_paths, True, safe))
    assert tasks.claim(3) is None
    assert reason in tasks.get(queued['id'])['blocker']


def test_dirty_non_git_historical_and_project_holds_fail_closed(repo):
    db, registry, _, _, project = repo
    tasks = Tasks(db)
    (Path(project['path'])/'owner-draft').write_text('retain')
    queued = tasks.add(project['id'], 'opt in dirty', execution_policy=p(['safe'], True))
    assert tasks.claim(3) is None
    assert 'clean committed' in tasks.get(queued['id'])['blocker']
    ordinary = tasks.add(project['id'], 'normal dirty')
    # cancel earlier opted request to exercise ordinary fallback
    tasks.control(queued['id'], 'cancel', tasks.get(queued['id'])['revision'])
    claimed = tasks.claim(3)
    assert claimed['id'] == ordinary['id'] and claimed['workspace'] is None
    second = tasks.add(project['id'], 'independent', execution_policy=p(['safe'], True))
    assert tasks.claim(3) is None
    assert 'shared checkout' in tasks.get(second['id'])['blocker']
    assert (Path(project['path'])/'owner-draft').read_text() == 'retain'
    with db.transaction() as conn:
        conn.execute("UPDATE projects SET status='paused' WHERE id=?", (project['id'],))
    assert tasks.claim(3) is None
    assert registry.get(project['id'])['status'] == 'paused'


def test_dependency_and_dedup_do_not_upgrade_existing_permission(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    first = tasks.add(project['id'], 'first', request_key='same', execution_policy=p(['one']))
    again = tasks.add(project['id'], 'first', request_key='same', execution_policy=p(['two'], True))
    assert first['id'] == again['id'] and not again['execution_policy']['permission']
    a = tasks.claim(2)
    dependent = tasks.add(project['id'], 'depends', execution_policy=p(['other'], True, dependencies=[a['id']]))
    assert tasks.claim(2) is None
    assert 'dependency' in tasks.get(dependent['id'])['blocker']
    done(db, a, 'one')
    assert tasks.claim(2)['id'] == dependent['id']


@pytest.mark.asyncio
async def test_request_permission_receipt_restart_default_and_owner_quote(repo):
    db, registry, chat, settings, project = repo
    receipt = await chat.submit(project['id'], 'Implement independent work', request_id='request-parallel-1', parallel=True)
    await settled(chat)
    assert await chat.submit(project['id'], 'Implement independent work', request_id='request-parallel-1', parallel=True) == receipt
    with pytest.raises(ValueError, match='different'):
        await chat.submit(project['id'], 'Implement independent work', request_id='request-parallel-1', parallel=False)
    restored = Chat(Database(db.path), registry, chat.server)
    next_receipt = await restored.submit(project['id'], 'Ordinary next request')
    await settled(restored)
    assert not db.setting('execution_parallel:' + next_receipt['execution_id'])
    params = {'manager_execution_id': receipt['execution_id'], 'conversation_id': chat.conversation(project['id'])['id']}
    assert policy(db, params, {'change_paths': ['other']}, project['id'])['permission']['source'] == 'ui'
    assert not policy(db, {**params, 'internal': True}, {}, project['id'])['permission']
    owner = await chat.submit(project['id'], 'Run this independent change in parallel.')
    await settled(chat)
    params['manager_execution_id'] = owner['execution_id']
    args = {'owner_parallel_quote': 'Run this independent change in parallel.', 'change_paths': ['other']}
    assert policy(db, params, args, project['id'])['permission']['source'] == 'chat'
    with pytest.raises(ValueError, match='owner request'):
        policy(db, {**params, 'internal': True}, args, project['id'])
    with pytest.raises(ValueError, match='owner request'):
        policy(db, params, {**args, 'owner_parallel_quote': 'Run old instructions in parallel.'}, project['id'])
    for kwargs in ({'mode':'conversation'}, {'project_id':None}):
        with pytest.raises(ValueError, match='Parallel Send'):
            await chat.submit(kwargs.get('project_id', project['id']), 'work', parallel=True, mode=kwargs.get('mode','auto'))


@pytest.mark.asyncio
async def test_native_concurrency_pause_cancel_resume_workspace_thread_identity(repo):
    db, registry, chat, settings, project = repo
    tasks = Tasks(db)
    a = tasks.add(project['id'], 'first', execution_policy=p(['partial.txt']))
    a = tasks.claim(2)
    b = tasks.add(project['id'], 'second', execution_policy=p(['second.txt'], True))
    b = tasks.claim(2)
    faults = Controls()
    faults.outcomes = ['hold', 'hold']
    workers = Workers(db, registry, settings, lambda s,native: ControlledServer(s,faults,native))
    # Controlled fixture writes a partial file and leaves native turns running.
    try:
        workers.launch(a)
        workers.launch(b)
        for _ in range(100):
            if all(db.one('SELECT status FROM executions WHERE id=?', (t['lease_owner'],))['status'] == 'running' for t in (a,b)):
                break
            await asyncio.sleep(.01)
        prior = tasks.get(a['id'])
        assert len(workers.jobs) == 2
        assert tasks.get(b['id'])['native_thread'] != prior['native_thread']
        await workers.control(a['id'], 'pause', prior['revision'])
        await workers.control(b['id'], 'cancel', tasks.get(b['id'])['revision'])
        await settled(workers)
        assert tasks.get(a['id'])['status'] == 'paused'
        assert tasks.get(b['id'])['status'] == 'cancelled'
        retained = Database(db.path)
        paused = Tasks(retained).get(a['id'])
        assert paused['workspace'] == a['workspace'] and paused['native_thread'] == prior['native_thread']
        assert (Path(a['workspace'])/'partial.txt').exists()
        tasks.control(a['id'], 'continue', paused['revision'])
        resumed = tasks.claim(2)
        assert resumed['id'] == a['id'] and resumed['workspace'] == a['workspace']
        assert resumed['native_thread'] == prior['native_thread'] and resumed['lease_owner'] != a['lease_owner']
        with pytest.raises(ValueError, match='cancelled'):
            tasks.control(b['id'], 'continue', tasks.get(b['id'])['revision'])
    finally:
        await workers.close()


def test_missing_changed_workspace_never_redirects_to_shared_checkout(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'one', execution_policy=p(['one']))
    task = tasks.claim(2)
    done(db, task)
    tasks.control(task['id'], 'continue', tasks.get(task['id'])['revision'])
    git(task['workspace'], 'switch', '--detach')
    assert tasks.claim(2) is None
    assert 'branch changed' in tasks.get(task['id'])['blocker'] or 'Git operation' in tasks.get(task['id'])['blocker']
    assert git(project['path'], 'symbolic-ref', '--short', 'HEAD') == 'main'


def test_migration_keeps_all_old_columns_claims_and_is_repeatable(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    first = tasks.add(project['id'], 'shared historical')
    # Make an actual schema11-shaped copy, preserving preexisting task/native identity.
    with db.transaction() as conn:
        conn.execute('DROP INDEX workspace_worker')
        conn.execute('DROP INDEX project_worker')
        for column in ('workspace','workspace_branch','workspace_base'):
            conn.execute('ALTER TABLE tasks DROP COLUMN ' + column)
        conn.execute("CREATE UNIQUE INDEX project_worker ON tasks(project_id) WHERE lifecycle='running'")
        conn.execute('DELETE FROM schema_version WHERE version=12')
        conn.execute("UPDATE tasks SET lifecycle='running',lease_owner='unresolved',native_thread='exact-native' WHERE id=?", (first['id'],))
    before = db.all('SELECT * FROM tasks')
    migrated = Database(db.path)
    for row in migrated.all('SELECT * FROM tasks'):
        assert {key:row[key] for key in before[0]} == before[0]
    assert Tasks(migrated).claim(2) is None
    assert Database(db.path).all('SELECT * FROM tasks') == migrated.all('SELECT * FROM tasks')
    assert not migrated.all('PRAGMA foreign_key_check')


def test_integration_reservation_and_preparation_hold_survive_restart(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'source', execution_policy=p(['one']))
    task = tasks.claim(2)
    done(db, task, 'one')
    with db.transaction() as conn:
        reserve_integration(db, conn, tasks.get(task['id']), [task['id']])
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?", (task['id'],))
    restored = Database(db.path)
    assert restored.setting(INTEGRATION)['task_id'] == task['id']
    queued = tasks.add(project['id'], 'next', execution_policy=p(['other'], True))
    claimed = tasks.claim(2)
    assert claimed['id'] == task['id'] and tasks.claim(2) is None
    assert 'integration' in tasks.get(queued['id'])['blocker']
    with db.transaction() as conn:
        import os

        from ai_manager.codex import process_start
        save(conn, 'release_preparation', {'pid':os.getpid(),'process_start':process_start(os.getpid())})
        assert preparation_reason(conn)
        with pytest.raises(ValueError, match='Release preparation'):
            reserve_integration(db, conn, tasks.get(task['id']), [task['id']])


@pytest.mark.asyncio
async def test_manager_transport_refinement_stays_on_same_assignment(repo):
    db, registry, chat, settings, project = repo
    workers = Workers(db, registry, settings, NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    owner = await chat.submit(project['id'], 'Independent first request', parallel=True)
    await settled(chat)
    params = {'tool':'manager_action','callId':'delegate','manager_execution_id':owner['execution_id'],
              'conversation_id':chat.conversation(project['id'])['id'],'mode':'auto'}
    # Invoke as a still-running native tool; completed manager executions are guarded.
    with db.transaction() as conn:
        conn.execute('UPDATE executions SET finished_at=NULL WHERE id=?', (owner['execution_id'],))
    first = await scheduler.manager_action(project['id'], {**params,'arguments':{'action':'delegate','objective':'Work','change_paths':['one'],'parallel_safe':True}})
    task = scheduler.tasks.claim(2)
    done(db, task)
    result = await scheduler.manager_action(project['id'], {**params,'arguments':{'action':'continue','task_id':first['id'],'objective':'Refine this work'}})
    assert result['id'] == first['id'] and result['workspace'] == task['workspace']
    assert len(scheduler.tasks.list(project['id'])) == 1


@pytest.mark.asyncio
async def test_restart_recovers_parallel_assignments_in_exact_threads_workspaces(repo):
    from test_recovery import Faults, eligible, settle
    db, registry, _, settings, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'first', execution_policy=p(['one']))
    a = tasks.claim(2)
    tasks.add(project['id'], 'second', execution_policy=p(['two'], True))
    b = tasks.claim(2)
    faults = Faults(['hold','hold','success','success'])
    workers = Workers(db, registry, settings, faults.factory)
    workers.launch(a)
    workers.launch(b)
    for _ in range(100):
        if len(faults.calls) == 2:
            break
        await asyncio.sleep(.01)
    saved = {t['id']: tasks.get(t['id']) for t in (a,b)}
    await workers.close()
    eligible(db)
    restored = Workers(Database(db.path), registry, settings, faults.factory)
    await restored.recover()
    await settle(restored)
    for original in (a,b):
        retained = tasks.get(original['id'])
        assert retained['workspace'] == original['workspace']
        assert retained['native_thread'] == saved[original['id']]['native_thread']
        assert retained['status'] == 'completed'
        execution = db.one('SELECT * FROM executions WHERE id=?', (original['lease_owner'],))
        assert execution['worktree'] == original['workspace']
    assert len(db.all('SELECT id FROM executions WHERE task_id IS NOT NULL')) == 2
    assert len(faults.calls) == 4
    assert set(faults.resumed) == {t['native_thread'] for t in saved.values()}


def test_actual_merge_conflict_keeps_sources_and_sequential_resolution(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'one', execution_policy=p(['one']))
    a = tasks.claim(2)
    tasks.add(project['id'], 'two', execution_policy=p(['two'],True))
    b = tasks.claim(2)
    # Simulate an unexpected actual conflict despite declared disjoint scopes.
    done(db,a,'base.txt','source A\n')
    done(db,b,'base.txt','source B\n')
    source_b = git(b['workspace'],'rev-parse','HEAD')
    with db.transaction() as conn:
        reserve_integration(db,conn,tasks.get(a['id']),[b['id']])
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?",(a['id'],))
    combined = tasks.claim(2)
    with pytest.raises(ValueError,match='Git operation'):
        git(a['workspace'],'merge','--no-ff',source_b,'-m','combine')
    assert git(b['workspace'],'rev-parse','HEAD') == source_b
    assert '<<<<<<<' in (Path(a['workspace'])/'base.txt').read_text()
    assert tasks.claim(2) is None
    (Path(a['workspace'])/'base.txt').write_text('Reviewed A and B\n')
    git(a['workspace'],'add','base.txt')
    git(a['workspace'],'commit','-m','Reviewed combined resolution')
    done(db,combined)
    assert db.setting('assignment_source:'+b['id'])['state']=='integrated'
    assert git(b['workspace'],'rev-parse','HEAD') == source_b
    assert (Path(project['path'])/'base.txt').read_text() == 'original\n'


def test_paused_integration_resumes_pinned_source_with_retained_partial_merge(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'], 'source', execution_policy=p(['one']))
    task = tasks.claim(2)
    done(db,task,'one')
    with db.transaction() as conn:
        reserve_integration(db,conn,tasks.get(task['id']),[task['id']])
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?",(task['id'],))
    integrated = tasks.claim(2)
    pinned = tasks.get(task['id'])['execution_policy']['sources']
    (Path(task['workspace'])/'retained-integration-edit').write_text('review this partial work')
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='paused',lease_owner=NULL WHERE id=?",(task['id'],))
        conn.execute("UPDATE executions SET status='paused',finished_at=1 WHERE id=?",(integrated['lease_owner'],))
        from ai_manager.parallel import release_integration
        release_integration(conn,task['id'])
    tasks.control(task['id'],'continue',tasks.get(task['id'])['revision'])
    resumed=tasks.claim(2)
    assert resumed['workspace']==task['workspace']
    assert resumed['execution_policy']['sources']==pinned
    assert (Path(task['workspace'])/'retained-integration-edit').read_text()=='review this partial work'


def test_new_task_keeps_later_shared_commit_and_crash_workspace_receipt(repo):
    db, _, _, _, project = repo
    tasks = Tasks(db)
    tasks.add(project['id'],'one',execution_policy=p(['one']))
    original=tasks.claim(2)
    done(db,original,'one')
    git(project['path'],'merge','--ff-only',original['workspace_branch'])
    shared=Path(project['path'])
    (shared/'later-live-change').write_text('preserve newer live commit')
    git(shared,'add','later-live-change')
    git(shared,'commit','-m','Later live change')
    tasks.add(project['id'],'next',execution_policy=p(['two']))
    follow=tasks.claim(2)
    assert (Path(follow['workspace'])/'later-live-change').exists()
    # Mimic Git completion before DB workspace/claim transaction committed.
    with db.transaction() as conn:
        conn.execute('DELETE FROM executions WHERE id=?',(follow['lease_owner'],))
        conn.execute("UPDATE tasks SET lifecycle='queued',lease_owner=NULL,workspace=NULL,workspace_branch=NULL,workspace_base=NULL WHERE id=?",(follow['id'],))
    recovered=Tasks(Database(db.path)).claim(2)
    assert recovered['id']==follow['id'] and recovered['workspace']==follow['workspace']
    assert recovered['workspace_base']==follow['workspace_base']


@pytest.mark.asyncio
async def test_lost_integration_reservation_cannot_launch_a_native_turn(repo):
    db, registry, _, settings, project=repo
    tasks=Tasks(db)
    tasks.add(project['id'],'source',execution_policy=p(['one']))
    original=tasks.claim(2)
    done(db,original,'one')
    with db.transaction() as conn:
        reserve_integration(db,conn,tasks.get(original['id']),[original['id']])
        conn.execute("UPDATE tasks SET lifecycle='queued' WHERE id=?",(original['id'],))
    claimed=tasks.claim(2)
    with db.transaction() as conn:
        conn.execute('DELETE FROM settings WHERE key=?',(INTEGRATION,))
    workers=Workers(db,registry,settings,NativeFixture)
    workers.launch(claimed)
    await settled(workers)
    result=db.one('SELECT * FROM executions WHERE id=?',(claimed['lease_owner'],))
    assert result['status']=='failed' and result['thread_id'] is None
    assert 'Integration ownership changed' in result['error']
    assert Path(original['workspace']).is_dir()


def test_quiet_backup_keeps_worktree_partial_files_receipts_and_branch_refs(repo,tmp_path):
    import tarfile

    from ai_manager.deployment import backup_workspaces
    db, _, _, settings, project=repo
    tasks=Tasks(db)
    tasks.add(project['id'],'source',execution_policy=p(['one']))
    task=tasks.claim(2)
    output=tmp_path/'workspace-backup'
    output.mkdir()
    with pytest.raises(ValueError,match='writer cleanup'):
        backup_workspaces(settings.data_dir,db.path,output)
    done(db,task,'one')
    root=Path(task['workspace'])
    (root/'partial-work').write_text('retain uncommitted work')
    (root/'external-link').symlink_to(tmp_path/'outside')
    receipt=backup_workspaces(settings.data_dir,db.path,output)
    assert receipt[task['id']]['branch']==task['workspace_branch']
    with tarfile.open(output/('workspace-'+task['id']+'.tar')) as archive:
        assert archive.extractfile(task['id']+'/partial-work').read()==b'retain uncommitted work'
        assert archive.getmember(task['id']+'/external-link').issym()
    refs=git(project['path'],'bundle','list-heads',str(output/('workspace-source-'+project['id']+'.bundle')))
    assert task['workspace_branch'] in refs
    assert (output/('workspace-'+task['id']+'.json')).exists()


@pytest.mark.asyncio
async def test_chat_permission_cannot_strip_owner_sentence_negation(repo):
    db, _, chat, _, project=repo
    receipt=await chat.submit(project['id'],'Do not run this request in parallel.')
    await settled(chat)
    params={'manager_execution_id':receipt['execution_id'],'conversation_id':chat.conversation(project['id'])['id']}
    for quote in ('run this request in parallel.','Do not run this request in parallel.'):
        with pytest.raises(ValueError,match='explicit instruction'):
            policy(db,params,{'owner_parallel_quote':quote},project['id'])
    approved=await chat.submit(project['id'],'Parallel execution is approved.')
    await settled(chat)
    params['manager_execution_id']=approved['execution_id']
    assert policy(db,params,{'owner_parallel_quote':'Parallel execution is approved.'},project['id'])['permission']['source']=='chat'


def test_live_recovering_process_keeps_global_capacity_until_confirmed_exit(repo):
    import subprocess
    import sys

    from ai_manager.codex import process_start
    from ai_manager.parallel import active_workers
    db, registry, _, _, project=repo
    tasks=Tasks(db)
    tasks.add(project['id'],'owned source',execution_policy=p(['one']))
    claimed=tasks.claim(1)
    other=next(p for p in registry.list() if p['id']!=project['id'])
    tasks.add(other['id'],'other project queued')
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE executions SET status='recovering',pid=?,process_start=? WHERE id=?",
                         (process.pid,process_start(process.pid),claimed['lease_owner']))
            assert active_workers(conn)==1
        assert tasks.claim(1) is None
        process.terminate()
        process.wait(timeout=5)
        with db.transaction() as conn:
            assert active_workers(conn)==0
        assert tasks.claim(1)['project_id']==other['id']
        assert tasks.get(claimed['id'])['lease_owner']==claimed['lease_owner']
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_integrated_helper_retains_claude_resume_history_and_requires_session(repo,tmp_path):
    import json

    from ai_manager.deployment import Helper
    db, _, chat, settings, project=repo
    thread='11111111-1111-4111-8111-111111111111'
    conversation=chat.conversation(project['id'])
    with db.transaction() as conn:
        conn.execute('UPDATE conversations SET thread_id=? WHERE id=?',(thread,conversation['id']))
    history=settings.data_dir/'claude-threads'
    history.mkdir()
    body={'id':thread,'session':True,'cwd':project['path'],'turns':[{'id':'retained-turn','status':'completed'}]}
    (history/(thread+'.json')).write_text(json.dumps(body))
    claude_native=tmp_path/'claude-native'
    sessions=claude_native/'projects'/'fixture'
    sessions.mkdir(parents=True)
    session=sessions/(thread+'.jsonl')
    session.write_text('{"retained":"native Claude resume"}\n')
    helper=Helper({'data':str(settings.data_dir),'repo':project['path'],'native':str(tmp_path/'codex-native'),
                   'claude_native':str(claude_native),'preserve_files':[]})
    receipt=helper.snapshot(tmp_path/'snapshot')
    assert receipt['claude_threads'][str(history/(thread+'.json'))]
    assert receipt['claude_sessions'][str(session)]
    assert (tmp_path/'snapshot/claude-sessions'/(thread+'.jsonl')).read_bytes()==session.read_bytes()
    session.unlink()
    with pytest.raises(ValueError,match='resume session missing'):
        helper.snapshot(tmp_path/'blocked-snapshot')


@pytest.mark.asyncio
async def test_parallel_assignments_keep_pinned_provider_after_owner_switch(repo):
    from ai_manager.claude_host import CATALOG
    from ai_manager.model_settings import delegation, set_active
    db,registry,_,settings,project=repo
    class BothNative(NativeFixture):
        def __init__(self,settings,native=False):
            super().__init__(settings,native)
            self.catalog.extend(dict(item) for item in CATALOG)
        async def thread(self,cwd,instructions,thread_id=None,model=None,dynamic_tools=None):
            return thread_id or ('claude-' if model.startswith('claude-') else 'codex-')+Path(cwd).name
    catalog=BothNative(settings)
    tasks=Tasks(db)
    first=tasks.add(project['id'],'Codex source',execution_policy=p(['one']),model_route=delegation(db,catalog))
    a=tasks.claim(2)
    set_active(db,'claude')
    second=tasks.add(project['id'],'Claude independent source',execution_policy=p(['two'],True),
                     model_route=delegation(db,catalog,{'model':'claude-sonnet-5-5','effort':'medium'}))
    b=tasks.claim(2)
    workers=Workers(db,registry,settings,BothNative)
    BothNative.release=asyncio.Event()
    try:
        workers.launch(a)
        workers.launch(b)
        for _ in range(100):
            if all(tasks.get(t['id'])['native_thread'] for t in (a,b)):
                break
            await asyncio.sleep(.01)
        assert len(workers.jobs)==2
        assert tasks.get(first['id'])['native_thread'].startswith('codex-')
        assert tasks.get(second['id'])['native_thread'].startswith('claude-')
        assert tasks.get(first['id'])['workspace']!=tasks.get(second['id'])['workspace']
        set_active(db,'codex')
        BothNative.release.set()
        await settled(workers)
        assert db.one('SELECT model FROM executions WHERE id=?',(a['lease_owner'],))['model']=='test-model'
        assert db.one('SELECT model FROM executions WHERE id=?',(b['lease_owner'],))['model']=='claude-sonnet-5-5'
        assert tasks.get(first['id'])['status']==tasks.get(second['id'])['status']=='completed'
    finally:
        BothNative.release.set()
        BothNative.release=None
        await workers.close()

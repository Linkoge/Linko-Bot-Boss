"""Minimal pipeline: finished work merges into main one at a time, then a deploy is requested."""
import json
from pathlib import Path

import pytest
from test_minimal_flow import system as system_fixture  # noqa: F401
from test_parallel import done, p  # noqa: F401
from test_parallel import repo as repo_fixture

from ai_manager import pipeline as pipeline_module
from ai_manager.assignment_workflow import update
from ai_manager.parallel import git
from ai_manager.pipeline import Pipeline, atomic
from ai_manager.tasks import Tasks

system, repo = system_fixture, repo_fixture


def finish_task(db, project, name, output='All checks passed.\nRESULT: PASSED', filename=None, text='changed\n'):
    tasks = Tasks(db)
    task = tasks.add(project['id'], name, execution_policy=p([filename or name]))
    claimed = tasks.claim(2)
    done(db, claimed, filename or name, text)
    with db.transaction() as conn:
        conn.execute("UPDATE executions SET output=?,delivery='delivered' WHERE id=?", (output, claimed['lease_owner']))
        update(db, conn, task['id'], 'complete', 'Worker completed', automatic=True)  # as Workers.finish does
    return task


async def tick(pipe):
    pipe.step()
    if pipe.job:
        await pipe.job


def head(project):
    return git(project['path'], 'rev-parse', 'HEAD')


@pytest.fixture
def pipe(repo, monkeypatch):
    db, registry, _, _, project = repo
    monkeypatch.setattr(pipeline_module, 'ROOT', Path(project['path']).resolve())
    pipeline = Pipeline(db, registry)
    pipeline.set(project['id'], True)
    return pipeline, db, project


@pytest.mark.asyncio
async def test_finished_work_merges_into_main_and_requests_one_deploy(pipe):
    pipeline, db, project = pipe
    before = head(project)
    finish_task(db, project, 'one.txt')
    await tick(pipeline)
    assert head(project) != before and (Path(project['path']) / 'one.txt').read_text() == 'changed\n'
    request = json.loads((pipeline.root / 'request.json').read_text())
    assert request['project_id'] == project['id'] and request['source'] == 'auto'
    assert pipeline.pending() and not pipeline.installed()
    assert 'merged' in json.dumps(db.all("SELECT value FROM settings WHERE key LIKE 'assignment_merge:%'"))
    # Nothing else merges while the deploy is pending, and a merged commit is never merged twice.
    finish_task(db, project, 'two.txt')
    await tick(pipeline)
    assert not (Path(project['path']) / 'two.txt').exists()


@pytest.mark.asyncio
async def test_merges_are_sequential_after_deploy_finishes(pipe):
    pipeline, db, project = pipe
    finish_task(db, project, 'one.txt')
    finish_task(db, project, 'two.txt')
    await tick(pipeline)
    (pipeline.root / 'request.json').unlink()
    await tick(pipeline)
    assert (Path(project['path']) / 'one.txt').exists() and (Path(project['path']) / 'two.txt').exists()
    assert git(project['path'], 'status', '--porcelain') == ''


@pytest.mark.asyncio
async def test_off_failed_checks_and_owner_holds_do_not_merge(pipe):
    pipeline, db, project = pipe
    before = head(project)
    task = finish_task(db, project, 'failed.txt', output='Still failing\nRESULT: FAILED - test_x')
    await tick(pipeline)
    assert head(project) == before and not pipeline.pending()
    assert db.one("SELECT 1 FROM events WHERE kind='pipeline.blocked'")
    held = finish_task(db, project, 'held.txt')
    with db.transaction() as conn:
        update(db, conn, held['id'], 'owner_wait', 'Owner will decide')
    await tick(pipeline)
    assert head(project) == before
    with db.transaction() as conn:
        update(db, conn, held['id'], 'complete', 'Released')
    pipeline.set(project['id'], False)
    await tick(pipeline)
    assert head(project) == before and task['id']


@pytest.mark.asyncio
async def test_conflict_aborts_cleanly_and_reports_without_deploy(pipe):
    pipeline, db, project = pipe
    root = Path(project['path'])
    finish_task(db, project, 'base.txt', filename='base.txt', text='from worker\n')
    (root / 'base.txt').write_text('changed on main\n')
    git(root, 'commit', '-am', 'main moved')
    moved = head(project)
    await tick(pipeline)
    assert head(project) == moved and git(root, 'status', '--porcelain') == '' and not pipeline.pending()
    assert db.one("SELECT 1 FROM events WHERE kind='pipeline.merge_failed'")
    await tick(pipeline)  # Same commit is not retried.
    assert len(db.all("SELECT 1 FROM events WHERE kind='pipeline.merge_failed'")) == 1


@pytest.mark.asyncio
async def test_dirty_checkout_blocks_merge_and_detached_head_returns_to_main(pipe):
    pipeline, db, project = pipe
    root = Path(project['path'])
    finish_task(db, project, 'one.txt')
    (root / 'base.txt').write_text('uncommitted\n')
    await tick(pipeline)
    assert not (root / 'one.txt').exists() and (root / 'base.txt').read_text() == 'uncommitted\n'
    git(root, 'checkout', '--', 'base.txt')
    git(root, 'checkout', '--detach')
    finish_task(db, project, 'two.txt')
    with db.transaction() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'assignment_merge:%'")
    await tick(pipeline)
    (pipeline.root / 'request.json').unlink()
    await tick(pipeline)
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'main' and (root / 'two.txt').exists()


def test_cancel_only_before_restart_and_result_reported_once(pipe):
    pipeline, db, project = pipe
    request = pipeline.request(project['id'], 'auto')
    assert pipeline.request(project['id'], 'auto')['id'] == request['id']
    atomic(pipeline.root / 'state.json', {'id': request['id'], 'phase': 'restarting'})
    with pytest.raises(ValueError, match='already started'):
        pipeline.cancel()
    atomic(pipeline.root / 'state.json', {'id': request['id'], 'phase': 'waiting_idle'})
    assert pipeline.cancel() and not pipeline.pending()
    atomic(pipeline.root / 'result.json', {'id': '0123456789abcdef', 'status': 'rolled_back', 'reason': 'health failed',
                                           'project_id': project['id'], 'conversation_id': None})
    pipeline.report()
    pipeline.report()
    assert len(db.all("SELECT 1 FROM events WHERE kind='deployment.result'")) == 1
    assert pipeline.summary().startswith('rolled_back: health failed')


@pytest.mark.asyncio
async def test_pending_deploy_stops_new_coding_until_it_finishes(pipe):
    from ai_manager.config import Settings
    from ai_manager.orchestration import Scheduler
    from ai_manager.workers import Workers
    pipeline, db, project = pipe
    registry = pipeline.registry
    from fake_codex import FakeServer

    from ai_manager.chat import Chat
    settings = Settings(data_dir=db.path.parent)
    chat = Chat(db, registry, FakeServer(settings))
    workers = Workers(db, registry, settings)
    launched = []
    workers.launch = launched.append
    scheduler = Scheduler(db, registry, settings, workers, chat)
    task = Tasks(db).add(project['id'], 'queued work', execution_policy=p(['x.txt']))
    pipeline.request(project['id'], 'owner')
    await scheduler.tick()
    assert not launched and Tasks(db).get(task['id'])['status'] == 'queued'
    (pipeline.root / 'request.json').unlink()
    await scheduler.tick()
    assert [t['id'] for t in launched] == [task['id']]


@pytest.mark.asyncio
async def test_owner_merge_honors_holds_pending_deploy_and_the_shared_fence(pipe):
    from ai_manager import fence
    pipeline, db, project = pipe
    root = Path(project['path'])
    held = finish_task(db, project, 'held.txt')
    with db.transaction() as conn:
        update(db, conn, held['id'], 'owner_wait', 'Owner will decide')
    with pytest.raises(ValueError, match='owner hold'):
        await pipeline.merge_assignment(held['id'])
    assert not (root / 'held.txt').exists()
    with db.transaction() as conn:
        update(db, conn, held['id'], 'complete', 'Released')
    # A deploy holding the fence blocks both merge paths, and step() leaves no failure mark behind.
    with fence.hold(db.path.parent, True):
        with pytest.raises(ValueError, match='live checkout'):
            await pipeline.merge_assignment(held['id'])
        await tick(pipeline)
        assert not (root / 'held.txt').exists()
        assert not db.all("SELECT 1 FROM events WHERE kind='pipeline.merge_failed'")
    # A pending deploy request blocks the owner merge too.
    pipeline.request(project['id'], 'owner')
    with pytest.raises(ValueError, match='deploy is pending'):
        await pipeline.merge_assignment(held['id'])
    (pipeline.root / 'request.json').unlink()
    assert (await pipeline.merge_assignment(held['id']))['changed'] and (root / 'held.txt').exists()


@pytest.mark.asyncio
async def test_request_written_after_the_pending_check_still_stops_the_merge(pipe):
    pipeline, db, project = pipe
    finish_task(db, project, 'late.txt')
    candidate = pipeline.candidate()
    pipeline.request(project['id'], 'owner')  # appears between candidate selection and the locked merge
    await pipeline.merge(candidate)
    assert not (Path(project['path']) / 'late.txt').exists()
    assert not db.all("SELECT 1 FROM events WHERE kind IN ('pipeline.merged','pipeline.merge_failed')")


def test_a_deploy_fence_stops_new_tasks_and_chat_until_released(pipe):
    from ai_manager import fence
    from ai_manager.chat import Chat
    pipeline, db, project = pipe
    tasks = Tasks(db)
    task = tasks.add(project['id'], 'queued work', execution_policy=p(['x.txt']))
    with fence.hold(db.path.parent, True):
        assert tasks.claim(2) is None and tasks.get(task['id'])['status'] == 'queued'
        with pytest.raises(ValueError, match='deploy is restarting'):
            with Chat.deploy_fence(type('C', (), {'db': db})()):
                pass
    assert tasks.claim(2)['id'] == task['id']

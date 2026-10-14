import ast
import json
import subprocess
from pathlib import Path

import pytest
from test_model_settings import CatalogServer

from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.model_settings import MODELS, current, save
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.verification import REVIEW_INSTRUCTIONS, plan, select
from ai_manager.workers import Workers

RISKY = ['ai_manager/db.py', 'ai_manager/workers.py', 'ai_manager/orchestration.py', 'ai_manager/chat.py',
         'ai_manager/web.py', 'ai_manager/tasks.py', 'ai_manager/recovery.py', 'ai_manager/deployment.py',
         'ai_manager/deployment_intent.py', 'ai_manager/assignment_workflow.py', 'ai_manager/claude_host.py',
         'scripts/prepare_deployment.py', 'deploy/linko-bot-manager.service.in', 'pyproject.toml', 'uv.lock',
         'migrations/001_init.sql', 'src/auth/login.ts', 'frontend/session.js', '.env', 'app.py']


@pytest.mark.smoke
def test_tier_floor_is_deterministic_and_risky_paths_never_downgrade():
    small = {
        ('ai_manager/static/style.css',): 'Make Send red',
        ('ai_manager/static/app.js', 'ai_manager/static/style.css', 'docs/UI_THEME.md'): 'Tidy composer spacing',
        ('docs/DEPLOYMENT_HELPER.md',): 'Fix a typo',  # Text never changes runtime behavior.
        ('tests/test_deployment.py',): 'Rename a test',
    }
    for paths, text in small.items():
        assert select(list(paths), text)['tier'] == 'small', paths
    normal = [[], ['docs'], ['ai_manager/registry.py'], ['ai_manager/static/app.js', 'ai_manager/static/audio.js'],
              ['a.md', 'b.md', 'c.md', 'd.md'], ['Makefile']]
    for paths in normal:
        assert select(paths, 'Change it', requested='small')['tier'] == 'normal', paths
    for path in RISKY:
        record = select([path], 'Small one-file change', requested='small')
        assert record['tier'] == 'risky' and 'requested small not allowed' in record['reasons'], path
        assert select(['ai_manager/static/style.css', path], 'x', requested='normal')['tier'] == 'risky'
    for text in ('Fix login expiry', 'Add a schema migration', 'Rotate the CSRF secret', 'Restore from backup'):
        assert select(['ai_manager/static/style.css'], text, requested='small')['tier'] == 'risky', text
    assert select(['x.css'], 'x', integration=True)['tier'] == 'risky'
    assert select(['x.css'], 'x', deployment=True)['tier'] == 'risky'
    assert select(['x.css'], 'x', requested='risky')['reasons'][-1] == 'manager raised tier'
    assert select(['x.css'], 'x', previous={'tier': 'normal'})['tier'] == 'normal'
    assert select(['ai_manager/db.py'], 'x') == select(['ai_manager/db.py'], 'x')
    with pytest.raises(ValueError, match='small, normal or risky'):
        select(['x.css'], 'x', requested='tiny')


@pytest.mark.smoke
def test_tier_plans_scale_checks_and_keep_reports_short():
    small, normal, risky = (plan(select(p, 'x')) for p in (['a.css'], ['a.py'], ['db.py']))
    assert not any('Full' in check for check in small['checks']) and any('Full suite' in s for s in small['skip'])
    assert all(any('smoke' in check.lower() for check in p['checks']) for p in (small, normal, risky))
    assert sum('Full backend suite exactly once' in check for check in normal['checks']) == 1
    assert 'Reviewer' in normal['skip'] and any('diff review' in s for s in risky['skip'])
    assert plan(select(['a.css'], 'x'), integration=True)['tier'] == 'risky'
    assert '12 short lines' in small['report'] and 'execution record' in small['report']


def test_smoke_suite_is_bounded_and_covers_core_paths():
    marked = {}
    for path in Path(__file__).parent.glob('test_*.py'):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    ast.unparse(d) == 'pytest.mark.smoke' for d in node.decorator_list):
                marked[path.stem + '::' + node.name] = node
    assert 20 <= len(marked) <= 30
    areas = {'test_web', 'test_minimal_flow', 'test_chat_queue', 'test_agent_controls', 'test_recovery',
             'test_parallel', 'test_deployment', 'test_deployment_intent', 'test_model_settings', 'test_verification'}
    assert areas <= {name.split('::')[0] for name in marked}
    assert 'test_audio::test_twenty_minute_full_upload_real_probe' not in marked  # Slow probes stay full-suite only.


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class TierServer(CatalogServer):
    edit = None
    reviews = []
    fail_review = False
    manager = False

    def __init__(self, settings, native=False):
        super().__init__(settings, native)
        self.native = native

    async def start(self):
        if not self.native and not self.manager and TierServer.fail_review:
            raise RuntimeError('review provider unavailable')

    async def thread(self, cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        self.cwd, self.instructions = Path(cwd), instructions
        return await super().thread(cwd, instructions, thread_id, model, dynamic_tools)

    async def request(self, method, params, timeout=30):
        if method == 'turn/start' and self.native and TierServer.edit:
            TierServer.edit(self.cwd)
        if method == 'turn/start' and not self.native:
            TierServer.reviews.append({'pair': {k: params[k] for k in ('model', 'effort')},
                                       'instructions': self.instructions, 'prompt': json.loads(params['input'][0]['text'])})
        return await super().request(method, params, timeout)


@pytest.fixture
def system(tmp_path):
    root = tmp_path / 'projects'
    (root / 'one').mkdir(parents=True)
    repo = root / 'one'
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'style.css').write_text('a{}')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'base')
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root,), secure_cookie=False)
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, (root,))
    registry.add(repo)
    server = TierServer(settings)
    server.manager = True
    chat = Chat(db, registry, server)
    workers = Workers(db, registry, settings, TierServer)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    TierServer.edit, TierServer.reviews, TierServer.fail_review = None, [], False
    return db, registry, server, chat, workers, scheduler


def automatic(db, server):
    config = current(db, server)
    config['bot']['mode'] = 'automatic'
    for item in config['bot']['policy'].values():
        item['allowed'], item['levels'] = True, {e: True for e in ('low', 'medium', 'high')}
    return save(db, server, config)


async def delegate(scheduler, chat, project, call, **arguments):
    params = {'tool': 'manager_action', 'callId': call, 'manager_execution_id': 'manager-' + call,
              'conversation_id': chat.conversation(project)['id'], 'mode': 'auto',
              'arguments': {'action': 'delegate', **arguments}}
    return await scheduler.manager_action(project, params)


@pytest.mark.asyncio
async def test_delegate_records_tier_and_risky_rejects_weak_automatic_pair(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    automatic(db, server)
    weak = {'model': MODELS['Luna'], 'effort': 'low'}
    with pytest.raises(ValueError, match='stronger permitted pair'):
        await delegate(scheduler, chat, project, 'a', objective='Fix lease', change_paths=['ai_manager/tasks.py'],
                       worker_route=weak, verification_tier='small')
    with pytest.raises(ValueError, match='stronger permitted pair'):
        await delegate(scheduler, chat, project, 'b', objective='Refactor', change_paths=['ai_manager/registry.py'],
                       worker_route={'model': MODELS['Sol'], 'effort': 'medium'}, verification_tier='risky')
    risky = await delegate(scheduler, chat, project, 'c', objective='Fix lease', change_paths=['ai_manager/tasks.py'],
                           worker_route={'model': MODELS['Sol'], 'effort': 'high'})
    assert risky['execution_policy']['verification']['tier'] == 'risky'
    small = await delegate(scheduler, chat, project, 'd', objective='Make Send red', change_paths=['style.css'],
                           worker_route=weak)
    assert small['execution_policy']['verification'] == {'version': 1, 'tier': 'small', 'requested': None,
                                                          'reasons': ['UI/text change of at most 3 files']}
    # Manual settings are never overridden by the tier.
    config = current(db, server)
    config['bot']['mode'] = 'manual'
    config['bot']['manual'] = weak
    save(db, server, config)
    manual = await delegate(scheduler, chat, project, 'e', objective='Fix auth', change_paths=['web.py'])
    assert manual['worker_route']['model'] == weak['model'] and manual['execution_policy']['verification']['tier'] == 'risky'


@pytest.mark.asyncio
async def test_continue_retiers_monotonically(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    task = await delegate(scheduler, chat, project, 'a', objective='Make Send red', change_paths=['style.css'])
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task['id'],))
    conversation = chat.conversation(project)['id']

    async def proceed(call, **arguments):
        await scheduler.manager_action(project, {
            'tool': 'manager_action', 'callId': call, 'manager_execution_id': 'manager-' + call,
            'conversation_id': conversation, 'mode': 'auto',
            'arguments': {'action': 'continue', 'task_id': task['id'], **arguments}})
        with db.transaction() as conn:
            conn.execute("UPDATE tasks SET lifecycle='completed' WHERE id=?", (task['id'],))
        return Tasks(db).get(task['id'])['execution_policy']['verification']

    assert (await proceed('b', objective='Also adjust padding'))['tier'] == 'small'
    assert (await proceed('c', objective='Also keep the session cookie'))['tier'] == 'risky'
    lowered = await proceed('d', objective='Only colors after all', verification_tier='small')
    assert lowered['tier'] == 'risky' and 'requested small not allowed' in lowered['reasons']


@pytest.mark.asyncio
async def test_batching_merges_only_unstarted_small_work_from_same_conversation(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    first = await delegate(scheduler, chat, project, 'a', objective='Make Send red', change_paths=['style.css'],
                           acceptance='Send is red')
    merged = await delegate(scheduler, chat, project, 'b', objective='Fix README typo', change_paths=['README.md'],
                            acceptance='Typo fixed', batch=True)
    assert merged['id'] == first['id'] and merged['batched']
    task = Tasks(db).get(first['id'])
    assert 'Make Send red' in task['description'] and 'Fix README typo' in task['description']
    assert task['acceptance'] == 'Send is red\n\nTypo fixed'
    assert task['execution_policy']['change_paths'] == ['style.css', 'README.md']
    assert task['execution_policy']['verification']['tier'] == 'small'
    # A replayed manager call returns the batched assignment instead of creating a duplicate.
    assert (await delegate(scheduler, chat, project, 'b', objective='Fix README typo', change_paths=['README.md'],
                           batch=True))['id'] == first['id']
    # Overflowing Small scope, non-Small requests and started work are never merged.
    overflow = await delegate(scheduler, chat, project, 'c', objective='Restyle', change_paths=['a.css', 'b.css'],
                              batch=True)
    backend = await delegate(scheduler, chat, project, 'd', objective='Registry tweak',
                             change_paths=['ai_manager/registry.py'], batch=True)
    assert len({first['id'], overflow['id'], backend['id']}) == 3
    started = Tasks(db).claim(4)
    later = await delegate(scheduler, chat, project, 'e', objective='Fix docs typo', change_paths=['docs/x.md'],
                           batch=True)
    assert started['id'] == first['id'] and later['id'] not in {first['id'], overflow['id'], backend['id']}
    assert db.one("SELECT count(*) AS n FROM events WHERE kind='task.batched'")['n'] == 1


def worker_prompt():
    return next(json.loads(t['input'][0]['text']) for t in reversed(CatalogServer.turns)
                if 'workspace_ownership' in t['input'][0]['text'])


@pytest.mark.asyncio
async def test_actual_risky_diff_escalates_small_tier_and_runs_one_cheap_read_only_review(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    automatic(db, server)
    created = await delegate(scheduler, chat, project, 'a', objective='Make Send red', change_paths=['style.css'],
                             worker_route={'model': MODELS['Sol'], 'effort': 'low'})

    def edit(cwd):
        (cwd / 'style.css').write_text('a{color:red}')
        (cwd / 'db.py').write_text('SCHEMA = 2\n')  # Undeclared risky change.
        git(cwd, 'add', '.')
        git(cwd, 'commit', '-m', 'work')

    TierServer.edit = edit
    task = Tasks(db).claim(2)
    assert task['id'] == created['id'] and task['workspace_base']
    await workers._run(task)
    execution = db.one('SELECT * FROM executions WHERE id=?', (task['lease_owner'],))
    assert execution['status'] == 'completed'
    prompt = worker_prompt()
    assert prompt['verification']['tier'] == 'small' and 'verification' not in prompt['execution_policy']
    record = json.loads(db.one("SELECT payload FROM events WHERE kind='task.verification'")['payload'])
    assert record['assigned'] == 'small' and record['actual'] == record['effective'] == 'risky'
    assert record['escalated'] and record['changed_files'] == ['db.py', 'style.css'] and record['undeclared'] == ['db.py']
    assert len(TierServer.reviews) == 1
    review = TierServer.reviews[0]
    assert review['pair'] == {'model': MODELS['Luna'], 'effort': 'low'}  # Cheapest permitted pair.
    assert review['instructions'] == REVIEW_INSTRUCTIONS and 'SCHEMA = 2' in review['prompt']['diff']
    assert record['review']['status'] == 'completed' and record['review']['findings'] == 'Saved streamed answer'
    notice = chat.verification_notice(execution)
    assert notice['escalated'] and notice['review']['status'] == 'completed' and notice['undeclared'] == ['db.py']


@pytest.mark.asyncio
async def test_small_diff_skips_review_and_review_failure_never_blocks_completion(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']

    def edit(cwd):
        (cwd / 'style.css').write_text('a{color:red}')
        git(cwd, 'commit', '-am', 'red')

    TierServer.edit = edit
    await delegate(scheduler, chat, project, 'a', objective='Make Send red', change_paths=['style.css'])
    task = Tasks(db).claim(2)
    await workers._run(task)
    record = json.loads(db.one("SELECT payload FROM events WHERE kind='task.verification'")['payload'])
    assert record['effective'] == 'small' and not record['escalated'] and 'review' not in record
    assert TierServer.reviews == []
    TierServer.fail_review = True

    def lease(cwd):
        (cwd / 'tasks.py').write_text('LEASE = 1\n')

    TierServer.edit = lease
    await delegate(scheduler, chat, project, 'b', objective='Tighten lease', change_paths=['tasks.py'])
    task = Tasks(db).claim(2)
    await workers._run(task)
    row = db.one("SELECT status,error FROM executions WHERE id=?", (task["lease_owner"],))
    assert row["status"] == "completed", row["error"]
    record = json.loads(db.one("SELECT payload FROM events WHERE kind='task.verification' ORDER BY seq DESC")['payload'])
    assert record['assigned'] == 'risky' and record['review'] == {'status': 'unavailable', 'reason': 'review provider unavailable'}


@pytest.mark.asyncio
async def test_pre_tier_assignments_keep_original_prompt_and_skip_post_checks(system):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    task = Tasks(db).add(project, 'Existing assignment', chat.conversation(project)['id'],
                         execution_policy={'permission': {}, 'parallel_safe': False, 'reason': '',
                                           'change_paths': ['style.css'], 'depends_on': []})
    claimed = Tasks(db).claim(2)
    await workers._run(claimed)
    prompt = worker_prompt()
    assert 'verification' not in prompt and prompt['execution_policy'] == Tasks(db).get(task['id'])['execution_policy']
    assert not db.one("SELECT 1 FROM events WHERE kind='task.verification'") and TierServer.reviews == []
    execution = db.one('SELECT * FROM executions WHERE task_id=?', (task['id'],))
    assert chat.verification_notice(execution) is None


@pytest.mark.asyncio
async def test_unexpected_post_check_error_never_changes_completed_outcome(system, monkeypatch):
    db, registry, server, chat, workers, scheduler = system
    project = registry.list()[0]['id']
    import ai_manager.workers as module

    def timeout(root, base):
        raise subprocess.TimeoutExpired(['git', 'diff'], 60)

    def crash(paths):
        raise ZeroDivisionError('classifier bug')

    for call, patch, expected in (('a', ('changes', timeout), 'actual_error'), ('b', ('floor', crash), 'error')):
        monkeypatch.setattr(module, *patch)
        if call == 'b':
            monkeypatch.setattr(module, 'changes', lambda root, base: (['style.css'], 'diff', False))
        await delegate(scheduler, chat, project, call, objective='Make Send red ' + call, change_paths=['style.css'])
        task = Tasks(db).claim(2)
        await workers._run(task)
        execution = db.one('SELECT * FROM executions WHERE id=?', (task['lease_owner'],))
        assert execution['status'] == 'completed' and Tasks(db).get(task['id'])['status'] == 'completed'
        record = json.loads(db.one("SELECT payload FROM events WHERE kind='task.verification' AND execution_id=?",
                                   (execution['id'],))['payload'])
        assert expected in record
    assert 'timed out' in json.loads(db.one("SELECT payload FROM events WHERE kind='task.verification' ORDER BY seq")['payload'])['actual_error']
    assert chat.verification_notice(execution) == {'tier': 'small', 'post_check': 'failed: classifier bug'}

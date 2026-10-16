"""Permanent bounded task/agent listing contracts; fixtures never touch live data."""
import json

import pytest
from test_execution_ui import records  # noqa: F401
from test_web import client, sign_in  # noqa: F401

from ai_manager.execution_ui import execution_summary
from ai_manager.history import maintain
from ai_manager.orchestration import Scheduler


def seed(db, project, prefix, count=26):
    with db.transaction() as conn:
        for i in range(count):
            task, execution = f'{prefix}-task-{i}', f'{prefix}-agent-{i}'
            conn.execute('''INSERT INTO tasks(id,project_id,description,acceptance,state,created_at,updated_at,
                gate_history,workspace,"merge") VALUES(?,?,?,?,'done',1,?,'[{"output":"long gate log"}]',
                '/kept/workspace','{"state":"merged","main":"receipt"}')''',
                         (task, project, f'Derived task title {prefix} {i}\n' + 'Full objective ' * 100,
                          'Full acceptance criteria', 100 + i // 2))
            conn.execute('''INSERT INTO executions(id,project_id,task_id,agent_id,status,started_at,finished_at,
                output,input,thread_id,turn_id,commit_ref) VALUES(?,?,?,?,'completed',1,?,?,?,?,'turn','commit')''',
                         (execution, project, task, f'worker-{i}', 100 + i // 2,
                          f'Final result /reports/{task}.md\n' + 'Full report ' * 2000,
                          'Historical prompt', f'thread-{i}'))
        # All work is older than the terminal history. Retry waits are existing
        # queued/running states with timers, never a new queue/archive state.
        for i, (state, held, retry) in enumerate((('queued', 0, None), ('queued', 1, None),
                                                ('running', 0, None), ('queued', 0, 9999999999))):
            task, execution = f'{prefix}-active-{i}', f'{prefix}-waiting-{i}'
            conn.execute('''INSERT INTO tasks(id,project_id,description,state,held,retry_at,created_at,updated_at,
                lease_owner) VALUES(?,?,?,?,?,?,0,0,?)''',
                         (task, project, f'Old active {i}', state, held, retry, execution if state == 'running' else None))
            conn.execute('''INSERT INTO executions(id,project_id,task_id,agent_id,status,started_at,retry_at)
                VALUES(?,?,?,'worker','queued',0,?)''', (execution, project, task, retry))


def terminal_tasks(page):
    return [row['id'] for row in page['tasks'] if row['status'] in ('done', 'failed')]


def terminal_agents(page):
    return [row['id'] for row in page['executions'] if row['finished_at'] is not None]


def test_sql_window_all_active_tied_keys_older_refresh_and_reopened_boundary(records):  # noqa: F811
    db, tasks, (one, two) = records
    seed(db, one, 'one')
    # Finish the first fixture's running task to allow a second project's old work.
    db.run("UPDATE tasks SET state='queued' WHERE state='running'")
    seed(db, two, 'two')
    with db.transaction() as conn:
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at) VALUES('global-wait','manager','queued',0)")
        conn.execute("INSERT INTO executions(id,agent_id,status,started_at,finished_at) VALUES('global-finish','manager','completed',0,999)")
    for project, expected_active in ((one, 4), (two, 4), (None, 8)):
        page = tasks.page(project)
        agents = execution_summary(db, project)
        assert len(page['tasks']) == expected_active + 10
        assert len(agents['executions']) == expected_active + 10 + int(project is None)
        assert len(terminal_tasks(page)) == len(terminal_agents(agents)) == 10
        if project is None:
            assert {'global-wait', 'global-finish'} <= {r['id'] for r in agents['executions']}
        if project:
            assert all(row['project_id'] == project for row in page['tasks'] + agents['executions'])
        second = tasks.page(project, through=page['cursor'], older=True)
        second_agents = execution_summary(db, project, through=agents['execution_cursor'], older=True)
        assert len(terminal_tasks(second)) == len(terminal_agents(second_agents)) == 20
        assert terminal_tasks(second)[:10] == terminal_tasks(page)
        assert terminal_agents(second_agents)[:10] == terminal_agents(agents)
        assert tasks.page(project, through=second['cursor']) == second
        assert execution_summary(db, project, through=second_agents['execution_cursor']) == second_agents
        last = tasks.page(project, through=second['cursor'], older=True)
        last_agents = execution_summary(db, project, through=second_agents['execution_cursor'], older=True)
        assert len(set(terminal_tasks(last))) == min(30, 26 if project else 52)
        assert len(set(terminal_agents(last_agents))) == min(30, 26 if project else 52)
        assert last['has_more'] == (project is None)
        assert last_agents['has_older_executions'] == (project is None)
    agent_page = execution_summary(db, one)
    expanded_agents = execution_summary(db, one, through=agent_page['execution_cursor'], older=True)
    db.run("UPDATE executions SET status='completed',finished_at=1000 WHERE id='one-waiting-0'")
    refreshed_agents = execution_summary(db, one, through=expanded_agents['execution_cursor'])
    assert set(terminal_agents(refreshed_agents)) == set(terminal_agents(expanded_agents)) | {'one-waiting-0'}
    assert len(terminal_agents(execution_summary(db, one))) == 10
    # New finishes are included on refresh without displacing explicitly loaded history.
    page = tasks.page(one)
    expanded = tasks.page(one, through=page['cursor'], older=True)
    boundary = terminal_tasks(expanded)[-1]
    db.run("UPDATE tasks SET state='queued',updated_at=999 WHERE id=?", (boundary,))
    db.run("UPDATE tasks SET state='done',updated_at=1000 WHERE id='one-active-0'")
    refreshed = tasks.page(one, through=expanded['cursor'])
    assert set(terminal_tasks(refreshed)) == (set(terminal_tasks(expanded)) - {boundary}) | {'one-active-0'}
    more = tasks.page(one, through=refreshed['cursor'], older=True)
    assert len(terminal_tasks(more)) == 26
    assert not more['has_more']
    assert len(terminal_tasks(tasks.page(one))) == 10  # Reload/default is bounded again.
    with pytest.raises(ValueError, match='cursor'):
        tasks.page(two, through=expanded['cursor'])
    for bad in ('no-json', '[]', '{}', '["tasks",null,"NaN",1]'):
        with pytest.raises(ValueError, match='cursor'):
            tasks.page(through=bad)


@pytest.mark.asyncio
async def test_manager_default_five_compact_sql_filters_offset_and_full_read(records):  # noqa: F811
    db, tasks, (one, two) = records
    seed(db, one, 'one')
    other = tasks.add(two, 'Other project active')
    # manager_action read paths only need these two dependencies.
    scheduler = object.__new__(Scheduler)
    scheduler.db, scheduler.tasks = db, tasks
    from ai_manager.registry import Registry
    scheduler.registry = Registry(db, ())

    async def read(selected=one, **args):
        return await scheduler.manager_action(selected, {'tool': 'manager_action', 'arguments': args})

    listed = await read(action='list_tasks')
    rows = listed['assignments']
    assert len(rows) == 9 and listed['next_offset'] == 5
    assert len([r for r in rows if r['status'] == 'done']) == 5
    assert {r['id'] for r in rows if r['status'] != 'done'} == {f'one-active-{i}' for i in range(4)}
    compact = {'id', 'project_id', 'description', 'description_truncated', 'title', 'state', 'status',
               'blocker', 'lease_owner', 'revision', 'rounds', 'repair_limit', 'created_at', 'updated_at',
               'retry_at', 'result_summary'}
    assert all(set(row) == compact for row in rows)
    assert all(len(row['description']) <= 300 for row in rows)
    collected = [r['id'] for r in rows if r['status'] == 'done']
    while listed['next_offset'] is not None:
        listed = await read(action='list_tasks', offset=listed['next_offset'])
        assert all(r['status'] == 'done' for r in listed['assignments'])
        collected.extend(r['id'] for r in listed['assignments'])
    assert len(collected) == len(set(collected)) == 26
    db.run("UPDATE tasks SET description=description || ' searchable-tail-only' WHERE id='one-task-0'")
    tail = await read(action='list_tasks', query='searchable-tail-only')
    assert [r['id'] for r in tail['assignments']] == ['one-task-0']
    match = await read(action='list_tasks', query='one 12', status='done')
    assert [r['id'] for r in match['assignments']] == ['one-task-12']
    assert (await read(action='list_tasks', status='held'))['assignments'][0]['id'] == 'one-active-1'
    assert not (await read(action='list_tasks', status='failed'))['assignments']
    global_rows = (await read(None, action='list_tasks'))['assignments']
    assert len(global_rows) == 10 and other['id'] in {r['id'] for r in global_rows}
    with pytest.raises(ValueError, match='another project'):
        await read(action='list_tasks', project_id=two)
    with pytest.raises(ValueError, match='status'):
        await read(action='list_tasks', status='archive')
    maintain(db)
    full = await read(action='read_task', task_id='one-task-0')
    assert len(full['description']) > 1000 and full['acceptance'] == 'Full acceptance criteria'
    assert full['workspace'] == '/kept/workspace' and full['merge']['main'] == 'receipt'
    assert full['result']['output'].startswith('Final result /reports/one-task-0.md')
    assert full['result']['next_offset'] == 12000
    assert db.one("SELECT thread_id FROM executions WHERE id='one-agent-0'")['thread_id'] == 'thread-0'


def test_portfolio_api_scopes_and_explicit_sql_paging(client):  # noqa: F811
    browser, app, settings = client
    sign_in(browser, app, settings)
    one = app.state.registry.add(settings.project_roots[0] / 'one')['id']
    two = app.state.registry.add(settings.project_roots[0] / 'two')['id']
    app.state.db.run("UPDATE projects SET status='paused'")
    seed(app.state.db, one, 'one')
    app.state.db.run("UPDATE tasks SET state='queued',held=1 WHERE state IN ('queued','running')")
    seed(app.state.db, two, 'two')
    app.state.db.run("UPDATE tasks SET state='queued',held=1 WHERE state='running'")
    for scope in (one, two, None):
        params = {'project_id': scope} if scope else {}
        initial = browser.get('/api/portfolio', params=params).json()
        assert len(terminal_tasks(initial)) == len(terminal_agents(initial)) == 10
        expanded = browser.get('/api/portfolio', params=params | {'older': 'tasks',
                               'task_through': initial['task_cursor']}).json()
        assert len(terminal_tasks(expanded)) == 20 and len(terminal_agents(expanded)) == 10
        refreshed = browser.get('/api/portfolio', params=params | {'task_through': expanded['task_cursor']}).json()
        assert terminal_tasks(refreshed) == terminal_tasks(expanded)
        assert len(terminal_tasks(browser.get('/api/portfolio', params=params).json())) == 10
    assert browser.get('/api/portfolio', params={'project_id': two, 'task_through':
                       json.dumps(['tasks', one, 100, 1])}).status_code == 409

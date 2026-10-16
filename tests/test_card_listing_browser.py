"""Real browser card counts, paging, controls and compressed history retrieval."""
import json

from test_card_listing import seed
from test_model_picker_browser import browser_context, page, server  # noqa: F401

from ai_manager.db import Database
from ai_manager.history import maintain
from ai_manager.registry import Registry


def ids(browser_page, kind):
    attribute = 'task' if kind == 'task' else 'execution'
    return browser_page.locator(f'#{kind}-list [data-{attribute}-id]').evaluate_all(
        f'nodes=>nodes.map(n=>n.dataset.{attribute}Id)')


def test_cards_default_ten_paging_refresh_scope_reload_controls_and_history(page, browser_context):  # noqa: F811
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    projects = []
    for prefix in ('one', 'two'):
        path = root / 'projects' / prefix
        path.mkdir()
        project = Registry(db, (root / 'projects',)).add(path)['id']
        db.run("UPDATE projects SET status='paused' WHERE id=?", (project,))
        # The disposable scheduler cannot dispatch these fixtures.
        db.run("UPDATE tasks SET state='queued' WHERE state='running'")
        seed(db, project, prefix)
        projects.append(project)
    maintain(db)
    one, two = projects
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    page.evaluate('()=>selectProject(null)')
    assert len(ids(page, 'task')) == len(ids(page, 'agent')) == 18  # 8 active + 10 terminal across scopes.
    assert len([id for id in ids(page, 'task') if '-task-' in id]) == 10
    assert len([id for id in ids(page, 'agent') if '-agent-' in id]) == 10
    page.locator('[data-view=tasks]').click()
    delayed = []
    def delay_older(route):
        if 'older=' in route.request.url:
            delayed.append(route)
        else:
            route.continue_()
    page.route('**/api/portfolio?*', delay_older)
    with page.expect_request(lambda request: 'older=tasks' in request.url):
        page.locator('#tasks-older').click()
    assert page.evaluate('()=>refresh(false)') is False  # A poll cannot discard an older-page request.
    assert len(ids(page, 'task')) == 18
    route = delayed.pop()
    route.fulfill(response=route.fetch())
    page.unroute('**/api/portfolio?*', delay_older)
    page.wait_for_function('()=>state.portfolio.tasks.length===28')
    first_twenty = ids(page, 'task')
    assert len(first_twenty) == len(set(first_twenty)) == 28
    page.evaluate('()=>refresh(false)')
    assert ids(page, 'task') == first_twenty
    page.locator('[data-view=agents]').click()
    page.locator('#agents-older').click()
    page.wait_for_function('()=>state.portfolio.executions.length===28')
    assert len(set(ids(page, 'agent'))) == 28
    page.evaluate('()=>refresh(false)')
    assert len(ids(page, 'agent')) == 28
    # A late older response from the previous scope must never replace the new scope.
    page.route('**/api/portfolio?*', delay_older)
    with page.expect_request(lambda request: 'older=executions' in request.url):
        page.evaluate("()=>{window.oldListing=refresh(false,'executions');}")
    page.evaluate('(two)=>selectProject(two)', two)
    route = delayed.pop()
    route.fulfill(response=route.fetch())
    page.unroute('**/api/portfolio?*', delay_older)
    page.evaluate('()=>window.oldListing')
    assert len(ids(page, 'task')) == len(ids(page, 'agent')) == 14
    # Scope changes reset both independent windows, including rapid switches.
    page.evaluate('([one,two])=>Promise.all([selectProject(one),selectProject(two)])', projects)
    assert page.evaluate('state.project') == two
    assert len(ids(page, 'task')) == len(ids(page, 'agent')) == 14
    assert all(id.startswith('two-') for id in ids(page, 'task') + ids(page, 'agent'))
    page.evaluate('(one)=>selectProject(one)', one)
    page.locator('[data-view=tasks]').click()
    headline = page.locator('[data-task-id=one-task-25] h3')
    assert headline.inner_text() == 'Derived task title one 25'
    assert headline.inner_text() != 'one'
    assert {f'one-active-{i}' for i in range(4)} <= set(ids(page, 'task'))
    assert {f'one-waiting-{i}' for i in range(4)} <= set(page.evaluate('state.portfolio.executions.map(e=>e.id)'))
    # Existing controls keep revision handling and refresh bounded cards.
    held = page.locator('[data-task-id=one-active-1]')
    held.get_by_role('button', name='Release', exact=True).click()
    held.get_by_role('button', name='Hold', exact=True).wait_for()
    assert len(ids(page, 'task')) == 14
    held.get_by_role('button', name='Hold', exact=True).click()
    held.get_by_role('button', name='Release', exact=True).wait_for()
    held.get_by_role('button', name='Cancel', exact=True).click()
    held.get_by_role('button', name='Continue', exact=True).wait_for()
    assert len(ids(page, 'task')) == 13  # 3 active + exactly 10 terminal.
    held.get_by_role('button', name='Continue', exact=True).click()
    held.get_by_role('button', name='Hold', exact=True).wait_for()
    assert len(ids(page, 'task')) == 14
    # Finish a previously old task: ordinary refresh must still have ten terminal cards.
    db.run("UPDATE tasks SET state='done',updated_at=1000 WHERE id='one-active-0'")
    page.evaluate('()=>refresh(false)')
    assert len(ids(page, 'task')) == 13
    assert len([id for id in ids(page, 'task') if id in {f'one-task-{i}' for i in range(26)} | {'one-active-0'}]) == 10
    # Load all older history in each list, without losing pages on periodic refresh.
    page.locator('#tasks-older').click()
    page.wait_for_function('()=>state.portfolio.tasks.length===23')
    page.locator('#tasks-older').click()
    page.wait_for_function('()=>state.portfolio.tasks.length===30')
    assert page.locator('#tasks-older').is_hidden()
    assert len(set(ids(page, 'task'))) == 30
    page.evaluate('()=>refresh(false)')
    assert len(ids(page, 'task')) == 30
    assert page.locator('#tasks-older').is_hidden()
    page.locator('[data-task-id=one-task-0]').get_by_role('button', name='History', exact=True).click()
    page.locator('#history-reader').wait_for(state='visible')
    history = json.loads(page.locator('#reader-content pre').first.inner_text())
    assert history['task']['workspace'] == '/kept/workspace'
    assert len(history['task']['description']) > 1000
    page.locator('#reader-close').click()
    page.locator('[data-view=agents]').click()
    for expected in (24, 30):
        page.locator('#agents-older').click()
        page.wait_for_function('(n)=>state.portfolio.executions.length===n', arg=expected)
    assert page.locator('#agents-older').is_hidden()
    assert len(set(ids(page, 'agent'))) == 30
    page.locator('[data-execution-id=one-agent-0]').get_by_role('button', name='History and full output').click()
    page.locator('#history-reader').wait_for(state='visible')
    page.get_by_role('button', name='Read full output', exact=True).click()
    page.wait_for_function("()=>document.querySelectorAll('#reader-content pre').length===2")
    result = json.loads(page.locator('#reader-content pre').nth(1).inner_text())
    assert result['output'].startswith('Final result /reports/one-task-0.md')
    page.locator('#reader-close').click()
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    assert len(ids(page, 'task')) == 17 and len(ids(page, 'agent')) == 18
    page.evaluate('(one)=>selectProject(one)', one)
    assert len(ids(page, 'task')) == 13 and len(ids(page, 'agent')) == 14
    page.evaluate('()=>selectProject(null)')
    assert len(ids(page, 'task')) == 17 and len(ids(page, 'agent')) == 18

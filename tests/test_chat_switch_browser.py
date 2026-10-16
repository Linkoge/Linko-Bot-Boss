"""Project-switch timing and cache regressions on a disposable Chromium/server."""
# ruff: noqa: F811
import time
import uuid

from test_model_picker_browser import browser_context, page, server  # noqa: F401

from ai_manager.chat import Chat
from ai_manager.db import Database
from ai_manager.registry import Registry


def make_project(browser_context, count=150):
    _, _, root = browser_context
    path = root / 'projects' / uuid.uuid4().hex
    path.mkdir()
    db = Database(root / 'data/manager.sqlite3')
    registry = Registry(db, (root / 'projects',))
    project = registry.add(path)['id']
    chat = Chat(db, registry, None)
    conversation = chat.conversation(project)['id']
    with db.transaction() as conn:
        for i in range(count):
            conn.execute("INSERT INTO messages VALUES(?,?,'assistant',?,'completed',NULL,?)",
                         (uuid.uuid4().hex, conversation, f'Message {i}\n\n' + ('Paragraph **with formatting** and `code`.\n\n' * 100), time.time()))
    return project, chat


def measure_switch(page, project):
    return page.evaluate("""async project=>{
      const calls=[],original=api,render=updateMessageContent;
      let renderMs=0;
      api=async(...args)=>{const start=performance.now();const result=await original(...args);calls.push({path:args[0],ms:performance.now()-start});return result;};
      updateMessageContent=(...args)=>{const start=performance.now();try{return render(...args);}finally{renderMs+=performance.now()-start;}};
      const start=performance.now();
      try{const switching=selectProject(project),immediateMs=performance.now()-start;if(!state.history)await switching;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const paintMs=performance.now()-start;await switching;return {totalMs:performance.now()-start,immediateMs,paintMs,renderMs,calls};}
      finally{api=original;updateMessageContent=render;}
    }""", project)


def test_switch_timing(page, browser_context):
    project, chat = make_project(browser_context)
    page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': 6})
    timings=[]
    for _ in range(5):
        start=time.perf_counter()
        chat.history(project)
        timings.append((time.perf_counter()-start)*1000)
    page.evaluate('()=>refresh(false)')
    first=measure_switch(page, project)
    measure_switch(page, None)
    repeat=measure_switch(page, project)
    assert repeat['renderMs'] < first['renderMs']
    assert any('since=' in call['path'] for call in repeat['calls'] if '/api/history?' in call['path'])
    print({'server_history_ms':timings, 'first':first, 'return':repeat})


def test_return_reattaches_nodes_and_immediately_syncs_new_deleted_and_older_messages(page, browser_context):
    project, chat = make_project(browser_context, count=65)
    page.evaluate('()=>refresh(false)')
    page.evaluate('project=>selectProject(project)', project)
    page.wait_for_function('()=>!initialChatPosition')
    old = page.evaluate("()=>{window.retainedNode=$('messages').firstElementChild;$('messages').scrollTop=500;return state.history.messages[0].id;}")
    page.evaluate('()=>selectProject(null)')
    conversation = chat.conversation(project)['id']
    chat.delete_message(project, conversation, old, True)
    chat.db.run("INSERT INTO messages VALUES('background-new',?,'assistant','Arrived while away','completed',NULL,?)",
                (conversation, time.time()))
    held = []
    page.route('**/api/history?project_id=' + project + '*', lambda route: held.append(route))
    immediate = page.evaluate("""project=>{const start=performance.now();void selectProject(project);
      return {ms:performance.now()-start,same:$('messages').firstElementChild===retainedNode,count:state.history.messages.length};}""", project)
    assert immediate['same'] and immediate['count'] == 30
    assert page.locator('[data-message="background-new"]').count() == 0
    page.wait_for_function('()=>!!usageData')
    deadline = time.monotonic() + 5
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert held and 'since=' in held[0].request.url
    response = held[0].fetch()
    held[0].fulfill(response=response)
    page.unroute('**/api/history?project_id=' + project + '*')
    page.locator('[data-message="background-new"]').wait_for()
    assert page.locator('[data-message="' + old + '"]').count() == 0
    assert page.locator('#messages').evaluate('n=>n.scrollTop') == 500
    # Scrolling to the top retrieves another bounded page and keeps loaded rows.
    page.locator('#messages').evaluate('n=>n.scrollTop=0')
    page.wait_for_function('()=>state.history.messages.length===60')
    assert page.locator('#messages [data-message]').count() == 60
    print({'cached_restore_ms': immediate['ms']})


def test_reload_preview_is_visible_before_sync_and_reconciles_conversation_reset(page, browser_context):
    project, chat = make_project(browser_context, count=4)
    page.evaluate('()=>refresh(false)')
    page.evaluate('project=>selectProject(project)', project)
    chat.db.run('UPDATE projects SET last_used_at=? WHERE id=?', (time.time()+100, project))
    previous = chat.history(project)
    chat.reset(project, previous['conversation']['id'], previous['messages'][-1]['id'], True, archive=True)
    held = []
    pattern = '**/api/history?project_id=' + project + '*'
    page.route(pattern, lambda route: held.append(route))
    page.reload()
    page.wait_for_function('project=>state.project===project&&state.history?.messages.length===4', arg=project)
    assert page.locator('#messages [data-message]').count() == 4
    assert held and 'since=' in held[0].request.url
    held[0].fulfill(response=held[0].fetch())
    page.unroute(pattern)
    page.wait_for_function('()=>state.history?.messages.length===0&&state.ready')
    assert page.locator('#messages [data-message]').count() == 0
    assert page.evaluate('state.history.conversation.id') != previous['conversation']['id']


def test_late_project_response_cannot_overwrite_current_chat(page, browser_context):
    one, _ = make_project(browser_context, count=2)
    two, _ = make_project(browser_context, count=3)
    page.evaluate('()=>refresh(false)')
    held = []
    pattern = '**/api/history?project_id=' + one + '*'
    page.route(pattern, lambda route: held.append(route))
    page.evaluate('project=>{void selectProject(project);}', one)
    page.wait_for_timeout(100)
    assert held
    page.evaluate('project=>selectProject(project)', two)
    held[0].fulfill(response=held[0].fetch())
    page.unroute(pattern)
    page.wait_for_timeout(100)
    assert page.evaluate('state.project') == two
    assert page.evaluate('state.history.messages.length') == 3

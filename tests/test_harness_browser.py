"""Real Chromium: the ☼ harness picker left of the chat menu, keyboard use, persistence, task cards, mobile layout."""
# ruff: noqa: F811 -- pytest fixtures imported by name
import json

from browser_sync import refreshed
from test_model_picker_browser import browser_context, page, server  # noqa: F401

from ai_manager.core import Tasks
from ai_manager.db import Database
from ai_manager.registry import Registry


def harness_folder(root, name, manifest, prompt='Prompt.\n'):
    folder = root / 'data' / 'harnesses' / name
    folder.mkdir(parents=True)
    (folder / 'harness.json').write_text(json.dumps(manifest))
    (folder / 'prompt.md').write_text(prompt)


def header_layout(page):
    return page.evaluate("""()=>{const box=id=>document.getElementById(id).getBoundingClientRect();
      const h=box('harness-button'),c=box('chat-menu-button'),u=box('usage-button'),tabs=box('chat-menu-button').top;
      return {gap:c.left-h.right,sameRow:Math.abs(h.top-c.top)<1,width:h.width,height:h.height,
              usageVisible:u.right<=innerWidth+0.5&&u.width>0,overflow:document.documentElement.scrollWidth-innerWidth,
              order:[...document.querySelector('.workspace-row').children].filter(e=>e.tagName==='BUTTON').map(e=>e.id)}}""")


def test_harness_picker_keyboard_persistence_task_card_and_mobile(page, browser_context):
    _, _, root = browser_context
    db = Database(root / 'data/manager.sqlite3')
    path = root / 'projects' / 'harnessed'
    path.mkdir()
    project = Registry(db, (root / 'projects',)).add(path)['id']
    db.run("UPDATE projects SET status='paused' WHERE id=?", (project,))  # the disposable queue must not dispatch
    harness_folder(root, 'docs-writer', {'id': 'docs-writer', 'name': 'Docs writer', 'role': 'worker',
                                         'description': 'Writes documents.', 'prompt': 'prompt.md'})
    harness_folder(root, 'sneaky', {'id': 'sneaky', 'name': 'Sneaky', 'role': 'worker', 'description': 'x',
                                    'prompt': 'prompt.md', 'deploy': True})
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    page.evaluate('(id)=>selectProject(id)', project)
    page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=project)

    for width, height in ((1280, 800), (390, 845)):
        page.set_viewport_size({'width': width, 'height': height})
        layout = header_layout(page)
        assert layout['order'].index('harness-button') + 1 == layout['order'].index('chat-menu-button')
        assert 0 <= layout['gap'] <= 8 and layout['sameRow'] and layout['width'] == layout['height'] == 44
        assert layout['usageVisible'] and layout['overflow'] <= 0, layout

    # Keyboard: ArrowDown opens it on the first picker, like the chat menu; Escape closes it back to the button.
    page.locator('#harness-button').focus()
    page.keyboard.press('ArrowDown')
    page.wait_for_function("()=>!document.getElementById('harness-menu').hidden&&harnessMenu.data&&"
                           "document.getElementById('harness-status').textContent===''")
    assert page.evaluate('document.activeElement.id') == 'harness-manager'
    assert page.locator('#harness-button').get_attribute('aria-expanded') == 'true'
    assert page.locator('#harness-manager').input_value() == 'linko'
    assert page.locator('#harness-worker').input_value() == 'coding'
    assert page.locator('#harness-autonomy').input_value() == 'finish_request'
    assert page.locator('#harness-autonomy option[value=rounds]').is_disabled()
    assert 'not yet available' in page.locator('#harness-autonomy option[value=rounds]').inner_text()
    assert 'full manager' in page.locator('#harness-manager-description').inner_text() or \
        'by the book' in page.locator('#harness-manager-description').inner_text()
    invalid = page.locator('#harness-invalid').inner_text()
    assert 'sneaky' in invalid and 'cannot set deploy' in invalid
    assert page.locator('#harness-worker option').all_inner_texts() == ['Coding', 'Zero worker', 'Office', 'Humanizer', 'Visual', 'Docs writer']
    menu = page.locator('#harness-menu').bounding_box()
    assert menu['x'] >= 0 and menu['x'] + menu['width'] <= 390.5
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.id') == 'harness-autonomy'
    page.keyboard.press('Shift+Tab')
    page.keyboard.press('Shift+Tab')  # wraps inside the menu, to its last control: Autopilot's "Set up" link
    assert page.evaluate('document.activeElement.id') == 'autopilot-setup-link'  # the suite is never set up
    page.keyboard.press('Shift+Tab')  # the disabled switch is skipped
    assert page.evaluate('document.activeElement.id') == 'harness-worker'

    page.locator('#harness-manager').select_option('zero')
    page.wait_for_function("()=>document.getElementById('harness-status').textContent.includes('next manager turn')")
    assert page.locator('#harness-autonomy').input_value() == 'ask'  # Zero's own default
    page.locator('#harness-worker').select_option('docs-writer')
    page.wait_for_function("()=>document.getElementById('harness-status').textContent==='Saved.'")
    page.keyboard.press('Escape')
    assert page.locator('#harness-menu').is_hidden()
    assert page.evaluate('document.activeElement.id') == 'harness-button'
    page.evaluate('()=>loadHistory(state.selection)')  # explicit, so a slow poll under load cannot time this out
    page.wait_for_function("()=>state.history.messages.some(m=>m.content.startsWith('Manager harness: Zero'))")

    # The task records its harness at creation and the card shows it.
    task = Tasks(db).add(project, 'Write the user guide')
    assert task['harness'] == {'id': 'docs-writer', 'name': 'Docs writer'}
    page.locator('[data-view=tasks]').click()
    refreshed(page, 'refresh(false)')  # renders the new task now, not at the next poll
    page.wait_for_function(f"()=>document.querySelector('[data-task-id=\"{task['id']}\"] .task-harness')")
    assert page.locator(f'[data-task-id="{task["id"]}"] .task-harness').inner_text() == 'Harness: Docs writer'

    page.reload()  # shown = saved after a reload
    page.wait_for_function('()=>state.ready&&!!state.history')
    page.evaluate('(id)=>selectProject(id)', project)
    page.wait_for_function('(id)=>state.project===id&&!!state.history', arg=project)
    page.locator('#harness-button').click()
    page.wait_for_function("()=>harnessMenu.data&&harnessMenu.project===state.project")
    assert page.locator('#harness-manager').input_value() == 'zero'
    assert page.locator('#harness-worker').input_value() == 'docs-writer'
    page.mouse.click(5, 800)  # outside closes
    assert page.locator('#harness-menu').is_hidden()
    page.locator('#harness-button').click()
    page.locator('#chat-menu-button').click()  # the chat menu replaces it
    assert page.locator('#harness-menu').is_hidden() and page.locator('#chat-menu').is_visible()


def test_environment_picker_sets_another_instance_and_shows_unreachable(page, browser_context, tmp_path):
    from ai_manager import harness
    _, _, root = browser_context
    user_db = Database(tmp_path / 'writer' / 'manager.sqlite3')
    (tmp_path / 'kaxa-project').mkdir()
    user_project = Registry(user_db, (tmp_path,)).add(tmp_path / 'kaxa-project')['id']  # kaxa's one fixed project
    manifest = root / 'data' / 'deploy' / 'installed.json'
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps({'instances': [
        {'name': 'kaxa', 'data': str(tmp_path / 'writer'), 'url': 'http://127.0.0.1:1', 'service': 'k.service'},
        {'name': 'Offline', 'data': str(tmp_path / 'missing'), 'url': 'http://127.0.0.1:2', 'service': 'o.service'}]}))
    main_scope = "()=>api(`/api/harnesses?${harnessQuery(state.project)}`)"
    main_before = page.evaluate(main_scope)['manager']['id']
    try:
        page.locator('#harness-button').click()
        page.wait_for_function("()=>harnessMenu.data&&!document.getElementById('harness-environment-group').hidden")
        assert page.locator('#harness-environment option').all_inner_texts() == ['Main', 'kaxa', 'Offline']
        assert page.locator('#harness-environment').input_value() == ''  # Main is the default
        assert page.locator('#harness-model-group').count() == 0  # models are chosen only in the model picker
        main_labels = page.locator('#harness-menu label:visible').all_inner_texts()
        assert main_labels[-1] == 'Autopilot'  # this machine's own switch: only while Main is the environment
        main_labels.remove('Autopilot')

        page.locator('#harness-environment').select_option('kaxa')
        page.wait_for_function("()=>harnessMenu.data&&harnessMenu.env==='kaxa'&&"
                               "document.getElementById('harness-status').textContent===''")
        assert 'kaxa' in page.locator('#harness-scope').inner_text()
        # The same controls as Main: manager harness, autonomy and the default worker harness; no model or effort.
        labels = page.locator('#harness-menu label:visible').all_inner_texts()
        assert labels == ['Environment', 'Manager harness', 'Autonomy', 'Default worker harness']
        assert set(labels) >= set(main_labels)
        assert page.locator('#harness-menu select:visible').count() == 4
        assert page.locator('#harness-worker').input_value() == 'coding'
        page.locator('#harness-worker').select_option('coding')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent.startsWith('Saved for kaxa')")
        assert harness.saved(user_db, user_project)['worker'] == 'coding'
        page.locator('#harness-manager').select_option('zero')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent.startsWith('Saved for kaxa')")
        assert harness.manager_turn(user_db, None)[0].id == 'zero'
        page.locator('#harness-autonomy').select_option('finish_request')
        page.wait_for_function("()=>harnessMenu.data.autonomy==='finish_request'&&!harnessMenu.saving")
        assert harness.manager_turn(user_db, None)[1] == 'finish_request'
        assert harness.manager_turn(user_db, user_project) == harness.manager_turn(user_db, None)
        assert user_db.setting('model_choice') is None  # the harness panel never touches models
        assert page.evaluate(main_scope)['manager']['id'] == main_before  # Main unchanged

        page.locator('#harness-environment').select_option('Offline')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent==='Offline is unreachable.'")
        assert page.locator('#harness-scope').inner_text() == 'Offline is unreachable'
        assert 'unreachable' in page.locator('#harness-manager-description').inner_text()
        assert page.locator('#harness-manager').is_disabled()
        assert not (tmp_path / 'missing').exists()

        page.locator('#harness-environment').select_option('')
        page.wait_for_function("()=>harnessMenu.data&&!harnessMenu.env&&!harnessMenu.error")
        assert page.locator('#harness-manager').input_value() == main_before
    finally:
        manifest.unlink()


def test_access_switch_confirms_disable_enables_at_once_and_never_shows_for_main(page, browser_context, tmp_path):
    from ai_manager import access
    _, _, root = browser_context
    user_db = Database(tmp_path / 'writer' / 'manager.sqlite3')
    user_db.run("INSERT INTO sessions VALUES('someone','csrf',?)", (2e9,))
    manifest = root / 'data' / 'deploy' / 'installed.json'
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps({'instances': [
        {'name': 'kaxa', 'data': str(tmp_path / 'writer'), 'url': 'http://127.0.0.1:1', 'service': 'k.service'},
        {'name': 'Offline', 'data': str(tmp_path / 'missing'), 'url': 'http://127.0.0.1:2', 'service': 'o.service'}]}))
    toggle, dialog = page.locator('#harness-access-toggle'), page.locator('#access-confirm')
    try:
        page.locator('#harness-button').click()
        page.wait_for_function("()=>harnessMenu.data&&!document.getElementById('harness-environment-group').hidden")
        assert page.locator('#harness-access').is_hidden()  # Main has no switch

        page.locator('#harness-environment').select_option('kaxa')
        page.wait_for_function("()=>harnessMenu.access&&harnessMenu.env==='kaxa'")
        assert toggle.inner_text() == 'Disable access' and 'danger' in toggle.get_attribute('class')
        assert page.locator('#harness-access-state').inner_text() == 'Access to kaxa is enabled.'
        toggle.click()
        assert dialog.is_visible()
        assert page.locator('#access-confirm-title').inner_text() == \
            'Disable access to kaxa? Everyone signed in will be signed out.'
        page.locator('#access-confirm-cancel').click()
        assert dialog.is_hidden() and user_db.setting(access.KEY) is None  # nothing flips without confirming
        assert page.locator('#harness-menu').is_visible()

        toggle.click()
        page.locator('#access-confirm-submit').click()
        page.wait_for_function("()=>harnessMenu.access?.disabled&&!document.getElementById('access-confirm').open")
        assert user_db.setting(access.KEY) is True
        assert user_db.one('SELECT count(*) AS n FROM sessions')['n'] == 0
        assert toggle.inner_text() == 'Enable access' and 'danger' not in toggle.get_attribute('class')
        assert 'disabled' in page.locator('#harness-access-state').inner_text()

        toggle.click()  # enabling needs no confirmation
        page.wait_for_function("()=>harnessMenu.access&&!harnessMenu.access.disabled&&!harnessMenu.saving")
        assert dialog.is_hidden() and user_db.setting(access.KEY) is None
        assert toggle.inner_text() == 'Disable access'

        page.locator('#harness-environment').select_option('Offline')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent==='Offline is unreachable.'")
        assert toggle.is_disabled()
        assert page.locator('#harness-access-state').inner_text() == 'Offline is unreachable; access cannot be changed.'
    finally:
        manifest.unlink()

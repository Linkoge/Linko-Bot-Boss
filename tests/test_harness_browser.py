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
    assert 'Linko' in page.locator('#harness-manager-description').inner_text() or \
        'RULES.md' in page.locator('#harness-manager-description').inner_text()
    invalid = page.locator('#harness-invalid').inner_text()
    assert 'sneaky' in invalid and 'cannot set deploy' in invalid
    assert page.locator('#harness-worker option').all_inner_texts() == ['Coding', 'Docs writer']
    menu = page.locator('#harness-menu').bounding_box()
    assert menu['x'] >= 0 and menu['x'] + menu['width'] <= 390.5
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.id') == 'harness-autonomy'
    page.keyboard.press('Shift+Tab')
    page.keyboard.press('Shift+Tab')  # wraps inside the menu
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
        assert page.locator('#harness-model-group').is_hidden()  # Main keeps its own model picker

        page.locator('#harness-environment').select_option('kaxa')
        page.wait_for_function("()=>harnessMenu.data&&harnessMenu.models&&"
                               "document.getElementById('harness-status').textContent===''")
        assert 'kaxa' in page.locator('#harness-scope').inner_text()
        assert page.locator('#harness-worker-group').is_hidden() and page.locator('#harness-model-group').is_visible()
        page.locator('#harness-manager').select_option('zero')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent.startsWith('Saved for kaxa')")
        assert harness.manager_turn(user_db, None)[0].id == 'zero'
        page.locator('#harness-autonomy').select_option('finish_request')
        page.wait_for_function("()=>harnessMenu.data.autonomy==='finish_request'&&!harnessMenu.saving")
        assert harness.manager_turn(user_db, None)[1] == 'finish_request'
        model = page.locator('#harness-model option:not([disabled])').nth(1).get_attribute('value')
        page.locator('#harness-model').select_option(model)
        page.wait_for_function("(m)=>harnessMenu.models.settings.manager.model===m&&!harnessMenu.saving", arg=model)
        assert user_db.setting('model_choice')['manager']['model'] == model
        page.locator('#harness-effort').select_option('high')
        page.wait_for_function("()=>harnessMenu.models.settings.manager.effort==='high'&&!harnessMenu.saving")
        assert user_db.setting('model_choice')['manager'] == {'model': model, 'effort': 'high'}
        assert page.evaluate(main_scope)['manager']['id'] == main_before  # Main unchanged

        page.locator('#harness-environment').select_option('Offline')
        page.wait_for_function("()=>document.getElementById('harness-status').textContent==='Offline is unreachable.'")
        assert page.locator('#harness-scope').inner_text() == 'Offline is unreachable'
        assert 'unreachable' in page.locator('#harness-manager-description').inner_text()
        assert page.locator('#harness-manager').is_disabled()
        assert not (tmp_path / 'missing').exists()

        page.locator('#harness-environment').select_option('')
        page.wait_for_function("()=>harnessMenu.data&&!harnessMenu.models&&!harnessMenu.error")
        assert page.locator('#harness-manager').input_value() == main_before
        assert page.locator('#harness-model-group').is_hidden()
    finally:
        manifest.unlink()

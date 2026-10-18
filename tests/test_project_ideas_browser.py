"""Chromium: global project ideas, real clipboard, collapse and reload persistence."""
import sqlite3

import pytest
from playwright.sync_api import expect
from test_github_projects_browser import browser_app, page  # noqa: F401


def open_ideas(page):  # noqa: F811
    page.locator('#project-menu-button').click()
    menu = page.locator('#project-menu')
    expect(menu.locator('#project-menu-content > :first-child button')).to_have_text('Project ideas')
    menu.get_by_role('button', name='Project ideas', exact=True).click()
    expect(menu).not_to_be_visible()
    expect(page.locator('#view-ideas')).to_be_visible()


def database_work(root):
    with sqlite3.connect(root / 'data/manager.sqlite3') as conn:
        return {table: conn.execute(f'SELECT * FROM {table}').fetchall()
                for table in ('projects', 'tasks', 'executions', 'todos')}


@pytest.mark.parametrize('width', [1200, 390])
def test_project_idea_crud_real_clipboard_collapse_and_reload(page, browser_app, width):  # noqa: F811
    context, url, root = browser_app
    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=url)
    page.set_viewport_size({'width': width, 'height': 900})
    before = database_work(root)
    page.screenshot(path=str(root / f'ideas-overview-{width}.png'))
    open_ideas(page)
    page.locator('#project-idea-add').click()
    text = 'Future garden planner\n\nKeep <tags> literal.\n  Preserve these instructions.\n'
    page.locator('#editor [name=details]').fill(text)
    assert page.locator('#editor [name=details]').get_attribute('rows') == '12'
    expect(page.locator('#editor [name=title]')).to_have_value('')
    page.locator('#editor button[type=submit]').click()
    expect(page.locator('#editor')).not_to_be_visible()
    row = page.locator('#project-idea-list article')
    expect(row).to_have_count(1)
    expect(row.locator('summary')).to_have_text('Future garden planner')
    expect(row.locator('details')).not_to_have_attribute('open', '')
    expect(row.locator('.todo-details')).not_to_be_visible()
    assert row.locator('button').all_inner_texts() == ['Copy', 'Edit', 'Delete']
    row.get_by_role('button', name='Copy', exact=True).click()
    expect(page.locator('#notice-text')).to_have_text('Project idea copied.')
    assert page.evaluate('()=>navigator.clipboard.readText()') == 'Future garden planner\n\n' + text
    row.locator('summary').click()
    expect(row.locator('.todo-details')).to_be_visible()
    assert row.locator('.todo-details').text_content() == text
    page.screenshot(path=str(root / f'ideas-expanded-{width}.png'))
    # Background list refresh preserves the owner's expanded description.
    page.evaluate('()=>loadProjectIdeas()')
    expect(row.locator('.todo-details')).to_be_visible()
    row.get_by_role('button', name='Edit', exact=True).click()
    page.locator('#editor [name=title]').fill('My custom future project')
    edited = text + '\nEdited later.'
    page.locator('#editor [name=details]').fill(edited)
    page.locator('#editor button[type=submit]').click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(row.locator('summary')).to_have_text('My custom future project')
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    open_ideas(page)
    expect(row.locator('summary')).to_have_text('My custom future project')
    expect(row.locator('.todo-details')).not_to_be_visible()
    row.get_by_role('button', name='Copy', exact=True).click()
    expect(page.locator('#notice-text')).to_have_text('Project idea copied.')
    assert page.evaluate('()=>navigator.clipboard.readText()') == 'My custom future project\n\n' + edited
    page.screenshot(path=str(root / f'ideas-collapsed-{width}.png'))
    assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
    for button in row.locator('button').all():
        assert button.bounding_box()['width'] >= 44
    # Dismiss then accept the native confirmation; dismissing must not delete anything.
    page.once('dialog', lambda dialog: dialog.dismiss())
    row.get_by_role('button', name='Delete', exact=True).click()
    expect(row).to_have_count(1)
    page.once('dialog', lambda dialog: dialog.accept())
    row.get_by_role('button', name='Delete', exact=True).click()
    expect(row).to_have_count(0)
    page.reload()
    page.wait_for_function('()=>state.ready&&!!state.history')
    open_ideas(page)
    expect(page.locator('#project-idea-list .empty')).to_have_text('No project ideas yet.')
    assert database_work(root) == before
    page.locator('#project-idea-back').click()
    expect(page.locator('#view-chat')).to_be_visible()


def test_project_idea_copy_uses_shared_fallback_on_insecure_contexts(page):  # noqa: F811
    page.evaluate("()=>api('/api/project-ideas',{method:'POST',body:JSON.stringify({title:'Fallback',details:'Full description'})})")
    open_ideas(page)
    page.evaluate("""()=>{
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
      window.copiedIdea=null;
      document.execCommand=command=>{if(command==='copy'){window.copiedIdea=document.activeElement.value;return true;}return false;};
    }""")
    row = page.locator('#project-idea-list article')
    row.get_by_role('button', name='Copy', exact=True).click()
    expect(page.locator('#notice-text')).to_have_text('Project idea copied.')
    assert page.evaluate('()=>copiedIdea') == 'Fallback\n\nFull description'
    assert page.locator('.clipboard-fallback').count() == 0
    page.once('dialog', lambda dialog: dialog.accept())
    row.get_by_role('button', name='Delete', exact=True).click()
    expect(row).to_have_count(0)

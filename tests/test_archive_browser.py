"""Archive controls round trip through the real UI and a disposable database."""
# ruff: noqa: F811 -- pytest fixtures imported by name
from test_chat_open_start_browser import seed_history
from test_model_picker_browser import browser_context, page, server  # noqa: F401


def test_archive_part_then_all(page, browser_context):
    seed_history(browser_context)
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===50')
    current = page.evaluate('state.history.conversation.id')
    page.get_by_role('button', name='Chat controls', exact=True).click()
    page.get_by_role('menuitem', name='Archive part 10', exact=True).click()
    assert 'latest 10 messages' in page.locator('#chat-confirm-description').inner_text()
    page.locator('#chat-archive-name').fill('Earlier browser messages')
    page.locator('#chat-confirm-submit').click()
    page.wait_for_function('()=>state.history?.messages.length===10')
    assert page.evaluate('state.history.conversation.id') == current
    page.reload()
    page.wait_for_function('()=>state.history?.messages.length===10')
    page.get_by_role('button', name='Chat controls', exact=True).click()
    assert page.get_by_role('menuitem', name='Archive part 10', exact=True).is_disabled()
    page.get_by_role('menuitem', name='Archive all', exact=True).click()
    page.locator('#chat-confirm-submit').click()
    page.wait_for_function('()=>state.history?.messages.length===0')
    assert page.evaluate('state.history.conversation.id') != current
    context, url, _ = browser_context
    chats = context.request.get(url + '/api/chat-history').json()['conversations']
    assert sorted(c['message_count'] for c in chats if c['archived_at']) == [10, 145]

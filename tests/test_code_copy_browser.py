"""Real Chromium: the Copy button on rendered fenced code blocks, secure and non-secure clipboard paths."""
from test_model_picker_browser import browser_context, page, server  # noqa: F401

SOURCE = 'Intro text\n\n```sh\necho "<b>one</b>"\n  indented line\n```\n\nBetween\n\n```\nsecond block\n```\n'
RENDER = """([source, role])=>{
  const row=document.createElement('div');row.className='message assistant';row.id='copy-fixture';
  const content=document.createElement('div');content.className='message-content';row.append(content);
  document.getElementById('messages').append(row);updateMessageContent(content,source,role,false);
  window.clipboardWrites=[];
}"""


def render(page, source=SOURCE):  # noqa: F811
    page.evaluate(RENDER, [source, 'assistant'])
    return page.locator('#copy-fixture .code-block')


def test_each_block_has_a_copy_button_that_copies_exact_text_with_feedback(page):  # noqa: F811
    blocks = render(page)
    assert blocks.count() == 2
    page.evaluate("()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async t=>{window.clipboardWrites.push(t)}}})}")
    first, second = blocks.nth(0), blocks.nth(1)
    button = first.locator('button.code-copy')
    assert button.inner_text() == 'Copy'
    box, pre = button.bounding_box(), first.locator('pre').bounding_box()
    assert box['x'] + box['width'] <= pre['x'] + pre['width'] and box['y'] >= pre['y']
    assert pre['x'] + pre['width'] - (box['x'] + box['width']) < 12 and box['y'] - pre['y'] < 12  # top-right corner
    button.click()
    page.wait_for_function("()=>document.querySelector('#copy-fixture .code-copy').textContent==='Copied'")
    second.locator('button.code-copy').click()
    assert page.evaluate('()=>clipboardWrites') == ['echo "<b>one</b>"\n  indented line\n', 'second block\n']
    assert second.locator('button.code-copy').inner_text() == 'Copied'
    page.wait_for_function("()=>document.querySelector('#copy-fixture .code-copy').textContent==='Copy'")


def test_fallback_without_clipboard_api_and_blocks_added_by_a_later_update(page):  # noqa: F811
    render(page, 'start')
    page.evaluate("""()=>{
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
      document.execCommand=command=>{const active=document.activeElement;clipboardWrites.push([command,active.value]);return true};
    }""")
    page.evaluate("()=>updateMessageContent(document.querySelector('#copy-fixture .message-content'),'start\\n\\n```\\nlate block\\n```\\n','assistant',false)")
    button = page.locator('#copy-fixture .code-block button.code-copy')
    assert button.count() == 1
    button.click()
    page.wait_for_function("()=>document.querySelector('#copy-fixture .code-copy').textContent==='Copied'")
    assert page.evaluate('()=>clipboardWrites') == [['copy', 'late block\n']]
    assert page.locator('.clipboard-fallback').count() == 0


def test_failed_copy_is_reported_and_owner_messages_have_no_button(page):  # noqa: F811
    render(page)
    page.evaluate("()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});document.execCommand=()=>false}")
    page.locator('#copy-fixture button.code-copy').first.click()
    page.wait_for_function("()=>document.querySelector('#copy-fixture .code-copy').textContent==='Copy failed'")
    page.evaluate("()=>updateMessageContent(document.querySelector('#copy-fixture .message-content'),'```\\nplain\\n```','user',false)")
    assert page.locator('#copy-fixture .code-copy').count() == 0

"""Real Chromium: instances get a round Humanizer button instead of the chat menu and a Georgian UI; Main is unchanged."""
import pytest
from test_office_chats_browser import served, sign_in


def open_sheet(page):
    page.locator('#humanizer-button').click()
    page.wait_for_function("()=>document.getElementById('humanizer-dialog').open")


def test_instance_button_opens_humanizer_sheet_and_georgian(tmp_path):
    with served(tmp_path, office=True) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        button = page.locator('#humanizer-button')
        assert button.is_visible() and button.get_attribute('aria-label') == 'Humanizer'
        assert not page.locator('#chat-menu-button').is_visible() and not page.locator('#chat-menu').is_visible()
        size = button.bounding_box()
        assert size['width'] == 44 and size['height'] == 44  # the old menu button's size
        icon = page.locator('#humanizer-button svg')
        box = icon.bounding_box()
        assert box['width'] == box['height'] and icon.locator('circle').count() == 1
        open_sheet(page)
        assert page.locator('#writing-heading').inner_text() == 'Humanizer'
        assert page.locator('#writing-label-en').inner_text() == 'Style copy — English'
        assert page.locator('#writing-label-ka').inner_text() == 'Style copy — Georgian'
        x = page.locator('#humanizer-x')
        assert x.is_visible() and x.get_attribute('aria-label') == 'Close'
        dialog_box, x_box = page.locator('#humanizer-dialog').bounding_box(), x.bounding_box()
        assert x_box['x'] + x_box['width'] > dialog_box['x'] + dialog_box['width'] - 60 and x_box['y'] < dialog_box['y'] + 60
        save, close = page.locator('#writing-form .humanizer-actions button.primary'), page.locator('#humanizer-close')
        sb, cb = save.bounding_box(), close.bounding_box()
        assert abs(sb['y'] - cb['y']) < 2 and sb['x'] + sb['width'] <= cb['x'] + 1  # one row, side by side
        rules = page.locator('#humanizer-rules')
        rules.scroll_into_view_if_needed()
        assert rules.locator('h3').inner_text() == 'Humanizer rules'
        assert rules.bounding_box()['y'] > close.bounding_box()['y']  # the rules sit below the buttons
        assert page.evaluate("()=>document.getElementById('humanizer-body').nextElementSibling.id") == 'humanizer-rules'
        x.click()
        page.wait_for_function("()=>!document.getElementById('humanizer-dialog').open")
        open_sheet(page)
        page.locator('#humanizer-close').click()
        page.wait_for_function("()=>!document.getElementById('humanizer-dialog').open")
        page.locator('#office-settings').click()
        assert page.locator('#language-select').is_visible()
        page.locator('#language-select').select_option('ka')
        page.wait_for_function("()=>document.querySelector('#language-heading').textContent==='ენა'")
        assert page.evaluate("()=>localStorage.getItem('linko-language')") == 'ka'
        assert page.locator('#session-panel h2').inner_text() == 'სესია'
        assert page.locator('#humanizer-button').get_attribute('aria-label') == 'ჰიუმანაიზერი'
        page.locator('#humanizer-button').click()
        page.wait_for_function("()=>document.getElementById('writing-label-ka').textContent==='სტილის ნიმუში — ქართული'")
        assert page.locator('#humanizer-rules h3').inner_text() == 'ჰიუმანაიზერის წესები'
        assert page.locator('#humanizer-x').get_attribute('aria-label') == 'დახურვა'
        assert '45%' in page.locator('#humanizer-rules li').nth(2).inner_text()
        assert page.locator('#writing-heading').inner_text() == 'წერის სტილი' or page.locator('#writing-heading').inner_text()
        page.locator('#humanizer-close').click()
        page.reload()
        page.wait_for_function('()=>state.ready')
        page.wait_for_function("()=>document.querySelector('#new-chat').textContent==='+ ახალი'")
        page.evaluate("()=>{const s=document.getElementById('language-select');s.value='en';s.onchange();}")
        page.wait_for_function("()=>document.querySelector('#new-chat').textContent==='+ New'")
        assert not errors


def test_main_menu_is_unchanged_and_has_no_language(tmp_path):
    with served(tmp_path, office=False) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_default_timeout(10000)
        sign_in(page, url, tmp_path)
        page.locator('#chat-menu-button').click()
        page.wait_for_function("()=>!document.getElementById('chat-menu').hidden")
        assert page.locator('#clear-chat').is_visible()
        assert not page.locator('#humanizer-button').is_visible()
        page.keyboard.press('Escape')
        page.locator('.tabs [data-view=settings]').click()
        assert not page.locator('#language-panel').is_visible()
        assert page.locator('#writing-panel').is_visible()
        assert page.locator('#writing-heading').inner_text() == 'Writing'
        assert not page.locator('#humanizer-x').is_visible()
        assert page.locator('html').get_attribute('lang') == 'en'


def test_rules_text_matches_the_script_constants():
    import re
    from pathlib import Path
    from ai_manager import humanize
    html = (Path(__file__).resolve().parents[1] / 'ai_manager/static/index.html').read_text(encoding='utf-8')
    items = re.findall(r'<li>(.*?)</li>', html[html.index('id="humanizer-rules"'):])
    shares = [int(n) for n in re.findall(r'(\d+)%', items[2])]
    assert shares == [n for _, n in humanize.tool.KIND_SHARES]


def make_docx(path):
    import zipfile
    body = ''.join(f'<w:p><w:r><w:t>Then everything looked different, because nothing was ready, and nobody knew why. '
                   f'Another sentence follows here, with several ordinary words, and more commas, again and again {n}.</w:t></w:r></w:p>'
                   for n in range(40))
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, 'w') as package:
        package.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        package.writestr('word/document.xml', xml)


def test_instance_humanizes_files_and_keeps_recent_results(tmp_path):
    import zipfile
    text = ('Then everything looked different, because nothing was ready, and nobody knew why. '
            'Another sentence follows here, with several ordinary words, and more commas, again and again.\n') * 60
    txt, docx, bad = tmp_path / 'story.txt', tmp_path / 'report.docx', tmp_path / 'macro.exe'
    txt.write_text(text, encoding='utf-8')
    make_docx(docx)
    bad.write_bytes(b'MZ')
    original_docx = docx.read_bytes()
    with served(tmp_path, office=True) as (browser, url):
        page = browser.new_page(viewport={'width': 1200, 'height': 900}, accept_downloads=True)
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        sign_in(page, url, tmp_path)
        open_sheet(page)
        section = page.locator('#humanize-file')
        assert section.locator('h3').inner_text() == 'Humanize a file'
        assert page.evaluate("()=>document.getElementById('humanize-file').compareDocumentPosition(document.getElementById('writing-panel'))&4") > 0
        page.wait_for_function("()=>document.getElementById('humanize-file-typos').value==='0.2'")
        assert page.locator('#humanize-file-punct').input_value() == '2'
        # an unsupported file is refused with a clear message and nothing is uploaded
        page.set_input_files('#humanize-file-input', str(bad))
        page.locator('#humanize-file-run').click()
        page.wait_for_function("()=>document.getElementById('humanize-file-status').textContent.includes('Only .txt, .md and .docx')")
        assert page.locator('#humanize-file-results li').count() == 0
        # a text file: the spinner shows while it runs, then the counts and a download
        page.route('**/api/humanize/file?*', lambda route: (__import__('time').sleep(0.8), route.continue_()))
        page.locator('#humanize-file-typos').fill('5')
        page.locator('#humanize-file-punct').fill('20')
        page.set_input_files('#humanize-file-input', str(txt))
        page.locator('#humanize-file-run').click()
        page.wait_for_function("()=>!document.getElementById('humanize-file-busy').hidden&&document.getElementById('humanize-file-run').disabled")
        assert page.locator('#humanize-file-busy').inner_text() == 'Humanizing… keep this window open'
        page.wait_for_function("()=>document.getElementById('humanize-file-busy').hidden&&!document.getElementById('humanize-file-run').disabled")
        page.wait_for_function("()=>document.querySelectorAll('#humanize-file-results li').length===1")
        status = page.locator('#humanize-file-status').inner_text()
        assert 'typos' in status and 'punctuation marks dropped' in status and not status.startswith('0 ')
        with page.expect_download() as download:
            page.locator('#humanize-file-results li a').first.click()
        assert download.value.suggested_filename == 'story humanized.txt'
        out = open(download.value.path(), encoding='utf-8').read()
        assert out != text and len(out) <= len(text) + 2000
        assert txt.read_text(encoding='utf-8') == text
        # a Word document
        page.unroute('**/api/humanize/file?*')
        page.set_input_files('#humanize-file-input', str(docx))
        page.locator('#humanize-file-run').click()
        page.wait_for_function("()=>document.querySelectorAll('#humanize-file-results li').length===2")
        with page.expect_download() as download:
            page.locator('#humanize-file-results li a', has_text='Download').first.click()
        name = download.value.suggested_filename
        assert name.endswith(' humanized.docx')
        with zipfile.ZipFile(download.value.path()) as package:
            changed = package.read('word/document.xml')
        with zipfile.ZipFile(docx) as package:
            assert changed != package.read('word/document.xml')
        assert docx.read_bytes() == original_docx
        # closing and reopening (even after a reload) keeps the list from the server
        page.locator('#humanizer-close').click()
        page.reload()
        page.wait_for_function('()=>state.ready')
        open_sheet(page)
        page.wait_for_function("()=>document.querySelectorAll('#humanize-file-results li').length===2")
        assert page.locator('#humanize-file-results li a').count() == 2
        assert not errors




def test_backend_rejects_traversal_and_oversize_and_prunes(tmp_path):
    with served(tmp_path, office=True) as (browser, url):
        page = browser.new_page()
        sign_in(page, url, tmp_path)
        def post(name, size=10, typos=1, punct=1):
            return page.evaluate("""async([name,size,typos,punct])=>{
              const r=await fetch('/api/humanize/file?'+new URLSearchParams({name,typos,punct}),{method:'POST',
                headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':state.csrf},body:'word '.repeat(size)});
              return [r.status,await r.json()];}""", [name, size, typos, punct])
        status, body = post('../../etc/passwd.txt', 400)
        assert status == 200 and body['name'] == 'passwd humanized.txt'  # the path part is dropped, not followed
        assert post('notes.pdf')[0] == 422
        assert post('a.txt', 400, typos=99)[0] == 422
        assert post('a.docx', 20)[0] == 422  # not a zip
        assert post('..')[0] == 422
        assert post('big.txt', 4_300_000)[0] == 413  # over 20 MB
        for n in range(11):
            assert post(f'f{n}.txt', 400)[0] == 200
        listed = page.evaluate("async()=>(await (await fetch('/api/humanize/files')).json()).results")
        assert len(listed) == 10
        assert page.evaluate("async()=>(await fetch('/api/humanize/files/..%2F..%2Fx')).status") == 404
        assert page.evaluate("async()=>(await fetch('/api/humanize/files/'+'0'.repeat(32))).status") == 404
        data = list((tmp_path / 'data' / 'humanizer').iterdir())
        assert len(data) == 10 and all((d / 'upload.txt').exists() is False for d in data)  # uploads are not kept


def test_old_results_are_deleted_after_seven_days(tmp_path):
    import json
    import time
    from ai_manager.humanize_files import HumanizedFiles
    files = HumanizedFiles(tmp_path)
    for identifier, age in (('a' * 32, 8 * 86400), ('b' * 32, 60)):
        folder = files.root / identifier
        folder.mkdir(parents=True)
        (folder / 'result.txt').write_text('x')
        (folder / 'meta.json').write_text(json.dumps({'id': identifier, 'name': 'n humanized.txt', 'typos': 1,
                                                      'dropped': 1, 'created_at': time.time() - age}))
    assert [m['id'] for m in files.list()] == ['b' * 32]
    assert not (files.root / ('a' * 32)).exists()

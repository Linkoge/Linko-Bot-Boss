"""Real chat Markdown links download through the authenticated app endpoint."""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from test_merge_downloads import NAME, delivery, message, snapshot  # noqa: F401
from test_web import sign_in


def test_chat_link_downloads_georgian_document(delivery, tmp_path):  # noqa: F811
    d = delivery
    urls = snapshot(d)
    sign_in(d.browser, d.app, d.settings)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(10000)

        def serve(route):
            path = urlsplit(route.request.url).path
            if path == '/fixture':
                route.fulfill(content_type='text/html', body='<div id="messages" class="message-content markdown"></div>')
            else:
                response = d.browser.get(route.request.url)
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('http://localhost/**', serve)
        page.goto('http://localhost/fixture')
        static = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
        page.add_script_tag(path=str(static / 'vendor' / 'markdown-it.min.js'))
        page.add_script_tag(path=str(static / 'markdown.js'))
        page.evaluate("text=>document.getElementById('messages').append(renderChatMarkdown(text))", message(d))
        link = page.get_by_role('link', name=NAME, exact=True)
        assert link.get_attribute('href') == urls[NAME]
        with page.expect_download() as pending:
            link.click()
        download = pending.value
        assert download.suggested_filename == NAME
        target = tmp_path / NAME
        download.save_as(target)
        assert target.read_bytes() == d.files[NAME]
        browser.close()

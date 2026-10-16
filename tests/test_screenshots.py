"""Actual local Chromium captures, bounded selection and image transport on both providers."""

import asyncio
import base64
import json
import os
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_minimal_flow import NativeFixture
from test_minimal_flow import system as system_fixture

from ai_manager import screenshots
from ai_manager.claude_host import Host
from ai_manager.codex import MANAGER_INSTRUCTIONS, AppServer
from ai_manager.core import Core, Tasks
from ai_manager.orchestration import MANAGER_TOOL, Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.tool_content import ImageResult

system = system_fixture


def png_size(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    return struct.unpack('>II', data[16:24])


def sample_overview(data):
    # Decode in Chromium's canvas so whole-page content, not just PNG dimensions, is verified.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        return browser.new_page().evaluate('''async source => {
          const img = new Image(); img.src = source; await img.decode();
          const canvas = document.createElement('canvas'); canvas.width=img.width;canvas.height=img.height;
          const ctx=canvas.getContext('2d'); ctx.drawImage(img,0,0);
          return [.03,.5,.97].map(y=>Array.from(ctx.getImageData(Math.floor(img.width/2),Math.floor(img.height*y),1,1).data));
        }''', 'data:image/png;base64,' + base64.b64encode(data).decode())


@pytest.fixture(scope='module')
def website():
    stuck = threading.Event()
    release = threading.Event()
    bands = ''.join(f'<section style="background:hsl({i * 23} 70% 60%)">Screen {i + 1}</section>' for i in range(14))
    html = ('''<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
      <style>*{box-sizing:border-box}body{margin:0}section,footer{height:1080px}
      #product-grid{position:absolute;top:7800px;height:100px}
      header{position:fixed;top:0}html{scroll-behavior:smooth}</style>
      <header>Sticky header</header>''' + bands + '''
      <div id="product-grid" role="region" aria-label="product grid">Products</div>
      <footer style="background:#f00">Footer</footer>''').encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/stuck':
                stuck.set()
                release.wait(20)
                return
            body = html
            if self.path == '/login':
                body = b'<input type="password">'
            if self.path == '/short':
                body = b'<style>body{margin:0;background:green}</style><h1>Short page</h1>'
            if self.path == '/lazy':
                body = b'''<!doctype html><style>body{margin:0}section{height:1080px}</style>
                  <section>One</section><section>Two</section><script>
                  addEventListener('scroll', () => {
                    const n=document.querySelectorAll('section').length;
                    if(scrollY+innerHeight>=document.documentElement.scrollHeight && n<15){
                      const s=document.createElement('section');s.textContent='Screen '+(n+1);document.body.append(s);
                    }
                  });</script>'''
            if self.path == '/missing':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', stuck
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.asyncio
async def test_real_overview_has_whole_page_dimensions_and_fifteen_screens(website):
    result = await screenshots.screenshot(website[0])
    meta = result.metadata
    assert meta['page_height'] == 16200
    assert meta['screen_count'] == 15 and meta['summary'] == '15 screens'
    assert meta['viewport'] == {'width': 1920, 'height': 1080, 'scale': 1}
    assert meta['mode'] == 'overview' and len(result.images) == 1 and not meta['tiles']
    width, height = png_size(result.images[0])
    assert width <= 320 and height <= 900 and height > width
    assert abs(width / height - 1920 / 16200) < .002
    colors = await asyncio.to_thread(sample_overview, result.images[0])
    assert colors[-1] == [255, 0, 0, 255]  # Red footer really appears at the bottom of the overview.
    assert colors[0] != colors[1] != colors[2]
    assert max(abs(a - b) for a, b in zip((width, height), meta['overview_size'])) <= 1


@pytest.mark.asyncio
async def test_overview_keeps_lazy_scroll_created_content_and_short_page_height(website):
    lazy = await screenshots.screenshot(website[0] + '/lazy')
    assert lazy.metadata['page_height'] == 16200 and lazy.metadata['screen_count'] == 15
    short = await screenshots.screenshot(website[0] + '/short')
    assert short.metadata['page_height'] == 1080 and short.metadata['screen_count'] == 1
    assert png_size(short.images[0]) == (320, 180)


@pytest.mark.asyncio
async def test_real_tiles_reach_top_middle_last_and_full_review_uses_two_calls(website):
    first = await screenshots.screenshot(website[0], tiles='1-3,8,last')
    assert [t['index'] for t in first.metadata['tiles']] == [1, 2, 3, 8, 15]
    assert [t['scroll_y'] for t in first.metadata['tiles']] == [0, 1080, 2160, 7560, 15120]
    assert all(png_size(data) == (1920, 1080) for data in first.images)
    assert len(set(first.images)) == 5  # Distinct rendered content, not repeats of the top.
    for selection, expected in [('1-8', list(range(1, 9))), ('9-15', list(range(9, 16)))]:
        result = await screenshots.screenshot(website[0], tiles=selection)
        assert [t['index'] for t in result.metadata['tiles']] == expected
        assert len(result.images) == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('selector,y', [('#product-grid', 7310), ('product grid', 7310), ('footer', 15120)])
async def test_real_selector_and_named_element_are_centered_in_one_screen(website, selector, y):
    result = await screenshots.screenshot(website[0], selector=selector)
    assert result.metadata['tiles'][0]['scroll_y'] == y
    assert result.metadata['tiles'][0]['selector'] == selector
    assert len(result.images) == 1 and png_size(result.images[0]) == (1920, 1080)


@pytest.mark.asyncio
async def test_mobile_overview_and_end_tile_keep_device_pixels_and_partial_screen_count(website):
    overview = await screenshots.screenshot(website[0], device='mobile')
    assert overview.metadata['page_height'] == 16200
    assert overview.metadata['screen_count'] == 20  # ceil(16200 / 845), not device pixel height.
    width, height = png_size(overview.images[0])
    assert width <= 320 and height <= 900 and height > width
    last = await screenshots.screenshot(website[0], device='mobile', tiles='last')
    assert png_size(last.images[0]) == (780, 1690)
    assert last.metadata['tiles'][0]['scroll_y'] == 15355


@pytest.mark.smoke
@pytest.mark.parametrize('selection', ['1-9', '1,2,3,4,5,6,7,8,last', '1-999999999999'])
def test_eight_tile_limit_applies_to_each_request_before_browser_start(selection):
    with pytest.raises(ValueError, match='At most 8 tiles per call'):
        screenshots.validate_request('https://shop.example', tiles=selection)
    assert screenshots.select_tiles('1-8', 15) == list(range(1, 9))
    assert screenshots.select_tiles('9-15', 15) == list(range(9, 16))
    assert screenshots.select_tiles('1-3,3,last', 15) == [1, 2, 3, 15]


@pytest.mark.parametrize('selection', ['0', '-1', '2-1', '1,,2', '1-', 'all', '1.5'])
def test_invalid_tile_selection_fails_clearly(selection):
    with pytest.raises(ValueError):
        screenshots.select_tiles(selection, 15)
    with pytest.raises(ValueError, match='outside this page'):
        screenshots.select_tiles('16', 15)


@pytest.mark.asyncio
async def test_invalid_url_options_and_unknown_selector_do_not_masquerade_as_captures(website):
    for url in ('file:///etc/passwd', 'ftp://example.com', '', 'http://user:pass@example.com'):
        with pytest.raises(ValueError):
            await screenshots.screenshot(url)
    with pytest.raises(ValueError, match='Choose tiles or one selector'):
        await screenshots.screenshot(website[0], tiles='1', selector='footer')
    with pytest.raises(ValueError, match='No visible element'):
        await screenshots.screenshot(website[0], selector='#missing-element')
    with pytest.raises(ValueError, match='multiple visible elements'):
        await screenshots.screenshot(website[0], selector='section')
    with pytest.raises(ValueError, match='HTTP 404'):
        await screenshots.screenshot(website[0] + '/missing')
    with pytest.raises(ValueError, match='behind a login'):
        await screenshots.screenshot(website[0] + '/login')


@pytest.mark.asyncio
async def test_real_stuck_page_times_out_kills_browser_group_and_removes_temporary_files(website, tmp_path, monkeypatch):
    monkeypatch.setattr(screenshots, 'CAPTURE_TIMEOUT', 3)
    monkeypatch.setattr(screenshots.tempfile, 'tempdir', str(tmp_path))
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def record(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(screenshots.asyncio, 'create_subprocess_exec', record)
    started = time.monotonic()
    with pytest.raises(ValueError, match='Screenshot timed out after 3 seconds'):
        await screenshots.screenshot(website[0] + '/stuck')
    assert 2.8 <= time.monotonic() - started < 8
    assert website[1].is_set()  # Real pending HTTP navigation was killed.
    assert processes[0].returncode is not None
    assert list(tmp_path.iterdir()) == []
    # Descendants in the owned group must be dead, not orphaned Chromium renderers.
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            assert int(fields[2]) != processes[0].pid or fields[0] == 'Z'
        except (FileNotFoundError, ProcessLookupError):
            pass


@pytest.mark.asyncio
async def test_cancellation_kills_owned_capture(monkeypatch):
    process = SimpleNamespace(pid=432123, returncode=None)
    killed = []

    async def communicate(_):
        raise asyncio.CancelledError

    async def wait():
        process.returncode = -9

    async def spawn(*args, **kwargs):
        return process

    process.communicate, process.wait = communicate, wait
    monkeypatch.setattr(screenshots.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(screenshots.os, 'killpg', lambda *args: killed.append(args))
    with pytest.raises(asyncio.CancelledError):
        await screenshots.capture({})
    assert killed == [(process.pid, screenshots.signal.SIGKILL)] and process.returncode == -9


@pytest.mark.asyncio
async def test_dev_panel_compatibility_launcher_uses_shared_engine_and_existing_session_contract(website, tmp_path):
    config = {'url': website[0], 'devices': ['desktop'], 'outputDir': str(tmp_path), 'overlap': 0, 'settleMs': 0}
    root = Path(__file__).resolve().parents[1]
    # Exactly how screenshots.py invokes LINKO_PANEL_NODE; the old engine must never execute.
    completed = await asyncio.to_thread(subprocess.run, [sys.executable, str(root / 'scripts/dev_panel_capture.py'),
        '/nonexistent/capture.mjs'], input=json.dumps(config), capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    group = json.loads(completed.stdout)['groups'][0]
    assert group['device'] == 'desktop' and group['truncated'] is False
    assert len(group['images']) == 15
    assert group['images'][-1]['scrollY'] == 15120
    assert 'activeText' in group['images'][0]
    assert json.loads((tmp_path / 'progress.json').read_text()) == {
        'phase': 'Capturing', 'device': 'desktop', 'current': 15}
    assert png_size((tmp_path / group['images'][0]['file']).read_bytes()) == (1920, 1080)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_manager_scopes_capture_and_codex_returns_native_images_without_queueing(system, monkeypatch):
    db, registry, chat, settings = system
    project, other = registry.list()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    calls = []
    image = b'fixture png bytes'

    async def capture(*args):
        calls.append(args)
        return ImageResult({'screen_count': 15}, [image])

    monkeypatch.setattr('ai_manager.orchestration.screenshot', capture)
    params = {'tool': 'manager_action', 'arguments': {'action': 'screenshot', 'url': 'http://100.64.0.1:8765',
              'project_id': project['id'], 'tiles': '8,last'}}
    server = AppServer(settings)
    server.tool_handlers['test'] = lambda p: scheduler.manager_action(project['id'], p)
    replies = []

    async def send(message):
        replies.append(message)

    server.send = send
    try:
        await server._tool_call({'id': 1, 'params': {**params, 'threadId': 'test'}})
        reply = replies[0]['result']
        assert reply['success'] is True
        assert json.loads(reply['contentItems'][0]['text']) == {'screen_count': 15}
        assert base64.b64decode(reply['contentItems'][1]['imageUrl'].split(',')[1]) == image
        assert calls == [('http://100.64.0.1:8765', 'desktop', '8,last', '')]
        assert Tasks(db).list() == []
        params['arguments']['project_id'] = other['id']
        with pytest.raises(ValueError, match='cross-project'):
            await scheduler.manager_action(project['id'], params)
        params['arguments'].pop('project_id')
        with pytest.raises(ValueError, match='Select the intended project'):
            await scheduler.manager_action(None, params)
        assert len(calls) == 1
        assert 'screenshot' in MANAGER_TOOL['inputSchema']['properties']['action']['enum']
        assert 'Before giving UI/design advice, read the relevant code AND take screenshots' in MANAGER_INSTRUCTIONS
    finally:
        await chat.close()
        await workers.close()


@pytest.mark.asyncio
async def test_claude_host_and_real_mcp_stdio_preserve_native_image_blocks(tmp_path):
    # Exercise both bridge hops with a >64KB image, rather than a text-only mock.
    host = Host(tmp_path, 'unused', False)
    host.running['t'] = {'turn': 'turn'}
    image = os.urandom(100000)
    items = ImageResult({'screen_count': 15}, [image]).content_items()

    async def ask(method, params):
        assert method == 'item/tool/call' and params['tool'] == 'manager_action'
        return {'success': True, 'contentItems': items}

    host.ask = ask
    server = await asyncio.start_unix_server(host.bridge, path=str(host.socket))
    script = Path(__file__).resolve().parents[1] / 'ai_manager/claude_mcp.py'
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', str(script), str(host.socket), 't',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'manager_action', 'arguments': {'action': 'screenshot'}}}
        stdout, stderr = await asyncio.wait_for(process.communicate((json.dumps(request) + '\n').encode()), 10)
        assert process.returncode == 0, stderr
        reply = json.loads(stdout)['result']
        assert reply['isError'] is False
        assert json.loads(reply['content'][0]['text']) == {'screen_count': 15}
        assert reply['content'][1]['type'] == 'image' and reply['content'][1]['mimeType'] == 'image/png'
        assert base64.b64decode(reply['content'][1]['data']) == image
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        server.close()
        await server.wait_closed()
        host.socket.unlink()
        host.socket.parent.rmdir()

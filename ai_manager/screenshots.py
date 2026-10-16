"""Shared Dev Panel/manager capture engine, reworked from Dev Panel's capture.mjs.

Preserves real viewport scrolling, lazy-content preparation, visible image/font
readiness and the panel's groups/images contract. Only this module drives a browser;
scripts/dev_panel_capture.py is the compatibility entry point for the existing panel.
The manager runs it in a disposable process group with a hard wall-clock deadline.
"""

import asyncio
import base64
import json
import math
import os
import re
import signal
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .tool_content import ImageResult

MAX_TILES = 8
CAPTURE_TIMEOUT = 60
VIEWPORTS = {'desktop': (1920, 1080, 1), 'mobile': (390, 845, 2)}


def validate_request(url, device='desktop', tiles='', selector=''):
    if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).hostname:
        raise ValueError('Screenshot URL must be an HTTP(S) page reachable by the server')
    if urlsplit(url).username or urlsplit(url).password:
        raise ValueError('Pages behind a login are out of scope; do not include URL credentials')
    if device not in VIEWPORTS:
        raise ValueError('Screenshot device must be desktop or mobile')
    if not isinstance(tiles, str) or not isinstance(selector, str) or (tiles and selector):
        raise ValueError('Choose tiles or one selector, or omit both for an overview')
    if tiles:
        select_tiles(tiles, None)  # Reject malformed or oversized requests before starting Chromium.


def select_tiles(selection, screen_count):
    """One-based indexes, inclusive ranges and last; count limits each call only."""
    indexes = []
    for part in selection.split(','):
        part = part.strip()
        if part == 'last':
            values = [screen_count if screen_count is not None else 'last']
        elif re.fullmatch(r'[1-9]\d*', part):
            values = [int(part)]
        elif re.fullmatch(r'[1-9]\d*\s*-\s*[1-9]\d*', part):
            first, end = map(int, part.split('-'))
            if end < first:
                raise ValueError('Tile ranges must be ascending')
            if end - first + 1 > MAX_TILES:
                raise ValueError(f'At most {MAX_TILES} tiles per call; request the next range in another call')
            values = range(first, end + 1)
        else:
            raise ValueError('Tiles must be one-based indexes/ranges, e.g. 1-3,8,last')
        for index in values:
            if screen_count is not None and index > screen_count:
                raise ValueError(f'Tile {index} is outside this page ({screen_count} screens)')
            if index not in indexes:
                indexes.append(index)
        if len(indexes) > MAX_TILES:
            raise ValueError(f'At most {MAX_TILES} tiles per call; request the next range in another call')
    return indexes


async def capture(config):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'ai_manager.screenshots', cwd=Path(__file__).resolve().parents[1],
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(config).encode()), CAPTURE_TIMEOUT)
        if process.returncode:
            raise ValueError(stderr.decode(errors='replace').strip()[-2000:] or 'Screenshot capture failed')
        return json.loads(stdout)
    except TimeoutError as error:
        raise ValueError(f'Screenshot timed out after {CAPTURE_TIMEOUT} seconds; page did not finish') from error
    finally:
        # Kill the entire owned group, including browser descendants on timeout/cancellation.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def screenshot(url, device='desktop', tiles='', selector=''):
    validate_request(url, device, tiles, selector)
    with tempfile.TemporaryDirectory(prefix='linko-screenshot-') as directory:
        config = {'url': url, 'device': device, 'tiles': tiles, 'selector': selector, 'outputDir': directory}
        metadata = await capture(config)
        return ImageResult(metadata, [(Path(directory) / name).read_bytes() for name in metadata['files']])


def move_to(page, y):
    page.evaluate('y => window.scrollTo({top:y,left:0,behavior:"instant"})', y)
    page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')


def page_height(page):
    return page.evaluate('() => Math.ceil(Math.max(innerHeight, document.documentElement.scrollHeight, '
                         'document.body?.scrollHeight || 0))')


def ready_images(page):
    page.evaluate('''async () => {
      const visible = [...document.images].filter(img => {
        const r = img.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight;
      });
      await Promise.race([Promise.all(visible.map(async img => {
        if (!img.complete) await new Promise(r => {
          img.addEventListener('load', r, {once:true}); img.addEventListener('error', r, {once:true});
        });
        if (img.complete && img.naturalWidth) await img.decode().catch(() => {});
      })), new Promise(r => setTimeout(r, 2000))]);
    }''')


def prepare(page, url, height):
    response = page.goto(url, wait_until='domcontentloaded', timeout=20000)
    if not response or not response.ok:
        raise ValueError(f'Screenshot page returned HTTP {response.status if response else "no response"}')
    if page.locator('input[type="password"]:visible').count():
        raise ValueError('Pages behind a login are out of scope')
    page.evaluate('() => Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2000))])')
    # The Dev Panel engine walks actual viewports to trigger native lazy content.
    # Keep its finite walk bound; refuse a truncated overview instead of hiding the end.
    for index in range(100):
        bottom = max(0, page_height(page) - height)
        target = min(index * height, bottom)
        move_to(page, target)
        page.wait_for_timeout(60)
        ready_images(page)
        if target >= max(0, page_height(page) - height):
            break
    else:
        raise ValueError('Page keeps growing or exceeds 100 screens; capture could not determine its end')
    move_to(page, 0)
    page.wait_for_timeout(150)


def find_element(page, name):
    # CSS selectors, accessible landmark names, common id/class names, then visible text.
    from playwright.sync_api import Error

    candidates = [page.locator(name)]
    slug = re.sub(r'\s+', '-', name.strip())
    if re.fullmatch(r'[\w-]+', slug):
        candidates.extend([page.locator('#' + slug), page.locator('.' + slug)])
    for role in ('region', 'navigation', 'heading', 'button', 'link', 'contentinfo'):
        candidates.append(page.get_by_role(role, name=name, exact=True))
    candidates.append(page.get_by_text(name, exact=True))
    for candidate in candidates:
        try:
            visible = [candidate.nth(i) for i in range(min(candidate.count(), 100)) if candidate.nth(i).is_visible()]
            if len(visible) == 1:
                return visible[0]
            if len(visible) > 1:
                raise ValueError('Screenshot selector matches multiple visible elements; use a specific CSS selector')
        except Error:
            continue  # A name such as "product grid" need not be valid CSS.
    raise ValueError(f'No visible element matches {name!r}; use a CSS selector or exact accessible name/text')


def capture_page(browser, config, device):
    width, height, scale = VIEWPORTS[device]
    options = {'viewport': {'width': width, 'height': height}, 'screen': {'width': width, 'height': height},
               'device_scale_factor': scale, 'is_mobile': device == 'mobile', 'has_touch': device == 'mobile',
               'color_scheme': 'light'}
    if device == 'mobile':
        options['user_agent'] = ('Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
                                 '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
    legacy = config.get('legacy', False)
    with browser.new_context(**options) as context:
        page = context.new_page()
        page.set_default_timeout(10000)
        if legacy:
            origin = config.get('origin') or '{0.scheme}://{0.netloc}'.format(urlsplit(config['url']))
            page.on('framenavigated', lambda frame: page.close()
                    if frame == page.main_frame and not frame.url.startswith(origin + '/') else None)
        progress(config, 'Preparing lazy content', device)
        prepare(page, config['url'], height)
        assert_page(page, config)
        total = page_height(page)
        count = math.ceil(total / height)
        metadata = {'url': page.url, 'device': device, 'page_height': total, 'screen_count': count,
                    'summary': f'{count} screens', 'viewport': {'width': width, 'height': height, 'scale': scale},
                    'files': [], 'tiles': []}
        directory = Path(config['outputDir'])
        selection, selector = config.get('tiles', ''), config.get('selector', '')
        if legacy:
            positions = legacy_positions(page, total, height, config.get('overlap', 20))
        elif selector:
            element = find_element(page, selector)
            element.scroll_into_view_if_needed()
            box = element.bounding_box()
            total = page_height(page)
            target = max(0, min(page.evaluate('scrollY') + box['y'] + box['height'] / 2 - height / 2, total - height))
            positions = [(None, round(target))]
        elif selection:
            positions = [(index, min((index - 1) * height, total - height)) for index in select_tiles(selection, count)]
        else:
            # A thumbnail only: capture at normal layout width and resize the raster, never reflow the page.
            path = directory / f'{device}-overview.png'
            ratio = min(320 / width, 900 / total)
            session = context.new_cdp_session(page)
            shot = session.send('Page.captureScreenshot', {
                'format': 'png', 'captureBeyondViewport': True,
                'clip': {'x': 0, 'y': 0, 'width': width, 'height': total, 'scale': ratio / scale}})
            path.write_bytes(base64.b64decode(shot['data']))
            session.detach()
            metadata['overview_size'] = [round(width * ratio), round(total * ratio)]
            metadata['files'] = [path.name]
            metadata['mode'] = 'overview'
            return metadata
        progress(config, 'Capturing', device)
        for number, (index, target) in enumerate(positions, 1):
            move_to(page, target)
            page.wait_for_timeout(config.get('settleMs', 150))
            ready_images(page)
            assert_page(page, config)
            filename = f'{device}-raw-{number:03d}.png'
            page.screenshot(path=str(directory / filename), timeout=10000)
            item = {'index': index, 'scroll_y': round(page.evaluate('scrollY')), 'file': filename}
            if selector:
                item['selector'] = selector
            if legacy:
                item['activeText'] = page.locator('.slide-text.active, [aria-current="true"]').all_text_contents()
                item['activeText'] = '|'.join(item['activeText'])[:500]
                item['scrollY'] = item['scroll_y']
            metadata['tiles'].append(item)
            metadata['files'].append(filename)
            progress(config, 'Capturing', device, number)
        metadata['mode'] = 'tiles'
        return metadata


def progress(config, phase, device, current=0):
    if config.get('legacy'):
        (Path(config['outputDir']) / 'progress.json').write_text(json.dumps(
            {'phase': phase, 'device': device, 'current': current}))


def assert_page(page, config):
    if config.get('legacy') and (urlsplit(page.url).path or '/') != (urlsplit(config['url']).path or '/'):
        raise ValueError('Selected page changed during capture')


def legacy_positions(page, total, height, overlap):
    """Retain Dev Panel's closer samples in tall sticky stages and overlap setting."""
    regions = page.evaluate('''() => [...document.querySelectorAll('*')].filter(el => {
      const p = el.parentElement;
      return p && getComputedStyle(el).position === 'sticky' &&
        el.getBoundingClientRect().height >= innerHeight * .4 && p.getBoundingClientRect().height >= innerHeight * 2;
    }).slice(0,20).map(el => {const r=el.parentElement.getBoundingClientRect();
      return {top:Math.max(0,Math.round(r.top+scrollY)),end:Math.round(r.bottom+scrollY-innerHeight)};})''')
    positions, y, bottom = [], 0, total - height
    for index in range(100):
        positions.append((index + 1, y))
        if y >= bottom:
            break
        next_y = y + max(1, round(height * (1 - overlap / 100)))
        for region in regions:
            if y < region['top'] < next_y:
                next_y = region['top']
            if region['top'] <= y < region['end']:
                next_y = min(next_y, y + round(height * .5), region['end'])
        y = min(bottom, max(y + 1, next_y))
    return positions


def run_capture(config):
    from playwright.sync_api import sync_playwright

    legacy = config.get('legacy', False)
    if not legacy:
        validate_request(config['url'], config.get('device', 'desktop'), config.get('tiles', ''), config.get('selector', ''))
    executable = config.get('chromeBinary') or os.environ.get('LINKO_PANEL_CHROME')
    with sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=executable or None, args=['--disable-dev-shm-usage']) as browser:
            if legacy:
                groups = []
                for device in config['devices']:
                    result = capture_page(browser, config, device)
                    groups.append({'device': device, 'images': result['tiles'],
                                   'truncated': result['tiles'][-1]['scroll_y'] < result['page_height'] - VIEWPORTS[device][1]})
                return {'groups': groups}
            return capture_page(browser, config, config.get('device', 'desktop'))


if __name__ == '__main__':
    try:
        print(json.dumps(run_capture(json.load(sys.stdin))))
    except Exception as error:
        print(f'Screenshot failed: {error}', file=sys.stderr)
        sys.exit(1)

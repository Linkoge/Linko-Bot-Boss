import hashlib
import re
import shutil
from pathlib import Path

from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_web import client  # noqa: F401, F811

from ai_manager import web
from ai_manager.config import Settings


def test_manifest_assets_and_cache_boundaries(client):  # noqa: F811
    browser, _, _ = client
    html = browser.get('/')
    assert html.headers['cache-control'] == 'no-store'
    assert 'href="/manifest.webmanifest"' in html.text
    manifest_response = browser.get('/manifest.webmanifest')
    assert manifest_response.headers['content-type'].startswith('application/manifest+json')
    assert manifest_response.headers['cache-control'] == 'no-store'
    manifest = manifest_response.json()
    assert manifest['id'] == manifest['start_url'] == manifest['scope'] == '/'
    assert manifest['display'] == 'standalone'
    assert manifest['theme_color'] == manifest['background_color'] == '#10141e'
    assert {icon['purpose'] for icon in manifest['icons']} == {'any', 'maskable'}
    for icon in manifest['icons']:
        response = browser.get(icon['src'])
        assert response.headers['content-type'] == 'image/png'
        assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
        width = int.from_bytes(response.content[16:20], 'big')
        height = int.from_bytes(response.content[20:24], 'big')
        assert icon['sizes'] == f'{width}x{height}'
    urls = re.findall(r'(?:src|href)="(/static/assets/[^\"]+)"', html.text)
    assert len(urls) == 10
    for url in urls:
        response = browser.get(url)
        _, _, _, digest, name = url.split('/', 4)
        assert hashlib.sha256(response.content).hexdigest() == digest
        assert response.content == browser.get('/static/' + name).content
        assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
        assert browser.get('/static/' + name).headers['cache-control'] == 'no-store'
        assert browser.get(url.replace(digest, '0' * 64)).status_code == 404
    assert browser.get('/static/index.html').headers['cache-control'] == 'no-store'
    assert browser.get('/api/auth-status').headers['cache-control'] == 'no-store'
    assert browser.get('/api/portfolio').status_code == 401
    assert browser.get('/static/assets/' + '0' * 64 + '/../web.py').status_code == 404


def test_next_release_has_new_url_and_existing_process_pins_bytes(tmp_path, monkeypatch):
    static = Path(web.__file__).parent / 'static'
    shutil.copytree(static, tmp_path / 'static')
    monkeypatch.setattr(web, '__file__', str(tmp_path / 'web.py'))
    projects = tmp_path / 'projects'
    projects.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(projects,), secure_cookie=False)
    app = web.create_app(settings, FakeServer(settings))
    with TestClient(app, base_url='http://localhost') as browser:
        old_html = browser.get('/').text
        old_url = re.search(r'src="([^"]+/app.js)"', old_html)[1]
        old_content = browser.get(old_url).content
        path = tmp_path / 'static/app.js'
        path.write_bytes(old_content + b'\n// next release fixture\n')
        assert browser.get(old_url).content == old_content
    next_app = web.create_app(settings, FakeServer(settings))
    with TestClient(next_app, base_url='http://localhost') as browser:
        new_html = browser.get('/').text
        new_url = re.search(r'src="([^"]+/app.js)"', new_html)[1]
        assert new_url != old_url
        assert browser.get(new_url).content == path.read_bytes()
        assert browser.get(old_url).status_code == 404
        assert browser.get('/').headers['cache-control'] == 'no-store'

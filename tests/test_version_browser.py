"""Real Chromium: the version line heads the project menu, machines show latest / behind / offline, Settings > Version."""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''
import sys
sys.path.insert(0, 'tests')
import uvicorn
from fake_codex import FakeServer
from ai_manager.config import Settings
from ai_manager.web import create_app
s = Settings()
uvicorn.run(create_app(s, FakeServer(s)), host='127.0.0.1', port=s.port, log_level='error')
'''


class Peer(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(self.server.answer).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_version_menu_machine_labels_and_settings(tmp_path):
    (tmp_path / 'projects').mkdir()
    port = free_port()
    env = {**os.environ, 'AI_MANAGER_DATA': str(tmp_path / 'data'), 'AI_MANAGER_PORT': str(port),
           'AI_MANAGER_PROJECT_ROOTS': str(tmp_path / 'projects'), 'AI_MANAGER_SECURE_COOKIE': '0',
           'AI_MANAGER_RESTRICTED': '0', 'AI_MANAGER_PROJECT': '', 'AI_MANAGER_MACHINE_ROLE': '', 'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([sys.executable, '-c', PROGRAM], env=env, cwd=ROOT)
    url, servers = f'http://127.0.0.1:{port}', []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/api/version', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail('Disposable server did not start')
        mine = httpx.get(url + '/api/version', timeout=5).json()
        for answer in (mine, {'version': '2026.01.01.1', 'commit': 'abcdef1', 'commit_count': mine['commit_count'] - 2}):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
            server.answer = answer
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        machines = [{'name': 'Twin', 'url': f'http://127.0.0.1:{servers[0].server_port}'},
                    {'name': 'Old', 'url': f'http://127.0.0.1:{servers[1].server_port}'},
                    {'name': 'Gone', 'url': f'http://127.0.0.1:{free_port()}'}]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            page.set_default_timeout(15000)
            page.goto(url)
            page.locator('#username').fill('owner')
            page.locator('#password').fill(secrets.token_urlsafe(16))
            page.locator('#setup-token').fill((tmp_path / 'data/setup-token').read_text())
            page.locator('#login-button').click()
            page.wait_for_function('()=>state.ready&&!!state.history')
            page.evaluate('m=>api("/api/machines",{method:"PUT",body:JSON.stringify({machines:m})})', machines)
            label = f"{mine['version']} ({mine['commit']})"
            page.locator('#project-menu-button').click()
            page.wait_for_function('()=>document.querySelectorAll("#project-menu .machine-version[data-version-state]").length===3')
            head = page.locator('#project-menu-content > :first-child')
            assert 'project-menu-version' in head.get_attribute('class')
            assert label in head.inner_text()
            page.wait_for_function('()=>!!document.querySelector("#project-menu [data-project=self-update]")')
            states = page.evaluate('()=>[...document.querySelectorAll("#project-menu .project-menu-machine")].map(r=>r.querySelector(".machine-version").textContent)')
            assert states == [f"{mine['version']} · latest", '2026.01.01.1 · behind by 2', 'offline']
            page.locator('#project-menu-close').click()
            page.locator('.tabs [data-view=settings]').click()
            page.wait_for_function('()=>!document.querySelector("#version-auto").hidden')
            assert page.locator('#version-label').inner_text() == label
            assert page.locator('#version-auto-toggle').is_checked()  # not Main: on by default
            page.locator('#version-auto-toggle').click()
            page.wait_for_function('()=>versionState.data&&versionState.data.enabled===false')
            assert httpx.get(url + '/api/version', timeout=5).json() == mine
            browser.close()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

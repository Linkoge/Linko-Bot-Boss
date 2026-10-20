"""Failed-merge downloads use immutable Git content and the normal owner chat/auth paths."""
import json
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fake_codex import FakeServer
from fastapi.testclient import TestClient
from test_attachments import docx_bytes
from test_core import run_next
from test_core import world as world_fixture
from test_web import sign_in

from ai_manager import pipeline as pipeline_module
from ai_manager.chat import Chat
from ai_manager.config import Settings
from ai_manager.pipeline import Conflict
from ai_manager.web import create_app

NAME = 'ანგარიში [საბოლოო].docx'
world = world_fixture


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test',
                           '-c', 'user.email=test@example.invalid', *args], check=True,
                          capture_output=True, text=True, timeout=60).stdout.strip()


@pytest.fixture
def delivery(tmp_path):
    root = tmp_path / 'projects' / 'documents'
    root.mkdir(parents=True)
    git(root, 'init', '-q', '-b', 'main')
    for name in ('unchanged.txt', 'edited.txt', 'deleted.txt'):
        (root / name).write_text('original\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'initial')
    settings = Settings(data_dir=tmp_path / 'data', project_roots=(root.parent,), secure_cookie=False)
    app = create_app(settings, FakeServer(settings))
    project = app.state.registry.add(root)
    conversation = app.state.chat.conversation(project['id'])['id']
    task = app.state.core.tasks.add(project['id'], 'Prepare finished documents', conversation)
    git(root, 'checkout', '-qb', 'task-result')
    files = {NAME: docx_bytes(), 'edited.txt': b'finished\r\n\x00\xff\n',
             'report.pdf': b'%PDF-1.7\n\x00\xff', 'unknown.custom-extension': b'\x00\x80'}
    for name, data in files.items():
        (root / name).write_bytes(data)
    (root / 'deleted.txt').unlink()
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'finished task')
    commit = git(root, 'rev-parse', 'HEAD')
    git(root, 'checkout', '-q', 'main')
    db = app.state.db
    db.run("UPDATE tasks SET state='done',workspace_branch='task-result',workspace=? WHERE id=?",
           (str(tmp_path / 'missing-workspace'), task['id']))
    with TestClient(app, base_url='http://localhost') as browser:
        yield SimpleNamespace(root=root, settings=settings, app=app, db=db, project=project, task=task,
                              conversation=conversation, commit=commit, files=files, browser=browser,
                              pipeline=app.state.core.pipeline,
                              deploy=(project['id'], 'auto', task['id'], conversation, 'finished documents'))


def message(delivery):
    rows = delivery.db.all('SELECT * FROM messages WHERE conversation_id=?', (delivery.conversation,))
    assert len(rows) == 1
    assert rows[0]['role'] == 'assistant' and rows[0]['status'] == 'completed'
    return rows[0]['content']


def links(text):
    return re.findall(r'\]\((/api/tasks/[^)]+)\)', text)


def snapshot(delivery):
    delivery.pipeline.report_merge_failure(delivery.root, delivery.commit, delivery.deploy, 'attempt-1')
    return {parse_qs(urlsplit(url).query)['path'][0]: url for url in links(message(delivery))}


@pytest.mark.parametrize('failure', ['untracked', 'conflict', 'other'])
def test_failed_merge_posts_once_per_attempt_and_preserves_error(delivery, monkeypatch, failure):
    d = delivery
    if failure == 'untracked':
        (d.root / NAME).write_bytes(b'owner document')
        error = ValueError
    elif failure == 'conflict':
        (d.root / 'edited.txt').write_text('owner edit\n')
        git(d.root, 'commit', '-qam', 'owner edit')
        error = Conflict
    else:
        def failed(*args):
            raise RuntimeError('any merge error')
        monkeypatch.setattr(pipeline_module, 'merge_into_main', failed)
        error = RuntimeError
    for _ in range(2):
        with pytest.raises(error):
            d.pipeline.merge(d.root, d.commit, d.deploy, attempt_id='attempt-1')
    text = message(d)
    assert set(parse_qs(urlsplit(url).query)['path'][0] for url in links(text)) == set(d.files)
    assert len(d.db.all("SELECT * FROM events WHERE kind='task.merge_files'")) == 1
    assert 'deleted.txt' not in text and 'unchanged.txt' not in text
    assert not d.pipeline.pending()
    assert d.app.state.chat.history(d.project['id'])['messages'][0]['content'] == text
    assert d.app.state.core.tasks.get(d.task['id'])['state'] == 'done'
    with pytest.raises(error):
        d.pipeline.merge(d.root, d.commit, d.deploy, attempt_id='attempt-2')
    assert len(d.db.all('SELECT * FROM messages')) == 2
    if failure == 'untracked':
        assert (d.root / NAME).read_bytes() == b'owner document'


@pytest.mark.smoke
def test_downloads_are_byte_exact_authenticated_and_commit_pinned(delivery):
    d = delivery
    urls = snapshot(d)
    for url in urls.values():
        assert d.browser.get(url).status_code == 401
    sign_in(d.browser, d.app, d.settings)
    # Neither the shared checkout, the branch tip nor the hidden workspace supplies the download bytes.
    git(d.root, 'checkout', '-q', 'task-result')
    for name in d.files:
        (d.root / name).write_bytes(b'later branch contents')
    git(d.root, 'commit', '-qam', 'later task edits')
    git(d.root, 'checkout', '-q', 'main')
    for name in d.files:
        (d.root / name).write_bytes(b'live workspace contents')
    git(d.root, 'commit', '-qam', 'later main edits')
    for name, data in d.files.items():
        response = d.browser.get(urls[name])
        assert response.status_code == 200
        assert response.content == data
        assert response.headers['content-disposition'] == "attachment; filename*=UTF-8''" + quote(name, safe='')
        assert response.headers['x-content-type-options'] == 'nosniff'
    assert d.browser.get(urls[NAME]).headers['content-type'] == (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert d.browser.get(urls['report.pdf']).headers['content-type'] == 'application/pdf'
    assert d.browser.get(urls['unknown.custom-extension']).headers['content-type'] == 'application/octet-stream'


def test_rejects_unchanged_deleted_unknown_and_traversal_paths(delivery):
    d = delivery
    urls = snapshot(d)
    sign_in(d.browser, d.app, d.settings)
    endpoint = urlsplit(urls[NAME]).path
    for path in ('unchanged.txt', 'deleted.txt', 'unknown.txt', '../edited.txt', '/edited.txt',
                 'folder/../../edited.txt', './edited.txt', 'folder/../edited.txt',
                 'folder\\..\\edited.txt', 'edited.txt\0', '.git/config', 'edited.txt/child'):
        assert d.browser.get(endpoint, params={'path': path}).status_code == 404, path
    assert d.browser.get(endpoint.replace(d.task['id'], 'unknown'), params={'path': NAME}).status_code == 404
    assert d.browser.get(f"/api/tasks/{d.task['id']}/files/999999", params={'path': NAME}).status_code == 404
    assert d.browser.get(endpoint, params={'path': '%2e%2e%2f' + NAME}).status_code == 404


def test_list_is_capped_but_other_changed_files_remain_downloadable(delivery):
    d = delivery
    git(d.root, 'checkout', '-q', 'task-result')
    for index in range(25):
        (d.root / f'extra-{index:02}.txt').write_text(str(index))
    git(d.root, 'add', '-A')
    git(d.root, 'commit', '-qm', 'many documents')
    d.commit = git(d.root, 'rev-parse', 'HEAD')
    git(d.root, 'checkout', '-q', 'main')
    urls = snapshot(d)
    assert len(urls) == 20
    assert '+9 more files.' in message(d)
    sign_in(d.browser, d.app, d.settings)
    endpoint = urlsplit(next(iter(urls.values()))).path
    assert d.browser.get(endpoint, params={'path': NAME}).content == d.files[NAME]


def test_git_symlink_content_never_reads_the_target(delivery):
    d = delivery
    outside = d.root.parent / 'private.txt'
    outside.write_text('private owner content')
    git(d.root, 'checkout', '-q', 'task-result')
    (d.root / 'link.txt').symlink_to(outside)
    git(d.root, 'add', '-A')
    git(d.root, 'commit', '-qm', 'symlink')
    d.commit = git(d.root, 'rev-parse', 'HEAD')
    git(d.root, 'checkout', '-q', 'main')
    urls = snapshot(d)
    sign_in(d.browser, d.app, d.settings)
    response = d.browser.get(urls['link.txt'])
    assert response.content == str(outside).encode()
    assert b'private owner content' not in response.content


def test_missing_git_object_returns_not_found(delivery):
    d = delivery
    urls = snapshot(d)
    sign_in(d.browser, d.app, d.settings)
    event = d.db.one("SELECT * FROM events WHERE kind='task.merge_files'")
    saved = json.loads(event['payload'])
    saved['commit'] = '0' * 40
    d.db.run('UPDATE events SET payload=? WHERE seq=?', (json.dumps(saved), event['seq']))
    assert d.browser.get(urls[NAME]).status_code == 404


@pytest.mark.asyncio
async def test_worker_delivery_publishes_downloads_on_untracked_collision(world):
    world.deployable(False)
    chat = Chat(world.db, world.registry, FakeServer(world.settings))
    conversation = chat.conversation(world.project['id'])['id']
    task = world.tasks.add(world.project['id'], 'Prepare document', conversation)

    async def finish(root, prompt):
        text = await world.lab.default(root, prompt)
        (world.app / 'change-1.txt').write_text('owner document')
        return text

    world.lab.actions = [finish]
    done = await run_next(world)
    assert done['id'] == task['id'] and done['state'] == 'failed'
    assert done['rounds'] == 0 and 'change-1.txt' in done['blocker']
    rows = chat.history(world.project['id'])['messages']
    assert len(rows) == 1 and len(links(rows[0]['content'])) == 1
    assert 'change-1.txt' in rows[0]['content']
    assert (world.app / 'change-1.txt').read_text() == 'owner document'

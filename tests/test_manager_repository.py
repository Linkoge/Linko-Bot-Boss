"""Managers inspect real project code through a read-only capability on either provider."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_codex import FakeServer
from test_minimal_flow import NativeFixture
from test_minimal_flow import system as system_fixture

from ai_manager.codex import MANAGER_INSTRUCTIONS, AppServer
from ai_manager.core import Core, Tasks
from ai_manager.orchestration import MANAGER_TOOL, Scheduler
from ai_manager.pipeline import Pipeline
from ai_manager.repository import inspect_repository

system = system_fixture


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize('global_chat', [False, True])
async def test_non_manager_project_chat_has_real_path_reads_and_searches_without_a_worker(system, global_chat):
    db, registry, chat, settings = system
    project, other = registry.list()
    root = Path(project['path'])
    (root / 'shop.py').write_text('def checkout():\n    return "shop-specific behavior"\n')
    before = (root / 'shop.py').read_bytes()
    workers = Core(db, registry, settings, Pipeline(db, registry), NativeFixture)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    calls, evidence = [], []

    class InspectingServer(FakeServer):
        async def thread(self, *args, **kwargs):
            # Exercise the real production thread request, including sandbox and feature restrictions.
            return await AppServer.thread(self, *args, **kwargs)

        async def request(self, method, params, timeout=30):
            if method in {'thread/start', 'thread/resume'}:
                calls.append((method, params))
                return {'thread': {'id': params.get('threadId', 'project-reader')}}
            if method == 'turn/start':
                prompt = json.loads(params['input'][0]['text'])
                if not self.prompts:
                    if global_chat:
                        assert {p['id']: p['path'] for p in prompt['context']['projects']}[project['id']] == str(root)
                    else:
                        assert prompt['context']['project']['path'] == str(root)
                handler = self.tool_handlers[params['threadId']]

                async def action(name, **args):
                    return await handler({'tool': 'manager_action', 'callId': name,
                                          'arguments': {'action': name, 'project_id': project['id'], **args}})

                assert 'shop.py' in (await action('list_files'))['entries']
                found = await action('search_code', query='shop-specific')
                assert found['matches'] == [{'path': 'shop.py', 'line': 2,
                                              'text': '    return "shop-specific behavior"'}]
                evidence.append((await action('read_document', path=str(root / 'shop.py')))['content'])
                for mutation in ('write_file', 'edit_file', 'apply_patch', 'exec', 'commit'):
                    with pytest.raises(ValueError, match='Unsupported action'):
                        await action(mutation, path='shop.py', objective='Overwrite it')
                if not global_chat:
                    with pytest.raises(ValueError, match='cross-project'):
                        await action('read_document', project_id=other['id'], path='shop.py')
            return await super().request(method, params, timeout)

    chat.server = InspectingServer(settings)
    try:
        for message in ('Explain the checkout implementation', 'What does it return?'):
            receipt = await chat.submit(None if global_chat else project['id'], message)
            await asyncio.wait_for(asyncio.gather(*list(chat.jobs.values())), 5)
            execution = db.one('SELECT status,error FROM executions WHERE id=?', (receipt['execution_id'],))
            assert execution['status'] == 'completed', execution
        assert [method for method, _ in calls] == ['thread/start', 'thread/resume']
        for _, params in calls:
            assert params['cwd'] == str(chat.workspace if global_chat else root)
            assert params['sandbox'] == 'read-only' and params['approvalPolicy'] == 'never'
            for feature in ('shell_tool', 'unified_exec', 'apply_patch_freeform', 'code_mode', 'apps', 'plugins'):
                assert params['config']['features.' + feature] is False
            assert params['developerInstructions'] == MANAGER_INSTRUCTIONS
        assert calls[0][1]['dynamicTools'] == [MANAGER_TOOL]
        assert evidence == [before.decode()] * 2
        assert (root / 'shop.py').read_bytes() == before
        assert sorted(p.name for p in root.iterdir()) == ['shop.py']
        assert Tasks(db).list() == []
        assert {p['id']: p['path'] for p in chat.context(None)['projects']} == {
            p['id']: p['path'] for p in (project, other)}
    finally:
        await chat.close()
        await workers.close()


@pytest.mark.smoke
@pytest.mark.parametrize('action', ['read_document', 'list_files', 'search_code'])
def test_repository_reads_cannot_escape_through_absolute_paths_traversal_or_symlinks(tmp_path, action):
    root = tmp_path / 'repo'
    root.mkdir()
    outside = tmp_path / 'private.py'
    outside.write_text('private')
    (root / 'linked.py').symlink_to(outside)
    (root / 'linked-dir').symlink_to(tmp_path, target_is_directory=True)
    for path in ('../private.py', str(outside), 'linked.py', 'linked-dir/private.py'):
        with pytest.raises(ValueError, match='inside the selected project'):
            inspect_repository(root, action, path, 'private')
    assert inspect_repository(root, 'search_code', query='private')['matches'] == []
    assert outside.read_text() == 'private'
    with pytest.raises(ValueError, match='Unsupported repository read action'):
        inspect_repository(root, 'write_file', 'new.py')
    assert not (root / 'new.py').exists()


def test_repository_pages_large_source_and_search_results_without_running_code(tmp_path):
    root = tmp_path
    content = 'matching code\n' * 10000
    (root / 'large.py').write_text(content)
    for name in ('.git', 'node_modules', '.venv'):
        (root / name).mkdir()
        (root / name / 'generated.py').write_text('matching code')
    (root / 'binary').write_bytes(b'\0matching code')
    (root / 'bad.py').write_text('raise AssertionError("Never execute project code")')
    for index in range(55):
        (root / f'file-{index:02}.py').write_text('ordinary file')
    files = inspect_repository(root, 'list_files')
    assert len(files['entries']) == 50 and files['next_offset'] == 50
    assert inspect_repository(root, 'list_files', offset=50)['next_offset'] is None
    rebuilt, offset = '', 0
    while True:
        page = inspect_repository(root, 'read_document', 'large.py', offset=offset)
        rebuilt += page['content']
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert rebuilt == content  # The old 100KB document cap prevented reading actual large source files.
    first = inspect_repository(root, 'search_code', query='matching code')
    second = inspect_repository(root, 'search_code', query='matching code', offset=first['next_offset'])
    assert len(first['matches']) == 50 and first['matches'][0]['line'] == 1
    assert second['matches'][0]['line'] == 51
    assert {m['path'] for m in first['matches'] + second['matches']} == {'large.py'}
    assert inspect_repository(root, 'search_code', query='MATCHING')['matches'] == []
    assert inspect_repository(root, 'search_code', 'bad.py', query='Never execute')['matches'][0]['line'] == 1
    with pytest.raises(ValueError, match='nonempty'):
        inspect_repository(root, 'search_code')


def test_repository_search_deadline_is_explicit_and_recoverable(tmp_path, monkeypatch):
    (tmp_path / 'source.py').write_text('code')
    clock = iter([0, 6])
    monkeypatch.setattr('ai_manager.repository.time.monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='narrow path'):
        inspect_repository(tmp_path, 'search_code', query='code')

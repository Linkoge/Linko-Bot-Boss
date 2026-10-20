"""The README advice reaches existing manager chats without adding a workflow gate."""
import asyncio
import hashlib
import json
import re
from pathlib import Path

import pytest
from test_model_settings import SOL, SONNET, CatalogServer, current, save
from test_model_settings import system as system_fixture

from ai_manager.codex import ARCHITECTURE_GUIDE, MANAGER_INSTRUCTIONS, RULES, WORKER_RULES
from ai_manager.core import WORKER_INSTRUCTIONS, Tasks
from ai_manager.orchestration import MANAGER_TOOL

system = system_fixture


@pytest.mark.smoke
def test_rule_sections_have_one_source_and_manager_retains_all_content():
    source = (Path(__file__).resolve().parents[1] / 'RULES.md').read_text()
    before, rest = source.split('<!-- manager-only:start -->')
    manager_only, after = rest.split('<!-- manager-only:end -->')
    assert RULES == before + manager_only + after
    assert MANAGER_INSTRUCTIONS.startswith(RULES)
    assert WORKER_RULES == before + after
    assert WORKER_INSTRUCTIONS.startswith(WORKER_RULES)
    for paragraph in (manager_only.split('\n\n')):
        if paragraph.strip():
            assert MANAGER_INSTRUCTIONS.count(paragraph) == 1
            assert paragraph not in WORKER_INSTRUCTIONS
    assert 'Managers read/search' in manager_only
    assert "8. The owner's host agent" in manager_only
    assert '9. A restricted work instance' in manager_only
    for number in (*range(1, 8), 10):
        rule = re.search(rf'(?m)^{number}\. .*', source).group()
        assert rule in WORKER_RULES


@pytest.mark.smoke
def test_manager_guide_is_the_single_concise_readme_section():
    readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text()
    section = re.search(r'(?ms)^## Architecture design principles\n.*?(?=^## |\Z)', readme).group().strip()
    assert ARCHITECTURE_GUIDE == section
    assert MANAGER_INSTRUCTIONS.count(section) == 1
    assert '## GitHub backups and secret checks' not in MANAGER_INSTRUCTIONS
    assert len(section.split()) <= 550
    for advice in ('advisory only', 'Advice never blocks', 'requires no approval or review receipt',
                   'adds no model or routing restrictions', 'concrete incident', 'shown = saved = dispatched',
                   'No feature should stall', 'delete the old path in the same change', 'one-line before/after'):
        assert advice in section


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize('manager,project_chat,automatic', [(SOL, False, False), (SONNET, True, True)])
async def test_guide_refreshes_existing_manager_threads_and_delegation_stays_ungated(
    system, monkeypatch, manager, project_chat, automatic,
):
    db, registry, _, server, chat, workers, _ = system
    project = registry.list()[0]['id']
    scope = project if project_chat else None
    worker = {'model': SONNET if manager == SOL else SOL, 'effort': 'high'}
    save(db, server, {**current(db, server), 'manager': {'model': manager, 'effort': 'low'},
                      'worker': None if automatic else worker})
    conversation = chat.conversation(scope)
    db.run('UPDATE conversations SET thread_id=? WHERE id=?', ('old-thread', conversation['id']))
    previous = MANAGER_INSTRUCTIONS.removesuffix('\n' + ARCHITECTURE_GUIDE)
    db.set_setting('manager_tool_schema:' + conversation['id'],
                   hashlib.sha256((previous + json.dumps(MANAGER_TOOL, sort_keys=True)).encode()).hexdigest())
    calls = []
    delegated = []
    original = server.thread
    original_request = server.request

    async def capture(cwd, instructions, thread_id=None, model=None, dynamic_tools=None):
        calls.append((instructions, thread_id, model, dynamic_tools))
        return await original(cwd, instructions, thread_id, model, dynamic_tools)

    async def request(method, params, timeout=30):
        if method == 'turn/start' and 'Proceed with' in params['input'][0]['text']:
            # Simulate a native tool call during the active manager turn, with no review/approval receipt.
            delegated.append(await server.tool_handlers[params['threadId']]({
                'tool': 'manager_action', 'callId': 'small-replacement',
                'arguments': {'action': 'delegate', 'project_id': project, 'objective': 'Replace the worker selector',
                              'model': worker['model'], 'effort': worker['effort']},
            }))
        return await original_request(method, params, timeout)

    monkeypatch.setattr(server, 'thread', capture)
    monkeypatch.setattr(server, 'request', request)
    refreshed = None
    try:
        for content in ('Evaluate adding a second worker selector', 'Proceed with the small replacement'):
            receipt = await chat.submit(scope, content)
            await asyncio.wait_for(asyncio.gather(*chat.jobs.values()), timeout=5)
            result = db.one('SELECT status,thread_id FROM executions WHERE id=?', (receipt['execution_id'],))
            assert result['status'] == 'completed'
            assert calls[-1] == (MANAGER_INSTRUCTIONS, refreshed, manager, [MANAGER_TOOL])
            refreshed = result['thread_id']
        assert refreshed != 'old-thread'  # Advice reaches an old chat; subsequent turns retain continuity.

        # Cross-provider choices still enter and run in the ordinary queue.
        assert len(delegated) == 1
        task = delegated[0]
        assert task['status'] == 'queued' and task['worker_route'] == worker
        claimed = Tasks(db).claim()
        assert claimed['id'] == task['id']
        await workers._run(claimed)
        assert Tasks(db).get(task['id'])['state'] == 'done'
        assert {key: CatalogServer.turns[-1][key] for key in ('model', 'effort')} == worker
    finally:
        await workers.close()
        await chat.close()

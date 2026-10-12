"""Real native controls on a disposable long command; no production assignments or deployment."""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from ai_manager.chat import Chat
from ai_manager.codex import AppServer, process_start
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(.02)
    raise AssertionError('Timed out waiting for disposable acceptance state')


async def main():
    evidence = Path(os.environ.get('LINKO_AGENT_EVIDENCE', '/tmp/linko-agent-controls-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    receipts = []
    with tempfile.TemporaryDirectory(prefix='linko-native-controls-') as temporary:
        root = Path(temporary)
        projects = root / 'projects'
        source = projects / 'Control Fixture'
        source.mkdir(parents=True)
        (source / 'heartbeat.py').write_text("import os,time\nfrom pathlib import Path\nPath('command.pid').write_text(str(os.getpid()))\nPath('partial.txt').write_text('retained partial progress')\nfor i in range(3000):\n Path('heartbeat.txt').write_text(str(i))\n time.sleep(.02)\n")
        settings = Settings(data_dir=root/'data', project_roots=(projects,))
        settings.prepare()
        db = Database(settings.db_path)
        registry = Registry(db, (projects,))
        project = registry.add(source)
        workers = Workers(db, registry, settings)
        chat = Chat(db, registry, AppServer(settings))
        scheduler = Scheduler(db, registry, settings, workers, chat)
        origin = chat.conversation(project['id'])['id']
        try:
            for command in ('pause', 'cancel'):
                for name in ('command.pid', 'heartbeat.txt', 'partial.txt', 'resumed.txt'):
                    path = source/name
                    if path.exists():
                        path.unlink()
                task = Tasks(db).add(project['id'],
                    f'Disposable {command} lifecycle acceptance. On this initial turn, run python3 heartbeat.py in this repository and wait for that command. Do no other work. The test controller will interrupt you. If subsequently resumed, inspect partial.txt and do not run heartbeat.py again; preserve it and finish only added instructions. No commit, deployment, publishing or external changes.', origin,
                    acceptance='Preserve partial.txt and never repeat the interrupted command on resume.')
                claim = Tasks(db).claim(1)
                workers.launch(claim)
                await wait_until(lambda: (source/'command.pid').exists() and (source/'partial.txt').exists(), 120)
                pid = int((source/'command.pid').read_text())
                identity = process_start(pid)
                assert identity
                before = (source/'partial.txt').read_text()
                task = Tasks(db).get(task['id'])
                started = time.monotonic()
                requested = await workers.control(task['id'], command, task['revision'])
                request_seconds = time.monotonic()-started
                assert requested['status'] == command+'_requested'
                await wait_until(lambda: Tasks(db).get(task['id'])['lease_owner'] is None, 9)
                seconds = time.monotonic()-started
                assert process_start(pid) != identity, 'interrupted command still alive'
                assert (source/'partial.txt').read_text() == before
                confirmed = Tasks(db).get(task['id'])
                assert confirmed['status'] == ('paused' if command == 'pause' else 'cancelled')
                events = db.all("SELECT kind,payload,created_at FROM events WHERE execution_id=? AND kind IN ('worker.control.checkpoint','worker.control.forced','worker.finished') ORDER BY seq", (claim['lease_owner'],))
                receipt = {'command': command, 'request_seconds': request_seconds, 'confirmed_seconds': seconds,
                    'command_terminated': True, 'partial_preserved': True, 'events': events}
                if command == 'pause':
                    call = {'tool': 'manager_action', 'arguments': {'action': 'continue', 'task_id': task['id'],
                        'objective': 'Write resumed.txt containing bundled resume verified and finish; do not run heartbeat.py again.'},
                        'manager_execution_id': 'fixture-bundle', 'conversation_id': origin, 'callId': 'fixture-bundle', 'mode': 'auto'}
                    await scheduler.manager_action(project['id'], call)
                    workers.launch(Tasks(db).claim(1))
                    await wait_until(lambda: Tasks(db).get(task['id'])['lease_owner'] is None, 120)
                    assert Tasks(db).get(task['id'])['status'] == 'completed'
                    assert (source/'partial.txt').read_text() == before
                    assert (source/'command.pid').read_text() == str(pid)
                    assert 'bundled resume verified' in (source/'resumed.txt').read_text()
                    assert len(db.all('SELECT * FROM tasks')) == 1
                    assert len(db.all('SELECT * FROM executions WHERE task_id=?', (task['id'],))) == 2
                    receipt['same_assignment_native_resume'] = True
                receipts.append(receipt)
        finally:
            await workers.close()
            await chat.close()
    (evidence/'native-controls.json').write_text(json.dumps(receipts, indent=2))
    print(json.dumps(receipts, indent=2))


if __name__ == '__main__':
    asyncio.run(main())

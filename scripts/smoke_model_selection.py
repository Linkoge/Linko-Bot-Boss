"""Opt-in real provider acceptance, disposable project and durable isolated data."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from ai_manager.chat import Chat
from ai_manager.codex import AppServer
from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.model_settings import MODELS, current, save
from ai_manager.orchestration import Scheduler
from ai_manager.registry import Registry
from ai_manager.tasks import Tasks
from ai_manager.workers import Workers


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence / 'native-fixture'
    source = root / 'projects' / 'selection-smoke'
    source.mkdir(parents=True)  # Never overwrite a previous acceptance run.
    settings = Settings(data_dir=root / 'data', project_roots=(source.parent,))
    settings.prepare()
    db = Database(settings.db_path)
    registry = Registry(db, settings.project_roots)
    project = registry.add(source)
    server = AppServer(settings)
    chat = Chat(db, registry, server)
    workers = Workers(db, registry, settings)
    scheduler = Scheduler(db, registry, settings, workers, chat)
    chat.action_handler = scheduler.manager_action
    try:
        await server.start()
        (args.evidence / 'provider-catalog.json').write_text(json.dumps({
            'account_type': server.account_type, 'catalog': server.catalog}, indent=2))
        config = current(db, server)
        config['manager'] = {'model': MODELS['Sol'], 'effort': 'low'}
        config['bot']['manual'] = {'model': MODELS['Sol'], 'effort': 'medium'}
        config['bot']['mode'] = 'automatic'
        for model, item in config['bot']['policy'].items():
            item['allowed'] = model == MODELS['Luna']
            item['levels'] = {e: e == 'high' for e in ('low', 'medium', 'high')}
        save(db, server, config)
        scheduler.start()
        await chat.submit(project['id'], 'Delegate this simple well-defined objective now: do not edit files or run commands; '
                          'reply with exactly MODEL_SELECTION_OK and finish. Choose the permitted bot pair. '
                          'Do not deploy anything. After the result acknowledge it briefly.', mode='execute')
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = db.one("SELECT * FROM executions WHERE task_id IS NOT NULL AND delivery='delivered'")
            if result and not db.one('SELECT id FROM executions WHERE finished_at IS NULL'):
                assert result['status'] == 'completed', result['error']
                assert (result['model'], result['effort']) == (MODELS['Luna'], 'high')
                assert 'MODEL_SELECTION_OK' in result['output']
                tasks = Tasks(db).list(project['id'])
                assert len(tasks) == 1
                assert Tasks(db).get(tasks[0]['id'])['worker_route']['model'] == MODELS['Luna']
                manager = db.all('SELECT id,model,effort,status,thread_id,turn_id FROM executions WHERE task_id IS NULL')
                assert all((row['model'], row['effort']) == (MODELS['Sol'], 'low') for row in manager)
                evidence = {'worker': {k: result[k] for k in ('id', 'task_id', 'model', 'effort', 'status', 'thread_id', 'turn_id', 'delivery', 'output')},
                            'manager': manager, 'settings': Database(db.path).setting('model_settings'),
                            'selection_event': db.one("SELECT * FROM events WHERE kind='task.model_selected'"),
                            'database': str(db.path), 'deployment': 'not requested; not performed'}
                (args.evidence / 'native-selection.json').write_text(json.dumps(evidence, indent=2))
                print('Real manager Sol/Low → selected permitted Luna/High → worker → manager delivery passed')
                return
            failure = db.one("SELECT error FROM executions WHERE status='failed'")
            if failure:
                raise AssertionError(failure)
            await asyncio.sleep(.3)
        raise AssertionError('Native selection acceptance timed out; isolated database retained')
    finally:
        await scheduler.close()
        await workers.close()
        await chat.close()


if __name__ == '__main__':
    asyncio.run(main())

"""Automatic deploy setup: Create deploy setup task, seeding from .linko-deploy.json, never overwriting, none:true."""
import json
from pathlib import Path

import pytest
from test_core import git, run_next
from test_core import world as world_fixture

from ai_manager.deploy_auto import OBJECTIVE, SETUP_FILE, TITLE, read_proposal
from ai_manager.deploy_setup import validate_setup

world = world_fixture
SETUP = {'deploy_command': ['scripts/deploy.sh'], 'backup_command': ['scripts/backup.sh'],
         'health_command': ['scripts/health.sh'], 'rollback_command': ['scripts/rollback.sh']}


def writes(value):
    """A worker turn that commits .linko-deploy.json (raw text when a string) plus one ordinary change."""
    async def action(root, prompt):
        (Path(root) / SETUP_FILE).write_text(value if isinstance(value, str) else json.dumps(value))
        (Path(root) / 'scripts.txt').write_text('scripts\n')
        git(root, 'add', '-A')
        git(root, 'commit', '-qm', 'deploy setup')
        return 'Done.'
    return action


def undeployed(world):
    """A project with no saved setup and no earlier verdict, as every new project starts."""
    world.deployable(False)
    world.db.set_setting('deploy_none:' + world.project['id'], None)
    assert world.pipeline.setup(world.project['id']) is None


def setup_tasks(world, *states):
    return world.db.all("SELECT * FROM tasks WHERE substr(description,1,?)=? AND state IN (%s)"
                        % ','.join('?' * len(states or ('queued', 'running'))),
                        (len(TITLE), TITLE, *(states or ('queued', 'running'))))


@pytest.mark.asyncio
async def test_merge_without_setup_queues_exactly_one_create_deploy_setup_task(world):
    undeployed(world)
    project = world.project['id']
    world.tasks.add(project, 'first change')
    done = await run_next(world)
    assert done['merge']['deploy'] is False
    assert done['merge']['deployment'] == 'no deploy setup; Create deploy setup queued'
    (queued,) = setup_tasks(world)
    assert queued['project_id'] == project and queued['description'] == OBJECTIVE
    assert 'argv' in OBJECTIVE and SETUP_FILE in OBJECTIVE and 'fake systemctl' in OBJECTIVE
    # Deduplicated while queued/running: a second merge in the project adds no second task.
    world.tasks.control(queued['id'], 'hold')
    world.tasks.add(project, 'second change')
    again = await run_next(world)
    assert again['merge']['deployment'] == 'no deploy setup; Create deploy setup already queued'
    assert len(setup_tasks(world)) == 1
    assert world.pipeline.summary(project).startswith('no deploy setup; a Create deploy setup task')


@pytest.mark.asyncio
async def test_create_task_without_a_usable_file_does_not_loop(world):
    undeployed(world)
    world.tasks.add(world.project['id'], 'first change')
    await run_next(world)
    done = await run_next(world)  # the Create deploy setup task itself; its worker wrote no file
    assert done['description'].startswith(TITLE) and done['state'] == 'done'
    assert 'no further task is queued' in done['merge']['deployment']
    assert world.db.all("SELECT 1 FROM tasks WHERE state IN ('queued','running')") == []


@pytest.mark.asyncio
async def test_valid_file_in_a_merge_is_saved_once_and_requests_the_normal_deploy(world):
    undeployed(world)
    project = world.project['id']
    world.lab.actions.append(writes(SETUP))
    world.tasks.add(project, 'create deploy setup', reuse_objective=False)
    done = await run_next(world)
    assert world.pipeline.setup(project) == validate_setup(SETUP)
    assert done['merge']['deploy'] is True and done['merge']['deployment'].startswith('deploy setup seeded from ')
    request = json.loads((world.pipeline.root / 'request.json').read_text())
    assert request['project_id'] == project and request['source'] == 'auto' and request['setup'] == SETUP
    assert setup_tasks(world) == []
    assert (world.app / SETUP_FILE).is_file()
    # The file seeds once: after the owner removes the setup the same file does not bring it back.
    world.pipeline.cancel()
    world.pipeline.save_setup(project, None)
    world.tasks.add(project, 'later change')
    later = await run_next(world)
    assert world.pipeline.setup(project) is None and not world.pipeline.pending()
    assert later['merge']['deploy'] is False and 'removed after it was seeded' in later['merge']['deployment']
    assert setup_tasks(world) == []


@pytest.mark.asyncio
async def test_repo_file_never_overwrites_a_saved_setup(world):
    saved = world.pipeline.setup(world.project['id'])
    assert saved
    world.lab.actions.append(writes(SETUP))
    world.tasks.add(world.project['id'], 'change the file')
    done = await run_next(world)
    assert world.pipeline.setup(world.project['id']) == saved != SETUP
    assert done['state'] == 'done' and done['merge']['state'] == 'merged' and 'seeded' not in done['merge']['deployment']
    assert setup_tasks(world) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [
    '{not json', json.dumps({k: v for k, v in SETUP.items() if k != 'rollback_command'}),
    json.dumps({**SETUP, 'extra': ['x']}), json.dumps({'none': 'yes'}), json.dumps([1])])
async def test_invalid_file_is_reported_and_creates_no_task_or_setup(world, content):
    undeployed(world)
    world.lab.actions.append(writes(content))
    world.tasks.add(world.project['id'], 'bad file')
    done = await run_next(world)
    assert done['state'] == 'done' and done['merge']['state'] == 'merged'
    assert done['merge']['deployment'].startswith('invalid ' + SETUP_FILE + ': ')
    assert world.pipeline.setup(world.project['id']) is None and not world.pipeline.pending()
    assert world.db.all("SELECT 1 FROM tasks WHERE state IN ('queued','running')") == []


@pytest.mark.asyncio
async def test_none_true_records_not_deployable_and_stops_auto_queuing(world):
    undeployed(world)
    project = world.project['id']
    world.lab.actions.append(writes({'none': True, 'reason': 'docs-only repository'}))
    world.tasks.add(project, 'declare')
    done = await run_next(world)
    assert done['merge']['deployment'] == 'not deployable: docs-only repository' and done['merge']['deploy'] is False
    assert world.pipeline.summary(project) == 'not deployable: docs-only repository'
    assert world.pipeline.status(project)['deployment'] == 'not deployable: docs-only repository'
    world.tasks.add(project, 'more work')
    more = await run_next(world)
    assert more['merge']['deployment'] == 'not deployable: docs-only repository'
    assert world.db.all("SELECT 1 FROM tasks WHERE state IN ('queued','running')") == []
    assert world.pipeline.setup(project) is None


def test_read_proposal_states(tmp_path):
    assert read_proposal(tmp_path)[0] == 'absent'
    (tmp_path / SETUP_FILE).write_text(json.dumps({'none': True}))
    assert read_proposal(tmp_path)[:2] == ('none', 'no reason given')
    (tmp_path / SETUP_FILE).write_text(json.dumps({'service': 'app.service', 'backup_command': ['b'],
                                                   'health_url': 'http://127.0.0.1:1/h', 'rollback_command': ['r']}))
    state, value, digest = read_proposal(tmp_path)
    assert state == 'setup' and value['service'] == 'app.service' and digest
    (tmp_path / SETUP_FILE).write_text('x' * 30000)
    assert read_proposal(tmp_path)[0] == 'invalid'

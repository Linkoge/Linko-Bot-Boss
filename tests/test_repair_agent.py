"""Host agent status (idle/running/waiting/stuck/login expired), compact manager summary, launcher heartbeat and UI line."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import json
import os
import sys
from pathlib import Path

from ai_manager.repair_agent import AUTH_MESSAGE, agent_status, compact_summary, looks_expired
from test_model_picker_browser import browser_context, page, server  # noqa: F401

NOW = 1_000_000.0
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build(tmp_path, rows=(), beat=NOW - 30, installed=True, log_age=None):
    root = tmp_path / 'repair'
    (root / 'requests').mkdir(parents=True)
    if installed:
        (root / 'installed.json').write_text(json.dumps({'repo': 'x'}))
    if beat is not None:
        (root / 'heartbeat.json').write_text(json.dumps({'at': beat}))
    for n, row in enumerate(rows):
        row = {'id': f'r{n}', 'status': 'running', 'attempts': 1, 'created_at': NOW - 5000 + n, 'updated_at': NOW - 100,
               'instructions': f'job {n}\nsecond line', **row}
        (root / 'requests' / f"{row['id']}.json").write_text(json.dumps(row))
        if log_age is not None and row['status'] == 'running':
            log = root / 'runs' / row['id'] / '1.jsonl'
            log.parent.mkdir(parents=True)
            log.write_text('{}\n')
            os.utime(log, (NOW - log_age, NOW - log_age))
    return tmp_path


def test_not_installed_and_idle(tmp_path):
    assert agent_status(tmp_path, NOW)['state'] == 'not_installed'
    assert agent_status(build(tmp_path / 'a', installed=False), NOW)['installed'] is False
    status = agent_status(build(tmp_path / 'b'), NOW)
    assert status['state'] == 'idle' and status['job'] is None and status['heartbeat'] == NOW - 30


def test_running_job_with_heartbeat(tmp_path):
    status = agent_status(build(tmp_path, [{'started_at': NOW - 300}], log_age=20), NOW)
    assert status['state'] == 'running' and status['job']['title'] == 'job 0 second line'
    assert status['started_at'] == NOW - 300 and status['heartbeat'] == NOW - 20 and status['last_error'] == ''


def test_stuck_when_log_silent_or_launcher_not_ticking(tmp_path):
    silent = agent_status(build(tmp_path / 'a', [{'updated_at': NOW - 3000}], log_age=3000), NOW)
    assert silent['state'] == 'stuck'
    dead = agent_status(build(tmp_path / 'b', [{}], beat=NOW - 4000, log_age=10), NOW)
    assert dead['state'] == 'stuck' and 'not ticked' in dead['last_error']
    never = agent_status(build(tmp_path / 'c', [{'status': 'pending', 'attempts': 0}], beat=None), NOW)
    assert never['state'] == 'stuck'


def test_waiting_retry_shows_error(tmp_path):
    status = agent_status(build(tmp_path, [{'status': 'waiting', 'error': 'connection reset'}]), NOW)
    assert status['state'] == 'waiting' and status['last_error'] == 'connection reset'


def test_expired_login_detected_from_flag_and_text(tmp_path):
    flagged = agent_status(build(tmp_path / 'a', [{'status': 'failed', 'auth_expired': True, 'error': 'x'}]), NOW)
    assert flagged['state'] == 'login_expired' and flagged['auth_expired'] and flagged['last_error'] == AUTH_MESSAGE
    text = agent_status(build(tmp_path / 'b', [{'status': 'failed', 'error': 'OAuth token has expired. Please run /login'}]), NOW)
    assert text['state'] == 'login_expired'
    assert looks_expired('API Error: 401 {"type":"authentication_error"}') and not looks_expired('disk full')
    other = agent_status(build(tmp_path / 'c', [{'status': 'failed', 'error': 'disk full'}]), NOW)
    assert other['state'] == 'idle' and other['last_error'] == 'disk full'


def test_compact_summary_is_bounded(tmp_path):
    rows = [{'status': 'succeeded' if n % 2 else 'failed', 'result': 'R' * 20000, 'error': 'E' * 20000,
             'instructions': 'T' * 20000} for n in range(40)]
    rows.append({'status': 'running', 'instructions': 'now' * 5000, 'error': 'e' * 9000, 'attempts': 2})
    summary = compact_summary(build(tmp_path, rows, log_age=5), NOW)
    assert len(json.dumps(summary)) < 3000
    assert len(summary['last_jobs']) == 5 and summary['state'] == 'running' and summary['current_job']['attempt'] == 2
    assert set(summary) == {'state', 'current_job', 'started_at', 'last_heartbeat', 'last_error', 'last_jobs'}


def test_launcher_writes_heartbeat_and_flags_expired_login(tmp_path):
    from scripts.emergency_repair import Repair
    repair = Repair(tmp_path, {'repo': str(tmp_path), 'provider': 'claude'})
    repair.run()
    assert json.loads((tmp_path / 'repair/heartbeat.json').read_text())['at'] > 0
    log = tmp_path / 'a.jsonl'
    log.write_text(json.dumps({'type': 'result', 'is_error': True, 'result': 'OAuth token has expired. Please run /login'}) + '\n')
    row = {'id': 'r9', 'status': 'running', 'attempts': 1}
    (tmp_path / 'repair/requests').mkdir(parents=True, exist_ok=True)
    try:
        repair.outcome(row, log, tmp_path / 'a.txt', 1)
    except Exception as error:
        assert not error.retryable and 'login expired' in str(error)
    else:
        raise AssertionError('expected ProviderFailure')
    assert row['auth_expired'] is True


def test_menu_line_renders_states(page):
    agent = {'installed': True, 'state': 'running', 'job': {'title': 'Fix kaxa ACL'}, 'started_at': 1, 'heartbeat': 2,
             'last_error': ''}
    page.evaluate('a=>{state.portfolio.host_agent=a;renderProgress();}', agent)
    line = page.locator('#host-agent-line')
    page.click('#harness-button')
    line.wait_for(state='visible')
    assert 'Host agent running · Fix kaxa ACL' in line.text_content() and 'heartbeat' in line.text_content()
    assert page.evaluate("()=>$('harness-button').dataset.hostAgent") is None
    agent.update(state='login_expired', last_error=AUTH_MESSAGE)
    page.evaluate('a=>{state.portfolio.host_agent=a;renderProgress();}', agent)
    assert 'login expired' in line.text_content() and 'claude /login' in line.text_content()
    assert page.evaluate("()=>$('harness-button').dataset.hostAgent") == 'bad'
    page.evaluate('()=>{state.restricted=true;renderProgress();}')
    page.wait_for_function("()=>$('host-agent-line').hidden")

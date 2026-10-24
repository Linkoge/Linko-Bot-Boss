"""Read-only host repair progress: receipts + run logs under a temp DATA dir."""
import json
import os
import time

from ai_manager.repair_progress import host_requests, redact, trim

NOW = 1_000_000.0


def setup(tmp_path, receipt, log=None, config=None):
    root = tmp_path / 'repair'
    (root / 'requests').mkdir(parents=True)
    if config is not None:
        (root / 'installed.json').write_text(json.dumps(config))
    receipt = {'id': 'r1', 'status': 'running', 'attempts': 1, 'provider': 'codex', 'conversation_id': 'c1',
               'created_at': NOW - 800, 'started_at': NOW - 720, 'deadline': NOW + 6000, 'updated_at': NOW - 700,
               **receipt}
    (root / 'requests' / 'r1.json').write_text(json.dumps(receipt))
    if log is not None:
        run = root / 'runs' / 'r1'
        run.mkdir(parents=True)
        path = run / f"{receipt['attempts']}.jsonl"
        path.write_text(log)
        os.utime(path, (NOW - 30, NOW - 30))
    return tmp_path


def codex(command, message):
    return '\n'.join(json.dumps(e) for e in [
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': command}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': message}}]) + '\n'


def one(tmp_path, now=NOW):
    rows = host_requests(tmp_path, now)
    assert len(rows) == 1
    return rows[0]


def test_running_state_and_codex_last_step(tmp_path):
    setup(tmp_path, {}, codex('apt-get install -y qemu-kvm', 'Installing the hypervisor\nnow configuring libvirt'),
          {'max_attempts': 4})
    row = one(tmp_path)
    assert row['state'] == 'running' and row['attempt'] == 1 and row['max_attempts'] == 4
    assert row['elapsed'] == 720 and row['provider'] == 'codex' and row['deadline'] == NOW + 6000
    assert row['last_tool'] == 'command: apt-get install -y qemu-kvm'
    assert row['last_text'] == 'now configuring libvirt' and row['last_step'] == row['last_text'] and not row['stale']


def test_claude_events_and_trimming(tmp_path):
    event = {'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'x' * 400}, {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'systemctl restart y'}}]}}
    setup(tmp_path, {'provider': 'claude'}, json.dumps(event) + '\n')
    row = one(tmp_path)
    assert row['last_step'] == 'Bash: systemctl restart y' and len(row['last_text']) == 120


def test_retrying_waiting_queued_done_failed(tmp_path):
    for status, attempts, state in [('running', 2, 'retrying'), ('waiting', 1, 'waiting'), ('pending', 0, 'queued'),
                                    ('succeeded', 1, 'done'), ('failed', 1, 'failed')]:
        directory = tmp_path / status
        setup(directory, {'status': status, 'attempts': attempts, 'updated_at': NOW - 5}, codex('ls', 'ok'))
        assert one(directory)['state'] == state


def test_finished_requests_disappear_after_a_while(tmp_path):
    setup(tmp_path, {'status': 'succeeded', 'updated_at': NOW - 5000})
    assert host_requests(tmp_path, NOW) == []


def test_redaction_of_env_and_key_shaped_values(tmp_path):
    key = 'sk-' + 'ab12' * 8
    token = 'ghp_' + 'Zx9' * 8
    assert key not in redact(f'curl with {key}') and token not in redact(token)
    long = ''.join(['a1'] * 20)
    assert redact(f'export API_TOKEN={key} && run') == 'export API_TOKEN=*** && run'
    assert long not in redact('id ' + long) and 'Bearer abcdefghij1234' not in redact('Bearer abcdefghij1234')
    setup(tmp_path, {}, codex(f'OPENAI_API_KEY={key} codex login --token {token}', f'using {key}'))
    dump = json.dumps(one(tmp_path))
    assert key not in dump and token not in dump and 'OPENAI_API_KEY=***' in dump
    assert len(trim('y ' * 500)) <= 120


def test_missing_and_partial_files_are_tolerated(tmp_path):
    assert host_requests(tmp_path / 'nothing', NOW) == []
    setup(tmp_path, {}, '{"type": "item.completed", "item": {"type": "agent_message", "text": "fine"}}\n{"type": "ite')
    assert one(tmp_path)['last_text'] == 'fine'
    (tmp_path / 'repair/requests/bad.json').write_text('{"id": "x", "stat')
    (tmp_path / 'repair/requests/list.json').write_text('[1]')
    (tmp_path / 'repair/installed.json').write_text('{broken')
    assert [r['id'] for r in host_requests(tmp_path, NOW)] == ['r1']
    (tmp_path / 'repair/runs/r1/1.jsonl').unlink()
    row = one(tmp_path)
    assert row['last_step'] == '' and row['state'] == 'running'


def test_stale_request_is_flagged(tmp_path):
    setup(tmp_path, {'updated_at': NOW - 400}, codex('sleep 9', 'waiting'), {'turn_seconds': 600})
    assert not one(tmp_path)['stale']
    row = one(tmp_path, NOW + 700)
    assert row['stale'] and row['idle'] > 600 and row['state'] == 'running'
    waiting = tmp_path / 'w'
    setup(waiting, {'status': 'waiting', 'updated_at': NOW - 4000}, config={'turn_seconds': 600})
    assert not one(waiting)['stale']


def test_portfolio_reports_host_requests_and_restricted_hides_them(tmp_path):
    from test_restricted import instance, sign_in

    from fastapi.testclient import TestClient
    for restricted in (False, True):
        settings, app = instance(tmp_path, 'r' if restricted else 'm', restricted)
        now = time.time()
        setup(settings.data_dir, {'started_at': now - 60, 'updated_at': now, 'created_at': now - 60,
                                  'deadline': now + 600}, codex('ls', 'working'))
        with TestClient(app, base_url='http://localhost') as browser:
            sign_in(browser, settings)
            rows = browser.get('/api/portfolio').json()['host_repair']
        if restricted:
            assert rows == []
        else:
            assert [r['id'] for r in rows] == ['r1'] and rows[0]['conversation_known'] is False
            assert rows[0]['last_text'] == 'working'

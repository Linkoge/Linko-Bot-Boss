import json
import time
import urllib.error
from datetime import datetime, timezone

import pytest

from ai_manager import claude_host
from ai_manager.account_usage import AccountUsage

TOKEN = 'dummy-access-token-for-tests'


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class Endpoint:
    """Stands in for urllib.request.urlopen; records every request and its timeout."""

    def __init__(self, monkeypatch, body=None, error=None):
        self.body, self.error, self.requests = body, error, []
        monkeypatch.setattr(claude_host.urllib.request, 'urlopen', self)

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        body = json.dumps(self.body).encode()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, limit=-1):
                return body

        return Response()


def login(tmp_path, monkeypatch, **oauth):
    config = tmp_path / 'claude-config'
    config.mkdir(exist_ok=True)
    (config / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': TOKEN, **oauth}}))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(config))


def usage(five, week, reset5, reset7):
    return {'five_hour': {'utilization': five, 'resets_at': iso(reset5)},
            'seven_day': {'utilization': week, 'resets_at': iso(reset7)}}


@pytest.fixture
def host(tmp_path):
    return claude_host.Host(tmp_path / 'data', 'claude', True)


@pytest.mark.asyncio
async def test_fetch_fills_both_windows_and_persists(host, tmp_path, monkeypatch):
    login(tmp_path, monkeypatch)
    now = time.time()
    endpoint = Endpoint(monkeypatch, usage(42.5, 10, now + 3600, now + 86400))
    result = await host.rpc_account_rateLimits_read({})
    primary, secondary = result['rateLimits']['primary'], result['rateLimits']['secondary']
    assert (primary['usedPercent'], primary['windowDurationMins']) == (42.5, 300)
    assert (secondary['usedPercent'], secondary['windowDurationMins']) == (10, 10080)
    assert abs(primary['resetsAt'] - (now + 3600)) < 1 and abs(secondary['resetsAt'] - (now + 86400)) < 1
    assert result['rateLimits']['limitId'] == 'claude' and abs(result['observedAt'] - now) < 5
    assert json.loads(host.limits_file.read_text()) == result
    request, timeout = endpoint.requests[0]
    assert request.get_method() == 'GET' and request.data is None and 0 < timeout <= 10
    assert request.get_header('Authorization') == f'Bearer {TOKEN}'


@pytest.mark.asyncio
async def test_values_update_after_reset_through_account_usage(host, tmp_path, monkeypatch):
    login(tmp_path, monkeypatch)
    now = time.time()
    endpoint = Endpoint(monkeypatch, usage(90, 60, now + 5, now + 86400))

    class Server:
        usage_listener = usage_invalidate = None

        async def start(self):
            pass

        async def request(self, method, params, timeout=30):
            return await host.rpc_account_rateLimits_read(params)

    clock = [now]
    account = AccountUsage(Server(), clock=lambda: clock[0], monotonic=lambda: clock[0], provider='claude')
    first = (await account.read())['buckets'][0]['windows']
    assert [w['used_percent'] for w in first] == [90, 60]
    clock[0] = now + 61  # five-hour window reset passed; next 60s refresh picks up the new window
    endpoint.body = usage(1, 61, now + 61 + 18000, now + 86400)
    view = await account.read()
    windows = view['buckets'][0]['windows']
    assert [w['used_percent'] for w in windows] == [1, 61]
    assert not any(w['stale'] or w['reset_passed'] for w in windows) and view['status'] == 'available'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [
    'no-credentials', 'expired', 'http', 'network', 'shape', 'malformed-credentials'])
async def test_failure_keeps_last_saved_reading(host, tmp_path, monkeypatch, failure):
    login(tmp_path, monkeypatch)
    now = time.time()
    Endpoint(monkeypatch, usage(30, 20, now + 3600, now + 86400))
    saved = await host.rpc_account_rateLimits_read({})
    if failure == 'no-credentials':
        (tmp_path / 'claude-config' / '.credentials.json').unlink()
    elif failure == 'expired':
        login(tmp_path, monkeypatch, expiresAt=(now - 10) * 1000)
    elif failure == 'malformed-credentials':
        (tmp_path / 'claude-config' / '.credentials.json').write_text('{"claudeAiOauth": []}')
    elif failure == 'http':
        Endpoint(monkeypatch, error=urllib.error.HTTPError('u', 401, f'bad {TOKEN}', {}, None))
    elif failure == 'network':
        Endpoint(monkeypatch, error=OSError(f'unreachable {TOKEN}'))
    else:
        Endpoint(monkeypatch, {'five_hour': 'x', 'seven_day': {'utilization': 'high'}})
    again = await host.rpc_account_rateLimits_read({})
    assert again == saved and TOKEN not in json.dumps(again)


@pytest.mark.asyncio
async def test_failure_without_reading_is_unavailable_and_old_reading_is_stale(host, tmp_path, monkeypatch):
    login(tmp_path, monkeypatch)
    Endpoint(monkeypatch, error=OSError(TOKEN))
    assert await host.rpc_account_rateLimits_read({}) == {}
    old = time.time() - 1000
    host.limits_file.write_text(json.dumps({'rateLimits': {'limitId': 'claude', 'primary': {
        'usedPercent': 5, 'windowDurationMins': 300, 'resetsAt': time.time() + 3000, 'observedAt': old}},
        'observedAt': old}))

    class Server:
        usage_listener = usage_invalidate = None

        async def start(self):
            pass

        async def request(self, method, params, timeout=30):
            return await host.rpc_account_rateLimits_read(params)

    view = await AccountUsage(Server(), provider='claude').read()
    assert view['status'] == 'stale' and view['buckets'][0]['windows'][0]['stale']


@pytest.mark.asyncio
async def test_missing_login_makes_no_request_and_one_window_merges(host, tmp_path, monkeypatch):
    endpoint = Endpoint(monkeypatch, usage(1, 1, 1, 1))
    assert await host.rpc_account_rateLimits_read({}) == {}
    assert endpoint.requests == []
    login(tmp_path, monkeypatch)
    endpoint.body = {'five_hour': {'utilization': 12, 'resets_at': None}, 'seven_day': None}
    first = await host.rpc_account_rateLimits_read({})
    assert first['rateLimits']['primary']['usedPercent'] == 12 and 'secondary' not in first['rateLimits']
    assert first['rateLimits']['primary']['resetsAt'] is None


@pytest.mark.asyncio
async def test_passive_events_still_update_and_fetch_result_is_not_logged(host, tmp_path, monkeypatch, capsys):
    notes = []

    async def notify(method, params):
        notes.append((method, params))
    host.notify = notify
    await host.limits({'unifiedWindows': {'five_hour': {'utilization': 0.25, 'resetsAt': 4102444800}}})
    assert notes[-1][1]['rateLimits']['primary']['usedPercent'] == 25.0
    login(tmp_path, monkeypatch)
    Endpoint(monkeypatch, error=RuntimeError(TOKEN))
    assert (await host.rpc_account_rateLimits_read({}))['rateLimits']['primary']['usedPercent'] == 25.0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err

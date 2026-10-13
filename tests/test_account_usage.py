import asyncio
from types import SimpleNamespace

import pytest
from test_web import client as web_client  # noqa: F401, F811
from test_web import sign_in

from ai_manager.account_usage import AccountUsage
from ai_manager.codex import AppServer


def snapshot(used=48, duration=300, reset=2000):
    return {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset}


def payload():
    # Installed 0.162.1 read shape; durations/percentages actually observed.
    return {"rateLimitsByLimitId": {"codex": {
        "limitId": "codex", "planType": "plus", "primary": snapshot(),
        "secondary": snapshot(41, 10080, 3000),
    }}, "rateLimits": {"primary": snapshot(99)}}


def cache():
    return AccountUsage(SimpleNamespace(), clock=lambda: 1000, monotonic=lambda: 10)


def test_installed_shape_multi_bucket_precedence_and_reset_math():
    usage = cache()
    usage.update(payload(), replace=True)
    result = usage.view()
    one, two = result["buckets"][0]["windows"]
    assert (one["used_percent"], one["remaining_percent"], one["reset_in_seconds"]) == (48, 52, 1000)
    assert (one["label"], two["label"], two["remaining_percent"]) == (
        "Five-hour allowance", "Weekly allowance", 59)
    assert result["scope"] == "account" and result["status"] == "available"
    assert result["last_successful_refresh_at"] == 1000


@pytest.mark.parametrize("used", [None, True, -1, "41", float('nan'), float('inf')])
def test_invalid_values_never_fabricate_remaining(used):
    usage = cache()
    usage.update({"rateLimits": {"primary": snapshot(used, None, None)}})
    w = usage.view()["buckets"][0]["windows"][0]
    assert w["used_percent"] is None and w["remaining_percent"] is None
    assert w["resets_at"] is None and w["reset_in_seconds"] is None
    assert usage.view()["status"] == "unavailable"


def test_changed_missing_reversed_windows_and_expired_reset():
    usage = cache()
    usage.update({"rateLimits": {"primary": snapshot(0, 10080, 900), "secondary": snapshot(100, 15, None)}})
    a, b = usage.view()["buckets"][0]["windows"]
    assert a["remaining_percent"] == 100 and a["stale"] and a["reset_in_seconds"] == 0
    assert b["remaining_percent"] == 0 and b["label"] == "15-minute allowance"
    usage.update({"rateLimits": {"primary": snapshot(105, 60)}}, replace=True)
    w = usage.view()["buckets"][0]["windows"]
    assert len(w) == 1 and w[0]["remaining_percent"] == 0 and w[0]["used_percent"] == 105
    usage.update({}, replace=True)
    assert usage.view()["status"] == "unavailable" and not usage.view()["buckets"]


def test_notifications_do_not_relabel_other_buckets_or_refresh_old_observations():
    usage = cache()
    usage.update(payload())
    usage.clock = lambda: 1200
    usage.update({"rateLimits": {"limitId": "codex_other", "primary": snapshot(17, 60)}})
    buckets = usage.view()["buckets"]
    assert len(buckets) == 2 and buckets[0]["windows"][0]["stale"]
    assert not buckets[1]["windows"][0]["stale"]
    usage.invalidate()
    assert not usage.view()["buckets"]


@pytest.mark.asyncio
async def test_coalesced_metadata_only_read_cooldown_stale_failure_and_recovery():
    usage = cache()
    calls = []

    async def start():
        if not calls:
            usage.invalidate()  # Installed App Server initializes account/updated.

    async def request(method, params, timeout):
        calls.append((method, params, timeout))
        await asyncio.sleep(0)
        return payload()

    usage.server.start, usage.server.request = start, request
    results = await asyncio.gather(*[usage.read() for _ in range(10)])
    assert len(calls) == 1 and all(r["status"] == "available" for r in results)
    assert calls[0][0] == 'account/rateLimits/read'
    assert calls[0][1] == {"excludeResetCreditDetails": True}

    async def fail(*args, **kwargs):
        raise RuntimeError("private diagnostics")

    usage.server.request = fail
    usage.monotonic = lambda: 71
    usage.clock = lambda: 1061
    result = await usage.read()
    assert result["status"] == "stale" and 'private' not in result["error"]
    assert result["last_attempt_at"] == 1061
    assert result["last_successful_refresh_at"] == 1000
    assert result["buckets"][0]["windows"][0]["remaining_percent"] == 52
    usage.server.request = request
    usage.monotonic = lambda: 132
    usage.clock = lambda: 1122
    recovered = await usage.read()
    assert recovered["error"] is None
    assert recovered["last_successful_refresh_at"] == 1122
    usage.clock = lambda: 1130
    cached = await usage.read()
    assert cached["last_successful_refresh_at"] == 1122
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_real_transport_notification_and_account_change():
    usage = cache()
    server = AppServer(SimpleNamespace())
    server.usage_listener, server.usage_invalidate = usage.update, usage.invalidate
    stream = asyncio.StreamReader()
    import json
    stream.feed_data((json.dumps({"method": "account/rateLimits/updated", "params": payload()})+'\n').encode())
    stream.feed_eof()
    await server._read(SimpleNamespace(stdout=stream))
    assert usage.view()["buckets"][0]["windows"][0]["remaining_percent"] == 52
    stream = asyncio.StreamReader()
    stream.feed_data(b'{"method":"account/updated","params":{"authMode":null}}\n')
    stream.feed_eof()
    await server._read(SimpleNamespace(stdout=stream))
    assert not usage.view()["buckets"]


def test_authenticated_api_unavailable_and_account_wide(web_client):  # noqa: F811
    browser, app, settings = web_client
    assert browser.get('/api/account-usage').status_code == 401
    sign_in(browser, app, settings)
    assert browser.get('/api/account-usage').json()['status'] == 'unavailable'
    assert browser.get('/api/account-usage?project_id=nonexistent').json()['scope'] == 'account'


def test_authenticated_api_real_shape_and_throttling(web_client):  # noqa: F811
    browser, app, settings = web_client
    sign_in(browser, app, settings)
    calls = []

    async def request(method, params, timeout):
        calls.append(method)
        return payload()

    app.state.chat.server.request = request
    first = browser.get('/api/account-usage').json()
    second = browser.get('/api/account-usage?project_id=unknown').json()
    assert first['buckets'][0]['windows'][0]['remaining_percent'] == 52
    assert first['buckets'] == second['buckets']
    assert calls == ['account/rateLimits/read']
    assert not app.state.db.all('SELECT * FROM executions')


@pytest.mark.asyncio
async def test_account_change_during_read_discards_old_account_result():
    usage = cache()

    async def start():
        pass

    async def request(*args, **kwargs):
        usage.invalidate()
        return payload()

    usage.server.start, usage.server.request = start, request
    assert not (await usage.read())['buckets']


def test_absent_codex_bucket_is_not_replaced_by_model_specific_quota():
    usage = cache()
    usage.update({'rateLimitsByLimitId': {'codex_other': {'primary': snapshot(42, 300)}}})
    assert [b['id'] for b in usage.view()['buckets']] == ['codex_other']


@pytest.mark.asyncio
async def test_failed_first_refresh_never_establishes_success():
    usage = cache()

    async def fail():
        raise RuntimeError("unavailable")

    usage.server.start = fail
    result = await usage.read()
    assert result["last_attempt_at"] == 1000
    assert result["last_successful_refresh_at"] is None
    assert result["status"] == "unavailable"


def test_successful_freshness_requires_usable_account_window_and_invalidates():
    usage = cache()
    for reading in ({}, {"rateLimits": {"primary": snapshot(None)}},
                    {"rateLimitsByLimitId": {"codex_other": {"primary": snapshot()}}}):
        usage.update(reading, replace=True)
        assert usage.view()["last_successful_refresh_at"] is None
    usage.update(payload(), replace=True)
    usage.clock = lambda: 1100
    usage.update({"rateLimitsByLimitId": {"codex_other": {"primary": snapshot()}}})
    assert usage.view()["last_successful_refresh_at"] == 1000
    usage.invalidate()
    assert usage.view()["last_successful_refresh_at"] is None

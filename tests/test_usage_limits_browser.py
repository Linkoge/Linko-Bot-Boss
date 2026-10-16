"""The limits widget follows persisted manager scope and rejects late/stale readings."""
# ruff: noqa: F811
import re
import time

import pytest
from test_chat_switch_browser import make_project
from test_model_picker_browser import browser_context, choose, open_panel, page, server  # noqa: F401


def test_limits_switch_with_repo_lock_manager_and_unavailable_provider(page, browser_context):
    held, mode = [], {'hold': False, 'unavailable': False, 'stale': False}

    def respond(route):
        response = route.fetch()
        data = response.json()  # The real endpoint resolves the saved effective manager.
        provider = data['provider']
        now = time.time()
        data.update(observed_at=now, metadata_stale=False, buckets=[] if mode['unavailable'] else [
            {'id': provider, 'windows': [
                {'duration_minutes': minutes, 'remaining_percent': remaining,
                 'observed_at': now, 'resets_at': now+3600, 'stale': mode['stale']}
                for minutes, remaining in [(300, 75 if provider == 'claude' else 20), (10080, 60)]
            ]}])
        route.fulfill(response=response, json=data)

    def usage(route):
        if mode['hold']:
            held.append(route)
        else:
            respond(route)

    page.route('**/api/account-usage*', usage)
    page.evaluate("()=>setUsageProvider(null,true)")
    page.wait_for_function("()=>$('usage-five').textContent==='20%'")
    project, _ = make_project(browser_context, count=2)
    page.evaluate("""async project=>{
      let panel=await api('/api/model-settings?project_id='+project);
      panel=await api('/api/projects/'+project+'/model-lock',{method:'PUT',body:JSON.stringify({revision:panel.settings.revision,locked:true})});
      await api('/api/model-settings?project_id='+project+'&locked=true',{method:'PUT',body:JSON.stringify({...panel.settings,manager:{model:'claude-sonnet-5-5',effort:'high'}})});
      await refresh(false);
    }""", project)
    mode['hold'] = True
    page.evaluate('project=>selectProject(project)', project)
    assert page.locator('#usage-five').inner_text() == 'unavailable'
    page.wait_for_timeout(50)
    assert held and 'project_id=' + project in held[0].request.url
    mode['hold'] = False
    respond(held.pop(0))
    page.wait_for_function("()=>$('usage-five').textContent==='75%'")
    assert page.locator('#usage-week').inner_text() == '60%'
    assert page.locator('#usage-button').get_attribute('aria-label').startswith('Claude')
    assert page.locator('#model-settings-button').get_attribute('data-provider') == 'claude'
    # Turning the repo lock off immediately returns to the global Codex selection.
    open_panel(page)
    choose(page, 'repo-lock')
    page.wait_for_function("()=>$('usage-five').textContent==='20%'")
    page.locator('#model-close').click()
    page.evaluate('()=>selectProject(null)')
    page.wait_for_function("()=>$('usage-five').textContent==='20%'")
    # Changing the saved global manager clears the old provider before metadata returns.
    mode['hold'] = True
    open_panel(page)
    choose(page, 'manager-Sonnet')
    assert page.locator('#usage-five').inner_text() == 'unavailable'
    page.wait_for_timeout(50)
    assert held
    mode['hold'] = False
    for route in held:
        respond(route)
    held.clear()
    page.wait_for_function("()=>$('usage-five').textContent==='75%'")
    page.locator('#model-close').click()
    # Stale or unsupported data must never retain a numeric percentage.
    for key in ('stale', 'unavailable'):
        mode[key] = True
        page.evaluate("()=>setUsageProvider('claude',true)")
        page.wait_for_function('()=>usageRequest===null')
        assert page.locator('#usage-five').inner_text() == 'unavailable'
        assert page.locator('#usage-week').inner_text() == 'unavailable'
        assert page.locator('#usage-five').evaluate('n=>n.scrollWidth<=n.clientWidth')
        mode[key] = False
    # A global save started in another context must also refresh the current
    # unlocked repo if the owner switches while that save is in flight.
    open_panel(page)
    choose(page, 'manager-Luna')
    page.wait_for_function("()=>$('usage-five').textContent==='20%'")
    waiting = []
    path = '**/api/model-settings?locked=false'
    page.route(path, lambda route: waiting.append(route))
    page.locator('[data-choice="manager-Sonnet"]').click()
    page.wait_for_timeout(100)
    assert waiting
    assert page.locator('#usage-five').inner_text() == 'unavailable'
    page.locator('#model-close').click()
    page.evaluate('project=>selectProject(project)', project)
    page.wait_for_function("()=>$('usage-five').textContent==='20%'")
    waiting[0].fulfill(response=waiting[0].fetch())
    page.unroute(path)
    page.wait_for_function("()=>!modelSaving&&$('usage-five').textContent==='75%'")


@pytest.mark.parametrize('width,height', [(1200, 900), (393, 851), (320, 640), (320, 420)])
def test_expanded_limits_show_both_provider_cards_for_either_manager(page, width, height, tmp_path):
    page.set_viewport_size({'width': width, 'height': height})
    mode = {'unavailable': None, 'stale': None}

    def usage(route):
        response = route.fetch()
        data = response.json()
        now = time.time()
        providers = {}
        for provider, values in [('codex', (20, 35)), ('claude', (75, 90))]:
            providers[provider] = {
                'provider': provider, 'observed_at': now, 'metadata_stale': False,
                'last_successful_refresh_at': now,
                'buckets': [] if mode['unavailable'] == provider else [{
                    'id': provider, 'credits': {'balance': '12' if provider == 'codex' else '34'},
                    'windows': [
                        {'duration_minutes': minutes, 'remaining_percent': remaining, 'observed_at': now,
                         'resets_at': now + 3600, 'stale': mode['stale'] == provider}
                        for minutes, remaining in zip((300, 10080), values)
                    ]}],
            }
        route.fulfill(response=response, json={**data, **providers[data['provider']], 'providers': providers})

    page.route('**/api/account-usage*', usage)
    original = page.evaluate("()=>api('/api/model-settings')")['settings']
    for manager, provider, compact in [('gpt-6.1-sol', 'codex', '20%'), ('claude-sonnet-5-5', 'claude', '75%')]:
        page.evaluate("""async model=>{
          const panel=await api('/api/model-settings');
          await api('/api/model-settings',{method:'PUT',body:JSON.stringify({...panel.settings,manager:{model,effort:'low'}})});
          setUsageProvider(null,true);
        }""", manager)
        page.wait_for_function("value=>$('usage-five').textContent===value", arg=compact)
        page.locator('#usage-button').click()
        page.wait_for_function('()=>!usageRequest')
        assert page.locator('.usage-provider').count() == 2
        assert page.locator('.usage-provider-head h3').count() == 0
        assert page.locator('.usage-provider-head').all_inner_texts() == ['', '']
        assert page.locator('.usage-provider .usage-logo').evaluate_all("n=>n.map(x=>x.getAttribute('aria-label'))") == ['OpenAI / Codex', 'Claude']
        assert not any('resets in' in t.lower() for t in page.locator('.usage-reset').all_inner_texts())
        assert page.locator('.usage-reset').first.inner_text() != 'Reset time unavailable'
        assert re.fullmatch(r'Last refresh \d\d:\d\d', page.locator('#usage-last-refresh').inner_text())
        assert page.locator('.usage-logo svg').count() == 2
        assert page.locator('#usage-details img, #usage-details use').count() == 0
        assert page.locator('.usage-remaining').all_inner_texts() == ['20%', '35%', '75%', '90%']
        assert page.locator('.usage-bar').evaluate_all("nodes=>nodes.map(n=>n.getAttribute('aria-valuenow'))") == ['20', '35', '75', '90']
        assert page.locator('.usage-window h4').all_inner_texts() == ['5 hour', 'Weekly', '5 hour', 'Weekly']
        for card in page.locator('.usage-provider').all():
            assert card.evaluate('n=>n.scrollWidth<=n.clientWidth')
            assert card.evaluate("n=>getComputedStyle(n).borderRadius") == '12px'
            assert card.evaluate("n=>getComputedStyle(n).backgroundColor") == 'rgb(32, 39, 52)'
        dialog = page.locator('#usage-dialog')
        assert dialog.evaluate('n=>n.scrollWidth<=n.clientWidth')
        box = dialog.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['height'] <= height
        # Credits stay scoped and can be expanded independently without overflowing.
        for name, balance in [('codex', '12'), ('claude', '34')]:
            metadata = page.locator('#usage-provider-' + name + ' .usage-metadata')
            metadata.locator('summary').click()
            assert 'Credits: ' + balance in metadata.inner_text()
            page.locator('#usage-refresh').click()
            page.wait_for_function('()=>!usageRequest')
            assert metadata.get_attribute('open') is not None
            metadata.locator('summary').click()
        for kind in ('unavailable', 'stale'):
            for failed in ('codex', 'claude'):
                mode[kind] = failed
                page.locator('#usage-refresh').click()
                page.wait_for_function('()=>!usageRequest')
                assert page.locator('#usage-provider-' + failed + ' .usage-remaining').all_inner_texts() == ['Unavailable'] * 2
                assert page.locator('#usage-provider-' + failed + ' .usage-bar[aria-valuenow]').count() == 0
                healthy = 'claude' if failed == 'codex' else 'codex'
                assert page.locator('#usage-provider-' + healthy + ' .usage-remaining').all_inner_texts() == (
                    ['75%', '90%'] if healthy == 'claude' else ['20%', '35%'])
                assert page.locator('#usage-five').inner_text() == ('unavailable' if failed == provider else compact)
                mode[kind] = None
        page.locator('#usage-refresh').click()
        page.wait_for_function('()=>!usageRequest')
        # Native modal behavior and focus return survive the extra card controls.
        page.locator('#usage-close').focus()
        page.keyboard.press('Shift+Tab')
        assert page.locator('#usage-refresh').evaluate('n=>n===document.activeElement')
        page.keyboard.press('Tab')
        assert page.locator('#usage-close').evaluate('n=>n===document.activeElement')
        dialog.screenshot(path=str(tmp_path / f'limits-{width}-{provider}.png'))
        page.keyboard.press('Escape')
        assert page.locator('#usage-button').evaluate('n=>n===document.activeElement')
    page.evaluate("""async settings=>{
      const panel=await api('/api/model-settings');
      await api('/api/model-settings',{method:'PUT',body:JSON.stringify({...settings,revision:panel.settings.revision})});
    }""", original)

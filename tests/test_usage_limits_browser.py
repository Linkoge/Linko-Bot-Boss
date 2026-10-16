"""The limits widget follows persisted manager scope and rejects late/stale readings."""
# ruff: noqa: F811
import time

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

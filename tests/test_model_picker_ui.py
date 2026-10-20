"""Picker structure and accessible repo lock controls; browser tests verify behavior and layout."""
import hashlib
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
LOCK_HEADER = '<span aria-hidden="true">·</span><div id="repo-lock-container" class="model-choice-row"></div><span aria-hidden="true">·</span>'
SWITCH = '<div id="worker-mode" class="model-choice-row" role="group" aria-label="Worker selection mode"></div>'
USERS = '<section id="instance-models" class="model-favorites" aria-labelledby="users-heading" hidden><h2 id="users-heading">Users</h2><div id="user-model-choices"></div></section>'


def _js():
    return (STATIC / 'model-settings.js').read_text()


@pytest.mark.smoke
def test_dialog_markup_is_the_original_manager_worker_favorites_layout_plus_the_worker_switch():
    html = (STATIC / 'index.html').read_text()
    dialog = re.search(r'<dialog id="model-dialog".*?</dialog>', html)[0]
    assert digest(dialog.replace(SWITCH, '').replace(LOCK_HEADER, '').replace(USERS, '')) == 'dca2607d9762556f0dc0816285b5e6d10ab0bf4b1a40b14a6ede57e1dc38fe14'
    assert USERS in dialog and dialog.index('id="manager-favorites"') < dialog.index('id="users-heading"')
    assert '<h2 id="manager-heading">Manager</h2>' + LOCK_HEADER in dialog
    assert 'id="repo-lock-container" class="model-choice-row"' in dialog
    assert '<h2 id="worker-heading">Worker</h2>' + SWITCH + '</div>' in dialog  # in the heading row, as Bot's was
    assert dialog.index('id="manager-choices"') < dialog.index('id="worker-choices"') < dialog.index('id="manager-favorites"')


def test_provider_cards_have_distinct_backgrounds_and_a_vertical_logo():
    css = (STATIC / 'style.css').read_text()
    assert '.provider-card{display:grid;grid-template-columns:minmax(0,1fr) 38px;' in css
    assert '.provider-card[data-provider=claude]{background:' in css
    assert '.provider-card .provider-logo{grid-column:2;grid-row:1 / 3;width:38px;' in css
    assert '.provider-logo[aria-pressed=true]' in css
    assert '.automatic-pool .model-choice-row button.effort-choice[aria-pressed=true]' in css
    assert '#repo-lock-container button{width:100%;overflow:hidden;text-overflow:ellipsis;' in css
    assert '#worker-mode{grid-template-columns:1fr 1fr;margin:0;flex:0 1 240px}' in css


def test_client_uses_one_settings_write_path_and_one_repo_toggle():
    js = _js()
    assert "card.prepend(models,levels);container.append(card)" in js
    assert "logo.innerHTML=PROVIDER_LOGOS[provider]" in js
    assert "if(!automatic)logo.removeAttribute('aria-pressed')" in js
    assert "renderPair(pair,role,`${user.name} ${titleLevel(role)}`,user,change" in js
    assert "section.hidden=!!state.restricted" in js
    assert "'/api/instance-model-settings?name='+encodeURIComponent(user.name)" in js
    assert "if(typeof clearUsage==='function')clearUsage()" in js
    assert 'setUsageProvider(provider,forceUsage)' in js
    assert 'setProviderIcon(modelPanel.provider,true)' in js
    assert 'setUsageProvider(null,true)' in js
    assert "const changeWorker=changeModels" in js
    assert 'modelWriteScope(project)' in js and 'model-lock' in js
    assert 'worker-model' not in js and 'project_worker' not in js and 'worker-default' not in js


def test_automatic_exists_only_as_the_manual_automatic_switch():
    for path in STATIC.glob('*.js'):
        text = path.read_text()
        assert 'worker-auto' not in text and "'Automatic'" not in text, path.name  # no stray "Automatic" row or button
    js = _js()
    assert js.count("['manual','automatic']") == 1 and "`${label} ${titleLevel(value)}`" in js
    assert "label='Worker',prefix='worker'" in js


def test_no_overlapping_pin_global_or_permitted_pairs_controls():
    js = _js().lower()
    assert 'pin' not in js and 'permitted' not in js and 'allowed_pairs' not in js
    assert "modelbutton('global'" not in js and "modelbutton('pin'" not in js


def test_pool_toggles_are_independent_and_cannot_remove_the_last_member():
    js = _js()
    assert "togglePool(c.worker,'allowed_models',m.model)" in js
    assert "togglePool(c.worker,'allowed_efforts',e)" in js
    assert "pair.allowed_models.length===1" in js and "pair.allowed_efforts.length===1" in js
    assert "if(values.length>1)values.splice(index,1)" in js
    assert "c.worker=newWorkerPool(panel,provider)" in js
    assert "?null:" not in js and 'const automatic=!settings.worker' not in js

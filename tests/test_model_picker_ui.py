"""Picker structure and accessible repo lock controls; browser tests verify behavior and layout."""
import hashlib
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
LOCK_HEADER = '<span id="repo-lock-separator" aria-hidden="true">·</span><div id="repo-lock-container" class="model-choice-row"></div>'
SWITCH = '<div id="worker-mode" class="model-choice-row" role="group" aria-label="Worker selection mode"></div>'
TOP = ('<div class="model-top"><select id="model-environment" aria-label="Environment" hidden></select>'
       '<div id="provider-visibility" class="model-choice-row" role="group" aria-label="Shown providers"></div>'
       '<button id="model-close" type="button" aria-label="Close model settings">×</button></div>'
       '<p id="model-environment-error" role="alert" hidden></p>')


def _js():
    return (STATIC / 'model-settings.js').read_text()


@pytest.mark.smoke
def test_dialog_markup_is_the_original_manager_worker_favorites_layout_plus_the_worker_switch():
    html = (STATIC / 'index.html').read_text()
    dialog = re.search(r'<dialog id="model-dialog".*?</dialog>', html)[0]
    assert digest(dialog.replace(SWITCH, '').replace(LOCK_HEADER, '').replace(TOP, '')) == 'a404aa4d497009e79397bba3ae81fd4a20a731c698cdc58292136896a7e82b96'
    # Environment selector and provider show/hide sit on top; there is no separate Users section any more.
    assert dialog.startswith('<dialog id="model-dialog" aria-label="Manager and worker settings">' + TOP)
    assert 'users-heading' not in dialog and 'instance-models' not in dialog
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
    assert "renderPair($('manager-choices'),'manager','Manager',panel,changeEnvironmentModels)" in js
    assert "select.hidden=!!state.restricted" in js
    assert "'/api/instance-model-settings?name='+encodeURIComponent(env)" in js
    assert 'renderUserModels' not in js and 'user-model-choices' not in js
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

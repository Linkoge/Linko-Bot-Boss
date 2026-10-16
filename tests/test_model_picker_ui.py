"""Picker structure and accessible repo lock controls; browser tests verify behavior and layout."""
import hashlib
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
LOCK_HEADER = '<span aria-hidden="true">·</span><div id="repo-lock-container" class="model-choice-row"></div><span aria-hidden="true">·</span>'
SWITCH = '<div id="worker-mode" class="model-choice-row" role="group" aria-label="Worker selection mode"></div>'


def _js():
    return (STATIC / 'model-settings.js').read_text()


@pytest.mark.smoke
def test_dialog_markup_is_the_original_manager_worker_favorites_layout_plus_the_worker_switch():
    html = (STATIC / 'index.html').read_text()
    dialog = re.search(r'<dialog id="model-dialog".*?</dialog>', html)[0]
    assert digest(dialog.replace(SWITCH, '').replace(LOCK_HEADER, '')) == 'dca2607d9762556f0dc0816285b5e6d10ab0bf4b1a40b14a6ede57e1dc38fe14'
    assert '<h2 id="manager-heading">Manager</h2>' + LOCK_HEADER in dialog
    assert 'id="repo-lock-container" class="model-choice-row"' in dialog
    assert '<h2 id="worker-heading">Worker</h2>' + SWITCH + '</div>' in dialog  # in the heading row, as Bot's was
    assert dialog.index('id="manager-choices"') < dialog.index('id="worker-choices"') < dialog.index('id="manager-favorites"')


def test_picker_styles_are_the_original_ones_and_the_switch_is_drawn_like_the_old_bot_switch():
    css = (STATIC / 'style.css').read_text()
    rules = [line for line in css.splitlines() if re.search(r'model-|favorite|#model|\.bot-settings', line)
             and not line.startswith('#model-dialog section:first-child')]
    assert len(rules) == 29
    assert digest('\n'.join(rules)) == '410c53f33ca1983787accd7236aab2fa3116d3459703b16af4ab48da943f59a7'
    assert '.model-choice-row button[aria-pressed=true]{background:var(--accent-bg)' in css
    assert '#repo-lock-container button{width:100%;overflow:hidden;text-overflow:ellipsis;' in css
    # The 81711bd rule for the Bot switch, verbatim apart from the id: two equal columns, 240px, no margin.
    assert [line for line in css.splitlines() if 'worker-mode' in line] == ['#worker-mode{grid-template-columns:1fr 1fr;margin:0;flex:0 1 240px}']


def test_client_uses_one_settings_write_path_and_one_repo_toggle():
    js = _js()
    # Snapshot includes scoped limits invalidation after confirmed model/lock saves.
    assert digest(js) == '14d8ae15cd719bf133a39c4ce175e3c1e46cda823e84f68aa1cdd65c20416ccb'
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
        assert 'worker-auto' not in text and 'Automatic' not in text, path.name  # no stray "Automatic" row or button
    js = _js()
    assert js.count("['manual','automatic']") == 1 and "`Worker ${titleLevel(value)}`" in js


def test_no_overlapping_pin_global_or_permitted_pairs_controls():
    js = _js().lower()
    assert 'pin' not in js and 'permitted' not in js and 'allowed_pairs' not in js
    assert "modelbutton('global'" not in js and "modelbutton('pin'" not in js

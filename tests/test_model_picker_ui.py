"""The model picker is the layout deployed at 208554a4cd (Manager / Worker / Favorites, a model row and an effort row per
provider, three families, Low/Medium/High) with one deliberate addition: the Manual | Automatic switch in the Worker heading
row, drawn exactly like the one the earlier picker (81711bd) had in its Bot heading row. Markup and styles stay pinned to
those revisions. The client hash includes the requested per-project worker persistence and clear action; browser tests
pin the original global geometry and exercise the project selections in tests/test_model_picker_browser.py."""
import hashlib
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
SWITCH = '<div id="worker-mode" class="model-choice-row" role="group" aria-label="Worker selection mode"></div>'


def _js():
    return (STATIC / 'model-settings.js').read_text()


@pytest.mark.smoke
def test_dialog_markup_is_the_original_manager_worker_favorites_layout_plus_the_worker_switch():
    html = (STATIC / 'index.html').read_text()
    dialog = re.search(r'<dialog id="model-dialog".*?</dialog>', html)[0]
    assert digest(dialog.replace(SWITCH, '')) == 'dca2607d9762556f0dc0816285b5e6d10ab0bf4b1a40b14a6ede57e1dc38fe14'
    assert '<h2 id="worker-heading">Worker</h2>' + SWITCH + '</div>' in dialog  # in the heading row, as Bot's was
    assert dialog.index('id="manager-choices"') < dialog.index('id="worker-choices"') < dialog.index('id="manager-favorites"')


def test_picker_styles_are_the_original_ones_and_the_switch_is_drawn_like_the_old_bot_switch():
    css = (STATIC / 'style.css').read_text()
    rules = [line for line in css.splitlines() if re.search(r'model-|favorite|#model|\.bot-settings', line)]
    assert len(rules) == 29
    assert digest('\n'.join(rules)) == '410c53f33ca1983787accd7236aab2fa3116d3459703b16af4ab48da943f59a7'
    # The 81711bd rule for the Bot switch, verbatim apart from the id: two equal columns, 240px, no margin.
    assert [line for line in css.splitlines() if 'worker-mode' in line] == ['#worker-mode{grid-template-columns:1fr 1fr;margin:0;flex:0 1 240px}']


def test_client_code_is_the_reviewed_picker_with_project_scoped_worker_persistence():
    # Hash the whole client, including the added scope capture, persisted effective worker and reset action.
    # The unchanged markup/style hashes above and rendered geometry checks retain the original design contract.
    assert digest(_js()) == 'ff6a6439032fc742ab7201bbd3722bb5fb2fcc7d6600a0892708cc1418e72bcb'


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

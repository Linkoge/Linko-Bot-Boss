"""The model picker is the layout deployed at 208554a4cd (Manager / Worker / Favorites, a model row and an effort row per
provider, three families, Low/Medium/High) with one deliberate addition: the Manual | Automatic switch in the Worker heading
row, drawn exactly like the one the earlier picker (81711bd) had in its Bot heading row. These tests pin markup, styles and
client code to those revisions, so any other change to the picker's look or structure means changing a hash on purpose, with
the owner's agreement. The rendered result is pinned by tests/test_model_picker_browser.py."""
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


def test_client_code_is_the_original_except_the_documented_worker_switch_and_off_family_effort_row():
    js = _js()
    original = js.replace("const PICKER_LEVELS=['low','medium','high'];\n", '').replace(
        "    const all=[...PICKER_LEVELS,...(selected?.levels||[]).filter(e=>!PICKER_LEVELS.includes(e))]; // Low/Medium/High; "
        "only a selected off-family model adds its own\n    for(const e of all)",
        "    for(const e of ['low','medium','high'])")
    # Deviation 2: the worker is null while Automatic (nothing is pressed) and the Manual | Automatic switch.
    mode = original[original.index('function renderMode()'):original.index('function renderPair(')]
    original = original.replace(mode, '').replace('  renderMode();\n', '').replace(
        " // the worker is null while automatic: the manager picks per task, nothing here is pressed", '').replace(
        'pair?.model', 'pair.model')
    original = original.replace(
        "addModel(models,m,`${label} ${m.name}`,pair.model===m.model,\n      ()=>changeModels(c=>{c[role]??={...c.manual};selectModel(c[role],m);}),",
        "addModel(models,m,`${label} ${m.name}`,pair.model===m.model,()=>changeModels(c=>selectModel(c[role],m)),")
    assert digest(original) == '0f9636add8a325e38e941cd2cbd208cbe62166026b1cbd16653729746f5da106'


def test_automatic_exists_only_as_the_manual_automatic_switch():
    for path in STATIC.glob('*.js'):
        text = path.read_text()
        assert 'worker-auto' not in text and 'Automatic' not in text, path.name  # no stray "Automatic" row or button
    js = _js()
    assert js.count("['manual','automatic']") == 1 and "`Worker ${titleLevel(value)}`" in js

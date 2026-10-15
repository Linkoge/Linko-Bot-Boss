"""The model picker is the UI that was deployed at 208554a4cd, the revision immediately before the simplification commit
c530e94. These tests pin its markup, styles and client code to that revision; changing the picker's look or structure means
changing these hashes on purpose, with the owner's agreement."""
import hashlib
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'ai_manager' / 'static'
digest = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731


def _js():
    return (STATIC / 'model-settings.js').read_text()


@pytest.mark.smoke
def test_dialog_markup_is_the_original_manager_worker_favorites_layout():
    html = (STATIC / 'index.html').read_text()
    dialog = re.search(r'<dialog id="model-dialog".*?</dialog>', html)[0]
    assert digest(dialog) == 'dca2607d9762556f0dc0816285b5e6d10ab0bf4b1a40b14a6ede57e1dc38fe14'
    assert dialog.index('id="manager-choices"') < dialog.index('id="worker-choices"') < dialog.index('id="manager-favorites"')


def test_picker_styles_are_the_original_ones():
    css = (STATIC / 'style.css').read_text()
    rules = [line for line in css.splitlines() if re.search(r'model-|favorite|#model|\.bot-settings', line)]
    assert len(rules) == 29
    assert digest('\n'.join(rules)) == '410c53f33ca1983787accd7236aab2fa3116d3459703b16af4ab48da943f59a7'


def test_client_code_is_the_original_except_the_documented_off_family_effort_row():
    js = _js()
    # The only deviation from the original file: a selected model outside the three families adds its own efforts.
    original = js.replace("const PICKER_LEVELS=['low','medium','high'];\n", '').replace(
        "    const all=[...PICKER_LEVELS,...(selected?.levels||[]).filter(e=>!PICKER_LEVELS.includes(e))]; // Low/Medium/High; "
        "only a selected off-family model adds its own\n    for(const e of all)",
        "    for(const e of ['low','medium','high'])")
    assert digest(original) == '0f9636add8a325e38e941cd2cbd208cbe62166026b1cbd16653729746f5da106'


def test_no_automatic_worker_row_anywhere_in_the_client():
    for path in STATIC.glob('*.js'):
        text = path.read_text()
        assert 'worker-auto' not in text and 'Automatic' not in text, path.name

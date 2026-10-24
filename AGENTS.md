## Known pitfalls

### New static asset breaks only the full suite (2026-10-25, task df8770)
When: you add a `.js`/`.css` file under `ai_manager/static/` (and its `<script>`/`<link>` in `ai_manager/static/index.html`).
Symptom: smoke and your own tests pass, but the post-merge full suite fails `tests/test_pwa.py::test_manifest_assets_and_cache_boundaries` (`assert len(urls) == N`).
Cause: that test counts the versioned asset URLs the page serves.
Do: raise the expected count in `tests/test_pwa.py` in the same commit, and run `pytest -q tests/test_pwa.py` before finishing.

### Fake keys must be built at runtime (2026-10-25, task 91a64a)
When: a test or fixture needs a key-, token- or PEM-shaped value.
Symptom: `scripts/worker_check.py` fails with `secrets: file:line ... (rule 10)`, or GitHub push protection (GH013) blocks the backup push.
Cause: a literal matching SECRET_PATTERNS (`ai_manager/push_diagnosis.py`) was committed; the check scans every commit from base to HEAD, so a later fix commit does not clear it.
Do: build it at runtime, e.g. `'ghp_' + 'Q' * 36`; if it is already committed, squash or amend so no commit contains it.

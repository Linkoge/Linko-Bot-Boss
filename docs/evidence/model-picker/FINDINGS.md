# Model picker: visual findings (2026-10-16)

Renders: real Chromium, disposable server, same fixture catalog and default saved state for every revision
(`git archive <rev>` served with that revision's own fixture). Left to right in `side-by-side-*.png`:
81711bd (Bot Manual/Automatic), 208554a, db292c6 (deployed), new Manual, new Automatic. Widths 1200, 393, 320.

## Facts established
- GitHub: `git ls-remote origin` answered; `origin/main` = `3f6f07a` (2026-10-11). Local `main` = `db292c6`, 147 commits ahead, 0
  behind. `208554a`, `c530e94`, `df6d154`, `2ae5229` and `db292c6` are NOT on the remote (`merge-base --is-ancestor`). No push was done.
- Live host: `deploy/last_good.json` and the deploy log show `db292c6045` deployed 01:07; the live server's `/static/*` files hash
  identically to the repository at `db292c6`. The live menu needs the owner login (`/api/model-settings` answers 401), so no
  live screenshot exists: the captures are local renders of those byte-identical assets. The live saved settings and catalog were not read.
- Picker history: 5c6e35b/f8f4d55/7a0188d/81711bd (Oct 15 10:21-21:16) had Manager / **Bot [Manual | Automatic]** / Favorites;
  198be86 (deployed 21:16) removed the Bot switch; c530e94 re-added a lone Automatic row and listed every catalog model/effort
  (sprawl); 2ae5229 kept the Automatic row; db292c6 removed Automatic again and pinned 208554a.

## Why db292c6 still looked wrong
`db292c6` renders byte-identical to `208554a` (cmp of the 1200 and 393 PNGs), so it was a faithful copy of the wrong target
for the owner: 208554a is the picker *after* the Bot Manual/Automatic switch was removed (198be86), and db292c6 additionally
removed the Automatic worker the simplification had asked for. Hash-equal markup could not show that; the pictures do:
the Worker heading row has no Manual | Automatic switch, and nothing in the UI can select Automatic.

## Differences, new picker vs 81711bd (reference for the switch) and 208554a (reference for the sections)
- Same as 208554a: Manager / Worker / Favorites sections, 3-column model and effort rows per provider, dialog 420/361/304 x 572.
- Same as 81711bd: the Manual | Automatic switch in the Worker heading row (same CSS: two equal columns, 240px, right-aligned
  with the close button, 38px, neutral fill, accent text on the pressed one). Dialog height is identical to 81711bd (572).
- Deviations: the heading says "Worker" (not "Bot"); exactly one worker pair is pressed (81711bd pressed one per provider because
  it kept per-provider settings; the worker is now one independent choice); in Automatic no model or effort is pressed, the
  effort rows are disabled like any effort row without a selected model, and tapping a model makes it a manual pick.
- Inference, not confirmed with the owner: which checkpoint the owner regards as known-good. The evidence points to 81711bd's
  switch plus 208554a's sections; if "Bot" as the heading word is wanted, it is one word in `index.html`.

## Optional project worker override (assignment bf46935c)

This assignment preserves the existing dialog markup and CSS, including the Manual | Automatic switch. The global picker
still passes its original rendered geometry at 1200/393/320. In a project, the same worker buttons edit only that project's
selection. One additional button beneath the worker rows clears the override (Use default worker) or displays inheritance
(Following default worker). Manager edits/favorites still edit the global manager. There are no Pin/Global controls or
permitted-pair filters; catalog validation remains the only model/effort restriction.

One revisioned `project_worker:<id>` setting owns the optional selection. Its null override follows `model_choice`; an
explicit `{worker, manual}` object preserves the existing Manual/Automatic semantics within the project. `current` resolves
this selection for the panel, manager context and delegation. Direct queued tasks use the same resolver when first assigned;
retained task routes keep their original model. Global defaults are never populated from a project override.

Permanent evidence:

- `tests/test_project_worker_models.py`: two distinct simultaneous project overrides; empty overrides and later global
  changes; clearing/reopening the database and HTTP app; stale revisions, invalid models/efforts, authentication and CSRF;
  unrestricted off-family model/effort support without displaying another scope's inactive model; Automatic versus
  inheritance; direct queue resolution; actual manager prompt context with independent manager turn selection.
- Its smoke regression delegates from a global manager to two projects, clears an override, then changes the default.
  Three tasks run in global FIFO order. Each asserts the worker's actual `thread` model and `turn/start` model/effort,
  the execution model/effort, and the persisted `task.model_selected`/task API route after settings have changed.
- `tests/test_model_picker_browser.py`: project A override, untouched project B, reload, manager editing and favorite recall,
  project Manual/Automatic restoration, clearing, and following a later default change. A forced HTTP 503 while switching
  from A to B proves the previous project's cached controls are cleared instead of becoming editable.
- `tests/test_model_picker_ui.py`: original markup/style hashes retained. The reviewed client hash now includes scoped
  persistence, effective-worker rendering, the clear action, and clearing stale controls on refresh failure.

Validation uses the repository virtualenv, disposable fixture databases/directories, a dynamically allocated localhost
browser server, and fake provider protocol turns. No live host settings, services or paid provider calls were used.

Final checks (each pytest command bounded by `timeout 240s`, lint/probes by `timeout 60s`):

- Focused suites: `test_model_settings.py`, `test_project_worker_models.py`, `test_model_picker_ui.py`,
  `test_model_picker_browser.py`, `test_chat_queue.py`, `test_minimal_flow.py`: **51 passed**.
- Fixed `python -m pytest -q -m smoke`: **38 passed**, 315 deselected.
- Ruff on all changed Python files and `git diff --check`: passed.
- Pytest reports the existing Starlette/httpx deprecation warning; there are no check failures.

Repair round 1 removes the inline synthetic login credential flagged by delivery. The HTTP app reload regression now
reuses its existing authenticated session cookies and still asserts the persisted cleared override through the protected
endpoint. No allowlist or scanner changes are needed. Only this assignment's single unmerged commit is amended, so the
flagged added line is absent from delivered history. The delivery scanner checks the full base..HEAD range after amendment.
Repair validation: `test_project_worker_models.py` plus `test_github_backup.py`: **20 passed**; fixed smoke: **38 passed**;
Ruff on the changed test and `git diff --check`: passed.

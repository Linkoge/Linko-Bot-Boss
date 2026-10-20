# Model picker: visual findings (2026-10-16)

The comparisons below record the earlier picker. The provider-card redesign replaces its Automatic behavior: one
provider logo is selected, model and effort buttons independently light the saved pool, and new catalog entries
remain off. Permanent Chromium coverage now verifies provider backgrounds, 38px logos spanning both rows, no horizontal
overflow at 1200/390/320px, reload, provider switching and prevention of empty model/effort pools. Original screenshots
remain historical evidence; current settings and dispatch behavior are described in README.md.

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

## Repo lock (assignment 5f732438)

The earlier worker-only override is replaced. The header reads Manager · [active repo name] · ×. The repo button uses
picker styles and is highlighted while locked. Model/effort rows, Manual | Automatic, favorites and the compact dialog
geometry remain covered at 1200/393/320. The screenshots above record the earlier layout; they predate the repo header.

One record owns all settings in a scope: `model_choice:<id>` if present, otherwise `model_choice`. ON copies current
global settings; OFF deletes the repo record, including remembered Manual. Re-ON copies current global again. Manager
and worker edits use the same write API and resolver. Favorites read and recall the current scope. Manager chats, repo
memory reviews, task delegation/direct execution and reports use the repo record; a report delivered to All Projects
still uses its task repo's settings. A task's first execution pair remains fixed. No usage-limit model fallback is added.
Schema 16 consumes explicit old worker overrides into full locks with the current global manager; null inheritance
stays unlocked, and the old keys, worker-only API and reset UI are removed.

Permanent evidence:

- `tests/test_project_worker_models.py`: manager and worker resolution/dispatch, report routing from repo and global
  origin chats, task pair retention, full-lock isolation, Automatic/Manual, OFF/re-ON, reload, one-time migration from
  unified and older global stores, stale writes, validation, authentication/CSRF and off-family catalog choices.
- `tests/test_model_picker_browser.py`: active repo heading, visible ON highlight, copying global, both roles' edits,
  Automatic/Manual across reload, scope-aware favorites, unlocked edits updating global, OFF/re-ON discarding prior
  settings, and failed repo refresh clearing all cached controls. Existing rendered layout and Send checks remain.
- `tests/test_model_picker_ui.py`: retained section/header markup, provider-card styling and logo placement, independent
  pool toggles and empty-pool guards, plus the single write path and removed worker controls.
- `tests/test_todos.py`: Start resolves the source repo's worker using the full repo lock.

Checks use disposable fixture data and allocated localhost ports, fake provider protocol turns, bounded pytest suites
and the fixed smoke suite. No live host settings, services or paid provider calls are used.

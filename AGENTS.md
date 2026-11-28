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

### New dependency fails the gate until the shared venv is synced (2026-11-19, task 3054e8)
When: a change adds a package to `pyproject.toml` / `uv.lock`. (`scripts/secrets_backup.py` avoids this by calling the system `openssl` CLI and using only the standard library.)
Symptom: the self-check and the application's gate fail at collection with `ModuleNotFoundError` for the new package, even though `uv run pytest` passes in the worktree.
Cause: `ai_manager/core.py:interpreter()` runs the gate with the shared checkout's `.venv`, which is only synced (`uv sync --locked`) by deploy after merge, so it cannot have a dependency added on this branch.
Do: lock it (`uv lock`, a minimal diff), verify with `uv run python -m pytest` in the worktree, and report that the shared venv needs `uv sync --locked` before the gate can pass. Do not run that sync from the worker.

### New top-level runtime dir breaks other-user instances (2026-11-21, task 8cd3c2)
When: the app starts reading a new top-level checkout path at runtime (like `harnesses/`).
Symptom: every test passes, but the deploy stops ("instance NAME cannot see …") or, before that check, rolled back.
Cause: an instance whose root adapter is still the old per-path `mounts` bind list only sees the listed paths.
Do: add the path to `RUNTIME_PATHS` in `ai_manager/code_view.py` (`tests/test_code_view.py` fails until you do). Adapters migrated with `scripts/instance_adapter.py apply NAME` (whole-checkout mount) need nothing more; for a legacy one, report the owner must run that apply once (as root).

### New schema version breaks two migration tests (2026-11-21, task 155c8a)
When: you add a `schema_version` step in `ai_manager/db.py`.
Symptom: only the full suite fails: `tests/test_core.py::test_migration_closes_old_nonfinal_tasks...` and `tests/test_chat_reliability.py::test_schema10_additive_migration...` (`assert 18 == 17`).
Cause: both assert the exact highest schema version after migrating.
Do: raise the expected version in both tests in the same commit.

### Browser asserts on async UI state flake only in the deploy's suite (2026-11-21, task 155c8a)
When: a Playwright test clicks a control whose handler awaits a promise (clipboard, `api(...)`) and then reads the result.
Symptom: the test passes alone and under plain `pytest -n auto`, but `scripts/full_suite.py` (6 loaded workers) intermittently reports `1 failed`.
Cause: `inner_text()` / `page.evaluate()` right after `click()` runs before the awaited handler finishes.
Do: `page.wait_for_function(...)` for the expected state before asserting; reproduce with `python scripts/full_suite.py`, not just the single file.

### Fake unfinished executions race the app's scheduler (2026-11-21, task bdc5d3)
When: a test using a live `create_app` app (the `client`/`pair` fixtures; the scheduler runs) inserts an `executions` row with `finished_at IS NULL` by hand, or edits a task it just queued (the runner may claim it first).
Symptom: passes alone and in a serial run, fails only in the parallel full suite (`scripts/full_suite.py`), e.g. a guard expected 409 but got 200.
Cause: `Chat.recover()` on the scheduler loop finishes unowned unfinished manager rows (status `running`, no live pid) between the test's requests.
Do: insert a state recovery leaves alone, e.g. status `recovering` with `retry_at` an hour ahead (what `recovery.defer()` writes); for a queued task, pause its project first (`_claim` skips non-active projects); reproduce with a `time.sleep(3)` before the assertion.

### New built-in harness breaks exact-list tests (2026-11-27, task 21af98)
When: you add a folder to `BUILTIN_FOLDERS` in `ai_manager/harness.py`.
Symptom: harness, browser and code-view tests fail on an exact set or list of built-in ids or picker option names.
Cause: `tests/test_harness.py` (catalog set, worker options, manager context), `tests/test_harness_browser.py` (worker `<option>` texts) and `tests/test_code_view.py` (`START` probe set) pin the built-ins.
Do: update all four in the same commit; run those three files, the browser one included. Every built-in's description also rides in the manager context (`worker_options`), so a bigger catalog can push `tests/test_project_memory.py::test_brief_between_the_old_and_new_maximum_chats...` over the 4000-byte context ceiling; shrink that test's brief filler (it only needs to sit between the old and new brief maximum).

### Stripping AI_MANAGER_* env silently re-slows smoke   (2026-11-27, task 72503a)
When: a test or fixture deletes every `AI_MANAGER_*` (or similar) variable to isolate itself from the host.
Symptom: the test passes but is slow again (fsync per commit, ~85 ms argon2 per password); smoke drifts past its 45 s budget, worst on a loaded host.
Cause: `tests/conftest.py` sets the suite's speed switches as env vars (`AI_MANAGER_SQLITE_SYNCHRONOUS`, `AI_MANAGER_TEST_WEAK_PASSWORD_HASH`) so server subprocesses inherit them.
Do: keep `SYNCHRONOUS_ENV` (`ai_manager/db.py`) and `WEAK_HASH_ENV` (`ai_manager/auth.py`) when stripping (see `tests/test_public_access.py` `clean_env`); compare smoke timings under load (4 `python3 -c 'while True: pass'`), not just idle.

### New Settings panel is invisible on instances   (2026-11-27, task 6b2525)
When: you add an `<article class="panel">` to `#view-settings` that instances (office mode) must show too.
Symptom: it works on Main, but on an instance the panel never appears; no error, only `tests/test_restricted_browser.py` (exact list of visible settings headings) notices.
Cause: `ai_manager/static/style.css` hides every settings article on `.restricted` except a `:not(#id)` whitelist.
Do: add the panel's id to that `:not(...)` list and its heading to the test's list; check with `pytest -q tests/test_restricted_browser.py`.

### Manager tool text is pinned by digest   (2026-11-27, task de4cb8)
When: you edit `MANAGER_TOOL` in `ai_manager/orchestration.py` (its description or schema), e.g. to tell the manager about a new flow.
Symptom: `tests/test_harness.py::test_default_harnesses_are_byte_for_byte_todays_prompts_and_tools` fails on `digest(...) == TOOL_BEFORE`.
Cause: the default harness must keep today's manager tool byte for byte; the test pins its sha256.
Do: prefer telling the manager through data it already gets (`result_notice` in `ai_manager/chat.py`, the manager context); change the tool and `TOOL_BEFORE` together only when the tool itself must change.

### A deploy repair round after a parallel task touched the same file   (2026-11-27, task de4cb8)
When: a deploy repair round arrives and the last main commit is "Merge task; preserve user files and save AI versions".
Symptom: the deploy's full suite fails (the receipt names no test) although your branch passed: main kept the other task's version of a file you both changed and saved yours as `<name> (AI version).<ext>`, so your tests run against code without your change.
Cause: `merge_into_main` (`ai_manager/pipeline.py`) never overwrites on a conflict; it keeps main's file and adds a copy.
Do: `git log -1 --format=%B <base>` lists the copies; reconcile each into the real file (one implementation, keep both tasks' tests) and delete the copy in the same commit.

### Project-menu browser tests flake on late async re-renders   (2026-11-28, task 27af6c)
When: a Playwright test opens `#project-menu` on a fresh page and then reads or clicks its entries (`tests/test_github_projects_browser.py`).
Symptom: passes alone, fails ~half the full-file runs and the deploy suite: `nextElementSibling` null / "Element is not attached to the DOM".
Cause: the first open fetches `/api/machines`, `loadVersion()` and `loadMachineVersions()`, and each re-renders the open menu when it arrives, replacing the elements under the test.
Do: preload them in the `page` fixture (`await loadVersion(); await loadMachineVersions(true)`, `machinesLoaded=true`); any new async fetch that re-renders the menu needs the same.

### Typing into a settings field races its status reload   (2026-11-28, task 27af6c)
When: a Playwright test opens a Settings panel through the project menu (`openGithubSettings`) and `fill()`s a field it loads from the server.
Symptom: the saved value is the OLD one (`Actual value: acme`) although the "saved" text appeared; only under load.
Cause: the status GET fired by opening the panel replies after `fill()` and rewrites the field before the click submits it.
Do: wait for that response (`page.expect_response(...)`) and for the field to equal it before filling.

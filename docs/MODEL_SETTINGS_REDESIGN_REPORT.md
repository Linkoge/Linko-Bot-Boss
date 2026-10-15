# Compact model settings implementation report — 2026-10-15

Status: **PASSED candidate in the retained assignment branch**. Application-managed merge/deployment
has not occurred in this worker. Auto-deploy ON is task context, not a succeeded deployment receipt.
No push, shared checkout change, service restart, deployment, shared database write or unrelated task
resume/cancel occurred. No parallel workers or independent reviewer were started.

Assignment: `4a985c35b51345ada354cd666acbf80e`.
Branch: `linko/assignment-4a985c35b51345ada354cd666acbf80e`.
Base inspected: `ca0576cf1b6cb5c833d15b273846aaa23e9f7c6e`.
Workspace: `/home/codex/.local/share/linko-bot-manager/assignment-workspaces/4a985c35b51345ada354cd666acbf80e`.

## Inspected authority and actual capabilities

Read PROJECT, MASTER_SPEC, STATE, TASKS, HANDOFF, IMPLEMENTATION_PLAN, DECISIONS,
`docs/security.md`, and the model-settings contract and implementation. No AGENTS.md was found
in this assignment workspace or its parent assignment-workspaces tree. Older deployment/pending
assertions are historical, not authority to operate shared runtime or unrelated held work.

Inspected `model_settings.py`, `model_families.py`, `providers.py`, `claude_host.py`, web endpoints,
actual HTML/JS/CSS and current behavioral/browser fixtures. The repository supports Codex
Luna/Sol/Astra and Claude Haiku/Sonnet/Opus, resolving supported exact versions from the provider
catalog; Claude's current host catalog uses `claude-{haiku,sonnet,opus}-5-5` and Low/Medium/High.
No native inference was needed or launched. Tests use explicit catalogs, never fabricated claims
about current account availability.

## Delivered behavior

The authoritative feature contract is [MODEL_SETTINGS.md](MODEL_SETTINGS.md). Manager displays
both providers with evenly spaced model/effort rows, no panel logos and the Close button on its
heading line. Bot mode controls share its heading line and operate the active provider's independent
settings. Manual uses two plain-name rows. Automatic uses direct model toggles and three aligned
per-model effort columns, preserving independent allowances and backend nonempty-policy enforcement.

All selected effort surfaces exactly match unselected surfaces; only text becomes the existing accent.
Model buttons retain selected backgrounds. Keyboard focus is separate. Manager/Bot/Favorites headings
match in color, typography and subtle appearance. Favorites has two global Manager-only slots,
far-left Insert buttons and effort-before-model recall entries; no dropdown/editor. SQLite settings,
audit events, existing authentication/CSRF and model-family validation are reused. Provider activation
and role settings save atomically; favorite recall does not change Bot choices. Unsupported obsolete
favorites remain unavailable; supported family updates resolve without effort substitution.

## Verification record

All commands ran sequentially in this isolated workspace. Python/dev tools came from the existing
`/home/codex/projects/Linko-Bot-Manager/.venv/bin` through PATH; no environment/dependency changes
were made. Test servers used temporary directories and disposable dynamically allocated loopback ports.

| Command | Final result |
| --- | --- |
| `python -m pytest -q -m smoke` | **37 passed, 281 deselected**, 3.68s; run once |
| `python -m pytest -q tests/test_model_settings.py tests/test_claude_provider.py` | 17 passed, one new assertion failed; affected test repaired below |
| `python -m pytest -q tests/test_model_settings.py::test_manager_favorites_persist_recall_across_providers_without_bot_changes` | **1 passed**, 0.88s; all 18 targeted cases now passed across the recorded runs |
| `ruff check ai_manager/model_settings.py ai_manager/web.py tests/test_model_settings.py scripts/browser_model_scroll.py scripts/browser_smoke.py` | **All checks passed** |
| `ruff check tests/test_model_settings.py` after assertion correction | **All checks passed** |
| `node --check ai_manager/static/model-settings.js` | **Exit 0** |
| `LINKO_MODEL_EVIDENCE="$PWD/.data/model-settings-evidence" python scripts/browser_model_scroll.py` | **Passed**, no page errors |
| `LINKO_CHAT_EVIDENCE="$PWD/.data/model-settings-evidence/chat-smoke" python scripts/browser_smoke.py` | **Passed**, no page errors/overflow |
| `git diff --check` | **Passed** |

There is no JS build step/package manifest: the app serves plain browser assets. The full backend
suite was deliberately left to the normal deployment script, per the task contract.

Two repair rounds conservatively recorded, one remaining:

1. Initial targeted command mistakenly included nonexistent `tests/test_model_families.py`.
   Output: `no tests ran in 0.00s; ERROR: file or directory not found: tests/test_model_families.py`.
   Removed that path; relevant family tests already reside in the existing model-settings module.
2. Targeted run: `1 failed, 17 passed` in 2.78s. New invalid-slot test expected HTTP 400;
   actual app-wide ValueError convention is HTTP 409 (`Choose favorite slot 1 or 2`). Corrected
   the assertion, reran only that affected test, and passed. No product fix was required.

The only warning was the preexisting FastAPI/Starlette TestClient deprecation concerning httpx.
Browser logs also include two intentional rejected last-allow removals:
`Allow at least one supported model and level combination`. These are successful rejection checks.
The smoke suite and passing browser checks were not rerun.

## Browser evidence and practical limits

Chromium verified both Manager provider selections; Manual model/effort restoration; direct Automatic
model toggle on/off; restricted Luna High-only and Astra Low-only policies; actual saved API policy;
rejected removal of the last supported model/effort pair; two empty slots, immediate Insert replacement,
Manager-only recall across providers, reload persistence; and unchanged Bot snapshots for both providers.
Backend tests additionally verify authentication/CSRF, invalid slots, revision/activation atomicity,
family-version updates, missing families/efforts, and rejected unavailable recall without changing settings.
Existing targeted execution tests verify manual override, permitted delegation, actual fake-provider turn
parameters and pinned assignment continuation. No production inference/execution was triggered.

| Viewport | Panel client/scroll width | Panel client/scroll height |
| --- | --- | --- |
| 1200×900 | 418 / 418 | 504 / 504 |
| 393×851 | 359 / 359 | 504 / 504 |
| 320×568 | 302 / 302 | 498 / 498 |

All controls fit these viewports without horizontal overflow or panel scrolling. Shorter screens retain
normal vertical modal scrolling. Captured screenshots at desktop and 320px were visually inspected.
Matching selected/unselected computed effort values: background `rgb(32,39,52)`, image `none`,
border `rgb(43,51,69)`, shadow `none`, weight `400`. Selected text `rgb(255,130,150)`;
other text `rgb(142,155,178)`. All three heading colors match the latter. Header center alignment,
far-left Insert position, no panel SVG/select/checkmark cells, arrow navigation, Enter selection,
Tab/Shift+Tab containment, Escape and opener focus restoration passed. Focus outline is independently solid.

The retained browser script also checks no scroll jumps during sends, streaming, reconciliation, history
tail-window changes, Markdown rebuilds and view switching across all three viewport sizes. The broader
browser smoke covers login, chat, selection preservation, all-425/five-message copy, clipboard fallback,
archive/search, rename, model roles, recovery information and desktop/mobile/narrow controls.

Raw command stdout/stderr, failed attempts, computed geometry/styles, scroll samples and screenshots:
`<workspace>/.data/model-settings-evidence/` (retained private per-workspace outputs). Files include
`smoke.txt`, `targeted.txt`, `targeted-repair-1.txt`, `targeted-repair-2.txt`, `lint.txt`,
`lint-after-repair.txt`, `browser.txt`, `browser-smoke.txt`, `browser-results.json`,
`panel-{1200,393,320}.png` and `chat-smoke/`. Browser fixture databases were temporary and removed
on normal completion. No physical Android/iOS testing or live auto-deployment success is claimed.

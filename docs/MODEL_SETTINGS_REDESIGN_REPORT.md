# Authorized bounded repair continuation — 2026-10-15

**RESULT: FAILED — startup probe hangs in pending-permission mock setup
(`scripts/probe_recording_start.py:102`, `page.evaluate(...)`).** The recording timing assertions and
full recording/model/chat browser suites pass, but the complete startup probe does not finish.
Candidate remains on the same retained assignment branch; no merge/deployment was performed or claimed.

The latest owner verification policy explicitly expanded the limit from three to **five total rounds**,
with three already used and two remaining. The historical three-round counter was preserved, not reset.
Used rounds four and five below. **5 rounds used, 0 remaining.** Stopped implementation repairs after
round five stalled; no replacement task, reviewer, parallel work, source-branch switch or scope change.
No production/shared data, checkout, services, paused/cancelled/held work or native inference was touched.
Base for this continuation: retained failed candidate `1e086d4`. Application code is unchanged here;
only probe instrumentation and truthful documentation are updated.

## Round four: correct causal instrumentation

The previous probe attached its `start` listener in patched `MediaRecorder.start`, after the app had
already installed its `onstart` handler. App mutations could be observed before that later listener,
so its timestamp did not establish the beginning of native event dispatch.

The probe now wraps recorder construction and installs native `start`/first-data listeners before the
app can install its handlers. It also records the current app phase at each event. Verification keeps
the timestamp inequality and adds the stronger assertion that phase is still **starting** when native
start dispatch is first observed. No application source or desired behavior was relaxed.

Command:
`python scripts/probe_recording_start.py --verify --output .data/model-recording-repair/start-after.json`.
The start/phase assertions passed in both scenarios. The run then failed with
`KeyError: 'config-response'`: the probe read its events immediately after the first chunk, which the
improved client can produce before the deliberately delayed metadata response. Partial event data
is now written before assertions so failures retain observations. The round-four event data was copied
to `start-round-4.json`, and the complete traceback remains in `probe-round-4.txt`.

## Round five: wait for observations, then stop at another harness defect

Added a probe-only wait for its configuration-response event before collecting/comparing timestamps.
This changes no application scheduling and preserves every native-start, first-sample and user-gesture
assertion. Both delay scenarios then passed all those assertions and persisted their complete data.

The probe subsequently stalled at its extra privacy-case setup:

```python
page.evaluate("window.originalCapture=navigator.mediaDevices.getUserMedia;navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.grantLater=resolve)")
```

The hang is at that setup expression, **before** the case's recording tap or pagehide dispatch. It is
not evidence of the application holding a microphone after pagehide. The expression's final value is
a function that returns an unresolved permission promise; evaluator handling of that return value is
a plausible harness cause. This was not repaired or waived with the budget exhausted. After more than
two minutes without completion, SIGINT was sent only to the exact owned probe process, preserving its
stack. Browser/context and temporary server cleanup followed. The interruption produces Playwright
TargetClosed/pending-task diagnostics; these are harness shutdown output, not app page errors.
No probe retry, deletion of the case, assertion relaxation or sixth repair round occurred.

## Measured recording behavior

Final corrected observations in `start-after.json` (Chromium fake audio device/auto-granted permission):

| Event after deliberate tap | Normal local metadata | Metadata delayed by 600ms |
| --- | --- | --- |
| Permission request | 13.7ms | 1.1ms |
| Starting UI observation | 17.2ms | 1.6ms |
| Stream ready | 72.8ms | 15.0ms |
| Native recorder start observed, app phase still starting | 133.7ms | 76.1ms |
| Recording UI observation | 134.8ms | 77.3ms |
| First recorded chunk | 377.8ms | 316.4ms |
| Configuration response | 52.5ms | 615.6ms |

For the preceding baseline's delayed scenario, permission was requested at 629.9ms and the first
chunk arrived at 944.1ms. The corrected candidate observations demonstrate that the metadata network
roundtrip no longer gates permission or initial capture, and Recording UI follows native start.
These are individual local fixture runs, with warm-device/order effects and a 250ms chunk timeslice,
not a device benchmark or physical-mobile improvement guarantee. Both permission requests retained
user activation. Physical OS permission/device/encoder startup remains unmeasured and browser-dependent.
Full end-to-end probe acceptance remains incomplete because its final setup hangs.

## Remaining initial browser verification completed (not repeated)

These checks were left unrun when the prior continuation exhausted its original budget. They ran once
here, sequentially, on disposable temporary fixtures and ports:

| Command | Result |
| --- | --- |
| `LINKO_AUDIO_EVIDENCE="$PWD/.data/model-recording-repair/audio" python scripts/browser_audio.py` | **passed**, real Chromium capture, fixture provider only, **28 uploads**, **6 chat submissions**, widths **1440/393/320** |
| `LINKO_MODEL_EVIDENCE="$PWD/.data/model-recording-repair/models" python scripts/browser_model_scroll.py` | **passed**, settings persistence/policy/mobile/keyboard and manual scrolling during sends/streams/polling |
| `LINKO_CHAT_EVIDENCE="$PWD/.data/model-recording-repair/chat" python scripts/browser_smoke.py` | **passed**, login/chat/copy/archive/search/rename/model/recovery/responsive behavior, no page errors/overflow |
| `ruff check scripts/probe_recording_start.py` | **All checks passed!** |
| `node --check ai_manager/static/app.js`, `model-settings.js`, `audio.js` | **exit 0**, final JS including the metadata-dependent Upload guard |
| `git diff --check` | **passed** |

The recording suite covers microphone denial, canceled permission/late tracks, unavailable metadata
cleanup, draft/context preservation, discard/retry/checkpoint behavior, audio uploads and limits, busy
manager/queued explicit Send, and recording Send with attachments. Model checks retain both functional
Bot provider groups, direct Automatic allowances/nonempty rejection, exact effort/mode surfaces,
favorites insert/derived highlight/recall and mobile focus/layout. The intentional last-allow errors
and preexisting Starlette/httpx deprecation warning remain expected outputs.

Previously passing results retained without repetition: **37 smoke tests** and **73 targeted backend
tests**, lint of all changed Python files, plus the async favorite browser suite covering immediate
close/focus, actual persisted fast-Send route, duplicate prevention, stale/unavailable/network failures,
draft retention and reopened-modal races. No backend/application change in this continuation justified
repeating them. The full backend suite remains reserved for deployment.

Durable evidence directory:
`/home/codex/.local/share/linko-bot-manager/assignment-workspaces/4a985c35b51345ada354cd666acbf80e/.data/model-recording-repair/`.
Files: `probe-round-4.txt`, `start-round-4.json`, `probe-round-5.txt`, `start-after.json`,
`browser-audio.txt`, `browser-model-scroll.txt`, `browser-smoke.txt`, `lint.txt`, and screenshot/results
subdirectories `audio/`, `models/`, `chat/`. Prior raw evidence under `.data/model-recording-ux/` is
unchanged. Reports below are historical; this section is the current acceptance and repair record.

---

# Immediate favorite recall / recording startup — 2026-10-15

**RESULT: FAILED — `scripts/probe_recording_start.py --verify`: assertion
`times['ui-recording'] >= times['recorder-start-event']`.** No normal merge/deployment is warranted
for this candidate. The same assignment/workspace/branch/thread is retained; no replacement task,
reviewer or parallel worker was created. Durable prior budget: **3 rounds used, 0 remaining**,
preserved despite stale machine metadata. After the first failing new check, implementation repairs
and remaining browser runs stopped. No further repair round was consumed or counter reset.

Base: `f8f4d55` in retained branch `linko/assignment-4a985c35b51345ada354cd666acbf80e`.
The owner describes that base as deployed. This worker did not inspect production or independently
establish that receipt. No shared checkout/database/service write, native model inference, deployment,
push, unrelated task resume/cancel, independent review or parallel work occurred. This failed candidate
is committed only in its assigned branch for durable retention, not successful automatic delivery.

## Implemented source and verified subset

Inspected authoritative project/spec/security/model/audio docs, retained prior evidence, real current
HTML/JS/CSS, provider/catalog validation, favorite endpoints, audio metadata/transcription code,
chat submission/replay and existing browser fixtures. No applicable workspace AGENTS.md was present
in the preceding inspections; all work stays in the same retained assignment scope.

Favorite recall closes the dialog and focuses its opener in the synchronous click turn, before starting
the network request. Insert remains open and copies the current Manager selection. One browser intent
queue now orders model writes, settings reads and owner chat acceptance (ordinary/parallel/recording
Send and To-Do Start). Fast Send captures its draft once and waits for pending selection, preventing
silent prior-model submission. HTTP acceptance still pins the actual Manager pair in the backend.
Duplicate clicks while pending are ignored. A failed/unconfirmed selection shows an external notice,
refreshes actual saved settings and blocks Send until an explicit settings choice succeeds; blocked
messages retain draft/attachments and do not remain as ambiguous submitted requests. Reopened settings
stay disabled until their ordered read; closed pending opens invalidate late read responses.

Recall uses a 15-second client deadline plus expected favorite provider/resolved pair/settings revision.
The optional expected body is validated server-side, preventing a late recall from overwriting a newer
confirmed settings selection or a replaced favorite. Existing bodyless recall API calls remain compatible.
A successful mutation clears the selection barrier; a read alone never claims an uncertain save succeeded.

**Passed browser subset:** synchronous close/opener focus, duplicate suppression, a deliberately held
recall followed by fast Send and reopened modal, actual persisted execution using Luna/High despite a
subsequent Astra selection, independent Bot settings preservation, simulated stale/unavailable/network
recall errors, external notices, unsent retained drafts, reconciled saved choices, canceled delayed opens,
and successful explicit recovery/send. No page errors. The temporary fixture's SQLite execution record
was read to verify the actual accepted model/effort, not just a local pressed state.

## Recording investigation and source change (acceptance incomplete)

`startAudio` previously rendered a Starting message, **awaited GET `/api/audio`**, then invoked
`getUserMedia`, constructed MediaRecorder and marked the UI Recording immediately after `start(250)`.
Inspection of `Audio.status` shows only local settings checks and `shutil.which(ffprobe)`: no STT,
provider inference, media upload or device initialization. The extra network gate was avoidable.

A baseline local Chromium probe used the real MediaRecorder API with Chromium's fake audio device and
permission auto-grant. It instrumented tap, Starting DOM mutation, permission request, stream resolution,
recorder start call/event and first data. An injected 600ms delay applied only to `/api/audio`:

| Baseline event after tap | Normal local response | Injected 600ms response delay |
| --- | --- | --- |
| Starting UI observation | 9.6ms | 1.7ms |
| Permission request | 35.2ms | 629.9ms |
| Stream ready | 72.4ms | 640.8ms |
| Recorder start call | 72.6ms | 640.9ms |
| UI Recording observation | 74.1ms | 643.7ms |
| Recorder start event observation | 134.3ms | 704.0ms |
| First recorded chunk | 379.5ms | 944.1ms |

These are two local fixture samples, not a distribution, physical-phone timing, real permission UI timing
or a device-speed claim. The injected delay demonstrates the serialized metadata roundtrip. The first
chunk includes the configured 250ms recorder timeslice; that is not all microphone initialization time.
Browser permission interaction, OS/device acquisition and encoder startup remain outside app control.

The source now requests permission inside the tap gesture while fetching metadata alongside it. It
starts the recorder immediately on stream acquisition and retains opening chunks while configuration
is pending, rather than holding a live stream without recording. Starting feedback is immediate;
Recording/timer begin inside the recorder's `onstart` handler. Configuration must succeed before STT.
Existing configuration read timeout is 15 seconds; pending device/recorder startup is bounded at 30
seconds. Audio Upload waits for metadata so its size/context checks never read an absent config.
There is no prewarming or unsolicited capture. On an explicit tap, an unavailable provider may now
briefly request permission before metadata rejection; failure stops acquired and late-arriving tracks.

Starting-session Discard, auth/view change, hidden/pagehide/freeze invalidates capture; late permission
results stop tracks. Existing recording interruption warnings, stop/Send/Upload, whole-file transcription,
draft/context fencing, IndexedDB recovery and limits remain in the source. Physical Android/iOS,
permission prompts, hardware startup, mobile keyboard and background suspension were not measured.

**Candidate recording verification failed before completing its timing report.** The `--verify` probe
asserted that its observed UI Recording timestamp must follow its observed recorder-start timestamp.
The source does update UI in `onstart`; the probe registers its separate event listener from the patched
`MediaRecorder.start` call, after that handler has already been installed. Listener/mutation-observer
ordering may therefore affect the timestamps. This is an inference from source inspection, not a
verified diagnosis or waiver. No instrumentation fix, assertion relaxation or failed-check rerun was
performed because the durable repair budget is exhausted. No candidate timing improvement, complete
recording lifecycle acceptance or physical-device performance claim is made.

## Exact validation record

All runs were sequential in the assigned workspace, with the existing development environment via
`PATH=/home/codex/projects/Linko-Bot-Manager/.venv/bin:$PATH`. Disposable fixture data/projects and
loopback ports were used. No full backend suite or network/paid provider inference was run.

| Command | Recorded output/result |
| --- | --- |
| `python scripts/probe_recording_start.py --output .data/model-recording-ux/start-before.json` | Baseline investigation completed; full events/timings retained |
| `python -m pytest -q -m smoke` | **37 passed, 283 deselected**, 2.88s; once |
| `python -m pytest -q tests/test_model_settings.py tests/test_claude_provider.py tests/test_audio.py tests/test_chat_queue.py` | **73 passed**, 36.90s; once |
| `ruff check ai_manager/model_settings.py ai_manager/web.py tests/test_model_settings.py scripts/browser_model_recall.py scripts/probe_recording_start.py scripts/browser_audio.py scripts/browser_model_scroll.py scripts/browser_smoke.py` | **All checks passed!** |
| `node --check ai_manager/static/app.js` / `model-settings.js` / `audio.js` | **Exit 0**, no output (before final pre-probe Upload metadata guard) |
| `LINKO_RECALL_EVIDENCE="$PWD/.data/model-recording-ux" python scripts/browser_model_recall.py` | **Passed**, immediate close/focus, ordered actual Send, failures/reconciliation/reopen races; once |
| `python scripts/probe_recording_start.py --verify --output .data/model-recording-ux/start-after.json` | **FAILED**, UI/start-event timestamp assertion; no candidate JSON produced |
| `scripts/browser_audio.py`, `scripts/browser_model_scroll.py`, `scripts/browser_smoke.py` | Not run this continuation after the failed probe; earlier passed runs remain historical |

Before the failing probe, ordinary implementation inspection added the Upload metadata guard and a
render when config arrives. This did not follow a failed check. No source fixes followed probe failure.
Only status/evidence documentation was then updated and the failed candidate retained in Git.
The preexisting FastAPI/Starlette httpx deprecation warning remains. The static app has no build step.

Raw command outputs and test evidence are in the retained private directory:
`/home/codex/.local/share/linko-bot-manager/assignment-workspaces/4a985c35b51345ada354cd666acbf80e/.data/model-recording-ux/`.
Files: `probe-before.txt`, `start-before.json`, `smoke.txt`, `targeted.txt`, `lint.txt`,
`recall-browser.txt`, `recall-results.json`, `closed-recall.png`, `probe-after.txt`.
The latter preserves the exact failing assertion/traceback. Temporary fixture databases were removed
on normal cleanup. A failed report must block normal automatic merge/deployment; no new deployment
success is claimed. Feature docs describe source intent while this report controls acceptance status.

---

# Owner refinements — 2026-10-15

**PASSED isolated candidate**, continuing the same assignment/workspace/branch/thread over
`5c6e35bd24` (full prior commit is in Git). The owner reports that prior candidate deployed;
this worker did not inspect shared runtime or independently establish its deployment status.
The refinements below are committed for application-managed automatic delivery. They are not a
claim that the new candidate has merged/deployed. The original implementation report follows below
and remains historical evidence, including its durable repair counter and narrower first layout.

## Refinements and design

Read the retained source/feature contract, project/security/task/handoff instructions and original
report; inspected the pre-redesign CSS at `ca0576c`. Kept the original 38px choice minimum height,
6px vertical padding, 36px Close minimum and its existing 9px rounded border/distinct default
background. Manual/Automatic now share effort controls' text-only selected style and independent
focus outline. Main model rows remain free of provider logos.

Both Bot provider groups are always rendered in either displayed mode. Manual has separate functional
model/effort rows for Codex and Claude; Automatic has model toggles and independent supported effort
allowances for both. Edits save to their respective provider settings without changing the Manager's
active provider. This preserves the existing routing rule: new work uses the active provider's saved
Bot configuration; pinned work retains its original exact provider/model/thread. The shared mode
selector updates both available providers in one SQLite transaction, checking each provider revision;
a stale second provider rolls back the first write and audit events. Completely unavailable provider
groups remain disabled and are skipped by mode writes. Legacy mixed saved modes initially display
the active provider's mode; editing the other provider also saves that displayed mode for its Bot.
PUT `/api/bot-mode` uses the existing authentication/CSRF protections and accepts `mode` plus both
provider `revisions`. No schema, new routing framework, service or dependency is introduced.

Favorite `aria-pressed` and selected gradient/border derive from availability plus the active Manager
provider, resolved model and effort on every render. Insert, recall, other Manager selectors, reload
and replacement all recompute that match; no last-click marker is stored. The current favorite's
surface matches the selected model button's surface. Effort/model text stays centered with an 8px
readable gap; existing `PROVIDER_LOGOS` SVGs appear at the far right with a balanced opposite spacer.
The far-left Insert still copies the current Manager selection immediately. Favorite storage/validation,
obsolete-family handling and Manager-only recall semantics are unchanged. Main provider rows have no
SVGs. Keyboard wrapping/arrow movement scroll focused controls into view; reopening starts at the top.

Feature authority updated in [MODEL_SETTINGS.md](MODEL_SETTINGS.md) and MASTER_SPEC.
No unrelated paused, cancelled or held work was resumed, no parallel workers or reviewer were used,
and no shared checkout/database/service was changed. No push or deployment was performed.

## Verification, commands and durable repair accounting

Same retained workspace:
`/home/codex/.local/share/linko-bot-manager/assignment-workspaces/4a985c35b51345ada354cd666acbf80e`.
Raw stdout/stderr and browser evidence are retained under `.data/model-settings-refinements/`.
Commands ran sequentially, using the existing development environment via
`PATH=/home/codex/projects/Linko-Bot-Manager/.venv/bin:$PATH`. Disposable server/database/project
fixtures used temporary directories and dynamically allocated loopback ports. No native inference.

| Command | Output/result |
| --- | --- |
| `python -m pytest -q -m smoke` | **37 passed, 282 deselected**, 2.84s; once for these refinements |
| `python -m pytest -q tests/test_model_settings.py tests/test_claude_provider.py` | **19 passed**, 2.73s; once |
| `ruff check ai_manager/model_settings.py ai_manager/web.py tests/test_model_settings.py scripts/browser_model_scroll.py scripts/browser_smoke.py` | **All checks passed!** |
| `node --check ai_manager/static/model-settings.js` | **Exit 0**, no output |
| `LINKO_MODEL_EVIDENCE="$PWD/.data/model-settings-refinements" python scripts/browser_model_scroll.py` | Initial new Close geometry assertion failed; see repair below |
| Same browser command, affected check after repair | **Settings persistence/policy/mobile/keyboard and manual scroll during sends/streams/polling passed** |
| `LINKO_CHAT_EVIDENCE="$PWD/.data/model-settings-refinements/chat-smoke" python scripts/browser_smoke.py` | **Browser passed**, no page errors/overflow; once |
| `ruff check scripts/browser_model_scroll.py` after repair | **All checks passed!** |
| `node --check ai_manager/static/model-settings.js` after keyboard repair | **Exit 0**, no output |
| `git diff --check` | **Passed** |

Durable counter preserved conservatively: prior report **2 rounds used, 1 remaining**, despite the
new request's machine metadata saying 1 used/2 remaining. Used only that remaining round here:

- Browser first run stopped at `assert close_style == ['rgb(29, 37, 52)',
  'rgb(43, 51, 69)', '9px', 36]`. The source restores the *minimum* 36px height; inherited 22px font
  and line height make actual height slightly taller. Corrected the test to retain exact background,
  border and radius checks and assert height **at least 36px**. Also made keyboard wrapping/arrow
  focus scroll into view and reopening reset modal scroll, needed for the now taller mobile panel.
- Reran only the affected browser script and its lint/JS syntax checks; all passed. Smoke, targeted
  backend and broader browser smoke were not repeated. **3 durable rounds used, 0 remaining**.
- The preexisting Starlette/httpx deprecation warning remains. Two logged empty-policy rejections
  are intentional passing checks: `Allow at least one supported model and level combination`.

Full backend suite remains reserved for the deployment script, per owner contract. No JS build step
exists: assets are plain HTML/CSS/JS.

## Behavioral and visual evidence

Backend tests now also cover authenticated/CSRF-protected shared mode saves, rollback on a stale second
provider revision, independent restricted Codex/Claude automatic routing (permitted High accepted,
denied Low rejected), and unchanged retained Manual pairs after switching both modes. Existing tests
cover saved favorites, family/catalog support, unavailable favorites, pinned execution/delegation and
Manager-only recall.

Real Chromium tests both Bot groups' Manual selections and effort updates, both groups' Automatic
model/effort toggles, and the actual saved API policies without changing active Manager provider.
They verify favorite highlight after Insert/recall, clearing after a different effort/model/provider,
restoration when the same pair is selected elsewhere, replacement and reload persistence, and unchanged
Bot choices through recall. Computed mode surfaces match unselected backgrounds/borders/shadows/weight;
effort comparisons retain the prior exact values. Favorite selected gradient and border exactly match
the selected Manager model. Close background/border/radius and comfortable choice heights pass.
Both headings stay aligned with their controls, all label colors match, and main rows contain no logos.
Exactly two favorite SVGs appear after filling slots, with centered label geometry and right padding
between 5px and 12px. Far-left Insert, no dropdown/checkmark rows, keyboard focus outline/containment,
visible wrap target, Enter/arrow/Escape and opener restoration pass.

| Automatic viewport | Client / scroll width | Client / scroll height |
| --- | --- | --- |
| 1200×900 | 418 / 418 | 742 / 742 |
| 393×851 | 359 / 359 | 742 / 742 |
| 320×568 | 302 / 302 | 550 / 734 |

Both Manual and Automatic were checked at all three sizes without horizontal overflow. The 320px
panel uses normal vertical scrolling to preserve comfortable controls; focused wrapped controls scroll
into view. Desktop and mobile-bottom screenshots were visually inspected. The broader browser smoke
also passed login/chat/history/copy/archive/rename/recovery checks. Existing manual chat scroll checks
passed sends, streams, refreshes, history-tail changes and Markdown rebuilds at all three viewport sizes.
Physical Android/iOS testing and new live deployment success are not claimed.

Evidence files: `smoke.txt`, `targeted.txt`, `lint.txt`, `lint-after-repair.txt`, `js-syntax.txt`,
`js-syntax-after-repair.txt`, `browser.txt`, `browser-repair-3.txt`, `browser-smoke.txt`,
`browser-results.json`, `panel-{1200,393,320}.png`, `favorites-{1200,393,320}.png`,
`manual-{1200,393,320}.png`, and `chat-smoke/`. Test fixture runtime data was disposed normally;
reports/screenshots/logs remain in this retained assignment workspace.

---

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

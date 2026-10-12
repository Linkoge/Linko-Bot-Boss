# Combined cleanup / compact composer candidate — prepared only

Exact release ID: `chat-ui-composer-v3-20261012`; candidate checkout:
`/home/codex/checkouts/linko-compact-composer-20261012`.

Extends the drained completed assignment `05ca122859d548adba73bd99f1f35176` and its
verified `chat-ui-cleanup-v2-20261012` candidate; no edits to that checkout or immutable
receipts. Preserves hidden caret, role styles/accessibility without avatars/speaker labels,
no ordinary Completed badges, compact utility menu immediately left of usage, all three
mode choices and original utilities. Preserves succeeded `queue-composer-v1-20261012`
FIFO, busy Send/Record, mode/origin binding, retry IDs, Enter/newline, internal result filtering.

Composer is 154px including padding/border, with a scrolling text area and three equal
44×44 buttons vertically at right: **+ file/photo**, **Send**, **Record**. Recording has
explicit Stop (transcribe into draft), Send (stop/transcribe/submit), Discard and available
audio Download; controls/status stay inside the compact input area. Recording square also
stops or retries, with state-specific accessible names. Draft attachments show removable,
owner-authenticated download links; messages and archived history retain download links.

Uploads are real owner-only, CSRF-protected raw-byte uploads, scoped to the exact project
and conversation. Four per message, 8 MiB each, 512 MiB retained storage cap. Empty files,
unsafe names, wrong chat scope and invalid photos reject without upload success. PNG/JPEG/WebP
have signature/ffprobe format and 20 megapixel checks; HEIC/SVG/other photo formats require
conversion. Other files remain bytes for native tools to read; there is no promise that any
arbitrary binary file is understood. Generated immutable private paths/checksums live under
`DATA/chat-workspace/attachments`; existing settings store metadata, so no schema/dependency
migration. Chat intake stores execution attachment references atomically, binds them to
request-id replay, sends photos as native `localImage` input, and automatically forwards
references and images to delegated/continued workers. UI messages show names, not internal
paths or transport instructions. Native context recovery can retrieve historical file references.

Storage is retained on clear/archive/remove-from-draft; these actions do not erase files.
No automatic upload garbage collector or delete endpoint is introduced. Storage-full errors
are explicit. Daily backups now include checksum-verified companion attachment directories;
operators must preserve them with DB/native history. See operations for restore/retention limits.

Verification includes `tests/test_attachments.py`, delegated-worker native input fixture,
`scripts/browser_composer.py` and existing cleanup/queue/audio/reliability suites, all required
by trusted preparation. Final exact results, screenshots, limitations, commit/assets and release
manifest are in [the full durable report](/home/codex/.local/share/linko-bot-manager/deployments/compact-composer-20261012/REPORT.md). Chromium covers desktop 1440×1000,
Android-sized 393×851 and narrow 320×420. Paid inference/STT and physical Android are not
asserted. No deployment requested or performed; only an exact durable succeeded deployment
receipt establishes activation. Existing queue baseline succeeded receipt is
`deployment-helper/results/95c21194ebf3e4e941debba157928521.json`.

---

# Chat UI cleanup — prepared, not deployed, 2026-10-12

Exact release ID: `chat-ui-cleanup-v2-20261012`. Candidate checkout:
`/home/codex/checkouts/linko-chat-cleanup-20261012`.
Current deployed baseline is `queue-composer-v1-20261012`, commit
`f2614893ce4ed58737cc6e9fdbfed62569a1c522`. Its immutable succeeded helper receipt is
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/95c21194ebf3e4e941debba157928521.json`.
That receipt supersedes the historical queue release's prepared-only wording below.
This cleanup is **implemented and verified; prepared only, activation pending**.
No deployment request, restart or new succeeded receipt is created by this assignment.

The textarea uses `caret-color: transparent`; native input focus/editing/selection, Enter
newlines, IME and accessibility label remain. Composer focus gets a visible border cue.
Live and saved message rows omit decorative avatars and visible author headings, reclaiming
the former avatar column. Existing role classes and palette distinguish user panels from
assistant text; accessible article names and copy export author labels remain.

Ordinary messages have no visible Completed badge. History status derives from manager
execution/stream completion, not accepted requested-task delivery. It contains no reliable
message-level acceptance/activation semantic signal, so this release omits Completed badges
entirely instead of guessing from prose, roles, turn termination or task lifecycle alone.
Queued, processing/streaming, waiting/retry, failed and stopped indicators remain. Content,
including blockers and pending activation reports, is not shortened or rewritten by this UI.

One 44×44 square, using the existing project menu visual language, sits immediately left of
usage percentages. Its menu exposes Clear, Archive, Copy 5, Copy all and each supported mode
as a radio item with current selection. Options derive from the existing single mode source;
Auto remains default, scope remains per submitted request/recording capture, and changing
mode does not rewrite queued/retry requests or captured audio context. Project/global context
switching follows the existing behavior. Disabled utilities retain their original guards.
Keyboard Enter/Space, arrows, Home/End, Escape and Tab, pointer/touch selection, outside
close and focus return are supported. The menu scrolls in short/narrow viewports and sits
above transient notices. Existing usage and project menus remain accessible.

No backend/schema/dependency or queue/audio logic changed. FIFO/request receipts, internal
transport filtering, native multiline input, 20-minute recording, recording/sending while busy,
queued messages, Stop and deployment admission/drain behavior remain. No new pause/cancel UI.

Validation: all 199 backend tests passed; Ruff, JS syntax and Git whitespace checks passed.
Authenticated disposable Chromium checks cover menu geometry/keyboard/touch at desktop,
mobile/narrow and short viewport sizes; caret-hidden editing/selection; three mode choices;
copy all 425 messages, Copy 5 and clipboard fallback; archive/clear/search; role accessibility;
ordinary completion badge omission; queue/busy recording, captured mode and submission retry;
Markdown/stream/reload/selection; internal result filtering and the existing regression browsers.
Physical mobile keyboards, assistive technology and real-provider STT were not exercised;
Chromium uses fixture Codex/STT and real browser audio capture. Existing Starlette/httpx
warning remains. Full release preparation repeats the required checks and records their output.

Manager routine brief/recent delivery facts and full evidence:
`/home/codex/.local/share/linko-bot-manager/deployments/chat-ui-cleanup-20261012/REPORT.md`.
Exact manifest, validation log and hashed bundle:
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/chat-ui-cleanup-v2-20261012/`.
A future owner deployment request must use the controlled helper and recheck the live base;
only an exact durable succeeded receipt may establish activation. Preserve production data,
active work, prior release receipts and unrelated changes.

# Nonblocking composer and active-first views — prepared, 2026-10-12

Exact integrated release ID: `queue-composer-v1-20261012`. Candidate checkout:
`/home/codex/checkouts/linko-nonblocking-chat-20261012`.
**Prepared only; not activated. No deployment request or succeeded receipt for this release.**
Includes `audio-composer-v3-20261012` (20-minute recording and compact composer) and
internal-result fix `0675150`; their immutable prepared releases remain historical evidence.
The three related assignments `2c7f9687a2fd49259468c5838a512c19`,
`4c8eab2e6b554fb7b757d5bb84cf2bb0`, `11efb3bb804d473fa211da4baa74e272`
were inspected read-only: completed, no lease. Shared executable files/service were untouched.

Tasks group running, queued, waiting, then terminal history; each group uses descending creation
and ID for stable ties. Agents use the existing confirmed-work predicate first, then unfinished
and completed history with descending queue time/ID; active records are selected before the
100-record window. Existing archived-task policy and full execution history remain.

Textarea Enter uses native multiline editing, including IME; no keyboard Send shortcut.
Send and microphone startup no longer depend on manager inference activity. Recorder startup,
Stop/transcription/upload/Send phases, pending HTTP requests, ambiguous submission retries,
origin project/conversation/mode, saved drafts and retained audio/transcript retry still guard
admission. Recording Send preserves the whole-file Stop → transcribe → submit flow.

Owner messages commit immediately to existing messages/executions/request receipts. SQLite
BEGIN IMMEDIATE and insertion rowid define acceptance FIFO per conversation, independent of
wall-clock ties. Only the earliest unfinished manager execution may launch or recover; later
requests remain queued through active work, questions and head backoff. Failure/explicit Stop
finishes only the affected execution. One-second scheduler passes drain after terminal completion.
The existing exclusive manager process flock plus synchronous job registration prevents duplicate
runners; a finished receipt does not release the old runner's native-thread handlers prematurely.
The next runner reloads the conversation's latest thread reference. Native-history reconciliation,
account gates, permissions, per-request mode and deployment drain gates remain in force.
No new schema, dependencies, provider traffic, deployment request, cancellation or restart.

Cold prompt history and the manager read_history tool exclude future queued inputs. Current owner
messages show queued/processing/waiting/completed/failed/stopped states from their executions;
assistant queued placeholders remain compact. Chat display follows committed message insertion
order. Request IDs deduplicate ambiguous retries, including concurrent transactions and restart;
legacy API callers without a request ID still lack retry deduplication. Pending requests remain
bound to their captured context and mode; a different payload under the same ID is rejected.
No guarantee of exactly-once arbitrary external side effects is introduced.

Verification uses disposable SQLite/native-turn fixtures and real Chromium with fixture-only STT.
Coverage includes older active records before recent history; multiline Enter/IME/no shortcuts;
busy recording/whole-file Send; double click and queued display; FIFO, equal timestamps,
concurrent receipts, crash before launch, restart, head retry/permanent failure, conversation
isolation, completion/cleanup races, future-history exclusion and unchanged deployment mode gate.
Full release validation log/manifest/bundle are private under
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/queue-composer-v1-20261012/`.
Detailed report and manager routine brief/recent-delivery reference:
`/home/codex/.local/share/linko-bot-manager/deployments/nonblocking-chat-20261012/REPORT.md`.
Only a future durable **succeeded** helper receipt establishes activation. Deployment remains
pending owner authorization. Physical mobile IME/microphone, real long-clip STT and live-provider
outage reproduction are not claimed; browser/audio tab lifetime limitations still apply.

# Chat history controls

Search in chat follows Settings in the workspace navigation. It searches message contents and archive names for the selected project context (All Projects is the global conversation). Selecting a result opens matching messages, including older messages; Read full chat opens the entire saved conversation. Long conversations and result lists have pagination.

The square chat menu immediately left of usage contains Clear, Archive, Copy 5 and Copy all, followed by individually listed Conversation, Auto and Execute modes. Copy all copies every message in the current conversation, fetching earlier pages beyond the 200 displayed messages. Copy five copies the latest five messages, or all messages when fewer than five exist. Both preserve author labels, line breaks and chronological order, include received streaming text, and show a short success notice. Empty chats disable copying. Clipboard API failures fall back to browser copying and restore the text selection.

Periodic refreshes keep existing message elements and text nodes. Streaming appends to the existing text node. Selecting text or reading older messages suppresses automatic scrolling; chats already at the bottom continue following incoming text.

Archive and Clear both show a confirmation dialog with Cancel focused initially. Archive offers an editable project/date name, preserves all messages and the original conversation/thread metadata, and starts a fresh conversation. Clear removes only the current conversation's messages and starts a fresh conversation. Existing archives, tasks, project memory, execution evidence and audit events remain. Both actions show an eight-second status notification without changing the composer layout. Unsent drafts are preserved.

The schema 6 migration adds chat_archives without rewriting existing rows. The archive transaction moves the prior conversation out of the active context and records its name/date alongside its original ID. A fresh active conversation has no Codex thread or summary. Clear removes the old message/conversation rows and tool-schema setting. Neither action calls Codex or launches workers.

Both endpoints require the owner session, CSRF token, explicit confirmation, current conversation ID and last message ID. Empty chats, changed confirmations and unfinished responses are rejected. A message submission waiting for model routes cannot append into a conversation that was archived or cleared meanwhile. Search/detail APIs enforce the selected context.

The historical validation/deployment report remains in Git history. Current coverage is in `tests/test_chat_history.py` and `scripts/browser_smoke.py`; current deployment evidence is in [CODEBASE_CLEANUP.md](CODEBASE_CLEANUP.md).

## Chat reliability patch — prepared, not deployed

This focused patch follows owner-approved diagnosis/planning assignment `49dec2f7a90642acb8b93ebdd8b422ab`. Its baseline is `5ff6d7f`; development and all fixture servers/data are in a separate standalone checkout. No production service command, live checkout edit, native-history rewrite, assignment resumption or deployment is part of this delivery.

History derives assistant status from execution status and `finished_at`, with its text and event cursor read in one SQLite snapshot. Queued/connecting, running, automatic-recovery wait, completed, failed and stopped responses stay distinct; terminal empty replies have a terminal label. On SSE error, the browser checks history after one second through the existing authenticated API and reattaches after the saved cursor. Old selection/attachment callbacks are ignored, replayed delta IDs are ignored, and snapshot refresh closes the previous attachment before reading. Stream expiry returns to login. Existing text-selection preservation still applies.

Each browser submission stores its original random request ID and exact content/project/conversation/mode in tab `sessionStorage` before sending. Ambiguous acceptance retains that record across reload and login; returning to its context or Send checks the same request. A new edit does not silently change the saved request. An explicit rejection clears it; successful acceptance clears it without discarding edits made while the request was pending. The single-owner API maps `(owner_id=1, request_id)` uniquely to a payload hash and original receipt in the same immediate transaction as message/execution acceptance. Matching retries return that receipt before active-conversation checks, including after completion/restart/clear/archive; changed content or context returns 409. Receipts deliberately outlive cleared messages. A crash before launching the accepted job uses existing startup recovery. Legacy API clients omitting `request_id` retain their old behavior and do not get this duplicate protection.

The existing scheduler observes attached manager/worker turns after approximately 60 seconds without meaningful item/tool/text/error activity, at most once per minute per turn and one bounded ten-second native-history read per scheduler tick. Native tool/question requests enter the existing event path as activity. Pending questions retain their input gate. Confirmed exact-turn completion/failure/interruption enters the attached runner's normal finalization/backoff path with authoritative saved text. A still-active turn, missing/uncertain history or failed inspection causes no retry, interruption, inference, ownership release or account-gate reservation. Observation is metadata only; it never diagnoses failure from elapsed time. Existing process/thread reuse, worker cleanup, project exclusivity, delivery and account/backoff protections remain. Connection reuse, upstream transport settings and retry policy are unchanged; the alleged reused-connection cause remains unproven.

Schema **10** adds only `chat_requests` and its composite primary key, plus the schema marker. There are no new dependencies or existing-row rewrites. The migration fixture compares every existing table's rows, including sessions, and checks integrity/foreign keys. Fixtures cover lost acceptance with one delegation, concurrent request acceptance, crash before launch, retry after completion/restart/clear, changed-context rejection, silent active generation, recent/long tool activity, approval wait, failed inspection, missed worker completion and exact history status/cursors. `scripts/browser_chat_reliability.py` actually drops an accepted HTTP response, restarts its disposable backend after completion and reloads the same browser tab; it also breaks SSE and tests exact text/status recovery, duplicate cursor replay, switching/stale callbacks, expiry and empty/stopped replies.

Validation: **97 backend tests passed**; real disposable Codex acceptance passed in **38.4 seconds**, with one assignment, preserved tests, a local fixture commit and automatic manager reporting; existing Chromium desktop/mobile/narrow acceptance and the dedicated browser fault suite passed; Ruff, JavaScript syntax and Git whitespace checks passed. The existing Starlette/httpx deprecation warning remains. Native outcomes in new fault tests are controlled fixtures; no live upstream outage or reused-connection reproduction was attempted. Browser persistence lasts for the tab session, requires working `sessionStorage`, and does not protect a caller that loses its request ID. Metadata inspection can delay a scheduler pass by up to ten seconds. These protections do not establish exactly-once external side effects; existing native-history/ownership limits still apply.

Independent review `fdf8b70ab6584a6d8eab3df275c5247a` accepted the material behavior above and reproduced a delayed-navigation race. The follow-up adds an entry selection-token guard to `loadHistory`, before closing any stream or incrementing its request counter. Its browser regression suspends an older Memory navigation, selects another chat and attaches SSE, then resumes the old navigation: the attachment, close count, history request counter, history object, selected project, selection token, execution and displayed messages/status must remain unchanged. This regression failed before the guard and passed after it; a current default history refresh still closes/replaces its own attachment and increments its counter normally. Both Chromium suites, JavaScript syntax, focused Ruff and Git whitespace checks passed again. The previously accepted 97 backend tests and 38.4-second real Codex acceptance were not repeated for this browser-only fix. The ten-second native inspection limit and migration/rollback behavior remain unchanged.

For a separately authorized release, use the existing [operations cutover procedure](operations.md): review/apply the tested commits to a separate release checkout; take verified online manager and native-history backups; rehearse schema 10/startup on copies; compare original rows/session receipts and native references; arrange a quiescent cutover that preserves active sessions and unresolved browser submissions; then switch only the dedicated manager service to the tested release. Verify asset hashes, authentication, scheduler, schema/integrity, preserved rows and saved request replay on disposable data. This assignment has performed no production backup, integration or cutover.

Rollback must preserve accepted requests and native history. Schema 10 is additive and old schema-9 code can read the expanded database, but that code ignores request IDs: downgrading it while an ambiguous browser submission exists could duplicate acceptance. Prefer disabling/reverting the new observer/UI while retaining the receipt-aware submission handler and `chat_requests`. A full baseline rollback requires a separate reviewed quiet-window plan to account for every unresolved owner submission first. Keep the new table, original execution/messages, native state/history and current sessions; do not restore an older database over newly accepted work or delete receipts to downgrade. No destructive down migration is needed.

## Authorized release preparation — 2026-10-12, cutover blocked

The owner authorized integration and deployment of this reviewed patch. The standalone release checkout is `/home/codex/checkouts/linko-chat-reliability-release-20261012`. Its implementation is the exact reviewed commits `00ac947c9f4c33161686eb73ecb890b77846b830` and `d3acbb15edf7954940cc37349167196148f0cfc3`, fast-forwarded from the clean live baseline `5ff6d7ff3f5cad4d0e2cad8d78bda3570edd8f5d`. Regenerating their format-patch reproduces SHA256 `a905f7482f3951bcfb56a84906d8ca7a8c4d17daeb3c7309262dfc832d6d27fa`. This documentation receipt changes no executable assets. There was no remote push or live checkout integration.

Fresh release-checkout validation passed: **97 tests in 6.71 seconds**, both Chromium suites (including dropped acceptance/reload/backend restart/SSE and delayed navigation), Ruff, JavaScript syntax and Git whitespace checks. The existing Starlette/httpx warning remains. Prior real disposable Codex acceptance belongs to the identical reviewed implementation; it was not repeated during release preparation.

Verified online preparation backup: `/home/codex/.local/share/linko-bot-manager/backups/chat-reliability-20261012-prepared/`. Its `manager.sqlite3` SHA256 is `9fd93b348b97d639ea82479c088c9e8430b9bc2e335fd1c30880a736ca762a1a`. The private manifest records SQLite state/history backups, all **73** referenced native rollout histories (none missing), copied service/environment settings and file hashes. Rollouts were copied and verified as byte prefixes at capture time because native history remains active. This is an online preparation snapshot, not a synchronized idle-cutover backup; refresh it after all work settles. Authentication secrets were neither printed nor committed.

On a separate copy, schema 9→10 migration preserved every existing table row, including 11 projects, 64 tasks, 147 executions, 122199 events, 82 messages, all conversations/native references, owner and session. Only the new empty `chat_requests` table and schema marker were added; integrity and foreign-key checks passed. Restore to a fresh destination reproduced the original rows. Baseline `Database` initialization can read the migrated copy without changing rows; this does not establish a safe receipt-blind rollback. Full startup was rehearsed on another copy with its PID ownership cleared, unfinished execution retry delayed and native access prohibited. Scheduler initialization, authentication boundary and exact release assets passed without changing the sanitized copy's rows. Production process reconciliation/inference was deliberately excluded from that rehearsal. Private migration/startup/rollback reports retain the evidence.

**Cutover blocker:** releasing execution `34a86168559b46798eb251b15c10fee2`, assignment `fd3c0e2042f44136a9c81e0f726bfef9`, native process PID **51178** is inside `linko-bot-manager.service`, parent manager PID **1410**. The unit uses `KillMode=control-group`. It is the only unfinished execution/active task claim; no pending delivery or question was present at inspection. Restarting from this worker would terminate the releasing turn. Repository operations offer only idle stop/fast-forward/start, with no supported safe operational handoff or dispatch-quiesce mechanism. No detached restart, service change, task-state manipulation or unsafe self-restart was performed.

Live production remains baseline `5ff6d7f`, schema **9**, PID **1410**, active since 2026-10-11 22:56:07 +04. Loopback and private HTTPS returned 200 for auth status/interface/assets, reported the baseline commit and running scheduler, and returned 401 for unauthenticated portfolio access. HTML/JS/CSS bytes match the live checkout. The daily backup timer is active; Tailscale listeners 443/3000/8787 are unchanged. These are existing-production health checks, not post-deployment evidence. Production schema 10, new served JS and post-restart chat smoke remain unverified because deployment did not occur.

To complete this already authorized release, use an operator session outside the manager service after this assignment and its manager reporting turn have finished. Arrange an owner submission quiet window; settle ambiguous legacy browser submissions and retain tab drafts/request IDs. Recheck live baseline/clean status, unfinished manager and worker executions, task leases, pending deliveries/questions, queued tasks and recovery eligibility; preserve unrelated work and wait if any work is active. Take fresh verified manager/native-history backups and repeat migration rehearsal on copies. Immediately recheck idleness before stopping only the dedicated service; while stopped, recheck persisted state and take the final manager snapshot. If new work appeared, start the original service and defer the release. Otherwise fast-forward the live checkout to this release receipt commit from the local release checkout and start the existing service with its existing environment and `.venv`.

Verify startup commit, schema 10, integrity/foreign keys, preserved pre-stop records/sessions/native references, scheduler health and exact committed assets over loopback/private HTTPS, with unauthenticated APIs still protected. Run focused chat reliability smoke on disposable data using the deployed code; do not create production acceptance assignments. Preserve `chat_requests` and newly accepted work for rollback; do not restore this older preparation snapshot over later work. A receipt-aware selective rollback needs its own reviewed implementation, and a complete baseline rollback needs every unresolved submission accounted for in a quiet window. The validated restore and additive-schema compatibility are preparation evidence, not authorization to discard new data.

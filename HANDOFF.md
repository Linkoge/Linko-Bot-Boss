# Agent controls — prepared only, 2026-10-12

Read [agent controls](docs/AGENT_CONTROLS.md) and its full private REPORT.md. Exact candidate
`agent-controls-v1-20261012`, isolated checkout `/home/codex/checkouts/linko-agent-controls-20261012`.
Implemented/verified locally; no publication or deployment request. Manager routine brief/recent delivery
should use the retained manager-summary.json, preserve original To-Do/task/execution linkage, and
record activation pending owner deployment request. No full memory reconciliation is needed.

The current deployed baseline is **chat-ui-mobile-composer-v4-20261012**, commit **00fb20f**,
established by exact succeeded receipt `deployment-helper/results/a9ef45440d33ae7a692f825249291e41.json`
under the private data directory. Older pending wording below is historical. No live files, production
assignment dispositions or unrelated work were changed for this candidate. Preserve partial work and
requested controls; do not manufacture idleness for a later deployment.

---

# Mobile composer regressions — fixed candidate, no deployment

Exact candidate release: `chat-ui-mobile-composer-v4-20261012`; isolated checkout
`/home/codex/checkouts/linko-mobile-composer-20261012`. Implemented and browser-verified;
prepared for later owner-requested activation. **No deployment is requested or performed.**
The deployed baseline is `chat-ui-composer-v3-20261012`, commit
`5b31f3fc554a16a21ba6f138d975e0af4d8ce748`, established by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/ae463def00a557e8cebafbf1c7536fa0.json`.
Earlier prepared-only statements for that baseline below are historical and superseded.
Detailed evidence and limitations: [full private report](/home/codex/.local/share/linko-bot-manager/deployments/mobile-composer-20261012/REPORT.md).


Fixes fresh-context + being left disabled, pointer button focus stealing and small left-hand
Stop/Discard controls. Direct trusted desktop/touch picker activation and real uploaded bytes
are checked; recording actions now align right with larger touch targets, retaining the compact
viewport and all original cleanup/audio/attachment/queue behavior. Physical Android keyboard
and native picker UI remain unverified; OS picker may temporarily hide/replace the keyboard.

Manager routine brief/recent delivery should reference the full private report and its
`manager-summary.json`: implemented/verified/prepared, pending owner deployment request and
physical-device verification. Preserve task `96591308b2354e0ba0671316c5ef58a9` / execution
`900ab350079f4a83abfc5ddfdfd65f07` linkage. Do not assert this new candidate is deployed;
only the exact future succeeded helper receipt establishes that. Prior immutable releases
and production data are unchanged. No full memory reconciliation is needed.

---

# Combined chat cleanup / compact composer — prepared only

Exact prepared release: `chat-ui-composer-v3-20261012`; checkout
`/home/codex/checkouts/linko-compact-composer-20261012`.
Reuses the finished original cleanup candidate after its worker and manager delivery drained;
preserves all original requirements and deployed queue behavior. Adds compact +/Send/Record,
working scoped attachments through native manager/worker plumbing, safe recording append and
truthful browser interruption recovery. No live edits, task cancellation, restart or deployment.

Read current [chat history](docs/chat-history.md), [audio lifecycle](docs/AUDIO.md),
and the [full private report](/home/codex/.local/share/linko-bot-manager/deployments/compact-composer-20261012/REPORT.md). Manager should record implemented/verified vs
prepared/not deployed in its routine brief/recent delivery using the report's manager-summary.json;
retain original task/result linkage and succeeded queue baseline receipt. This combined release
supersedes cleanup V2 for a later owner-requested activation; preserve its immutable receipts.
Do not claim activation without the exact future succeeded receipt. Attachment storage must be
preserved with database/native backups; prior DB-only backups do not contain uploaded bytes.

# Chat UI cleanup — prepared; activation pending, 2026-10-12

Exact release ID: `chat-ui-cleanup-v2-20261012`. Candidate:
`/home/codex/checkouts/linko-chat-cleanup-20261012`.
Read [docs/chat-history.md](docs/chat-history.md) for current implementation and authoritative
queue baseline correction. Queue release `queue-composer-v1-20261012` **did deploy successfully**;
its exact receipt is linked there. Older pending sections below remain historical.

Cleanup hides the insertion caret, removes decorative speakers/ordinary Completed badges,
and places history utilities and individual modes in a square menu immediately left of usage.
Preserves queue composer, audio and internal-result filtering. Implemented/verified/prepared;
**not deployed**, no deployment requested. All 199 tests and required browser checks pass.
Full private evidence, manager brief/recent-delivery facts and limitations:
`/home/codex/.local/share/linko-bot-manager/deployments/chat-ui-cleanup-20261012/REPORT.md`.
Manager should record this routine delivery with pending activation and the exact release/report
references, without full memory reconciliation or asserting deployment. No restart/cutover performed.

# Integrated ordering / nonblocking chat candidate — prepared, not activated

Exact release ID: `queue-composer-v1-20261012`. Read the current section of
[docs/chat-history.md](docs/chat-history.md) and its private REPORT.md for implementation,
verification, evidence and manager routine brief/recent-delivery facts. Candidate:
`/home/codex/checkouts/linko-nonblocking-chat-20261012`.
Preserves completed composer/audio/internal-result assignments, 20-minute capture and compact UI.
No deployment request, service restart, active-work cancellation or succeeded activation receipt.
The integrated candidate supersedes individual prepared IDs for a future requested activation;
keep their immutable receipts. Use controlled deployment only after owner requests activation.

# Worker delivery and manager context candidate — activation pending, 2026-10-12

Read [docs/MANAGER_COMMUNICATION.md](docs/MANAGER_COMMUNICATION.md). Exact combined release:
`worker-delivery-audio-v1-20261012`, checkout `/home/codex/checkouts/linko-worker-delivery-20261012`.
Includes the completed audio/composer v3 candidate. Raw results were application worker messages,
rendered verbatim and replayed into native context despite the existing brief-reply preference.
Compact internal notices, scoped exact-result retrieval, provenance-based history projection and
native-thread compatibility rotation preserve full audit/evidence and owner JSON. Shared preference
strengthened immediately; candidate code is not live. No deployment authorized/requested/succeeded.
Detailed evidence: `/home/codex/.local/share/linko-bot-manager/deployments/worker-delivery-20261012/REPORT.md`.
Manager should record the verified candidate and pending activation in its existing brief/recent
navigation using this report; do not run a full memory reconciliation or assert deployment.

# Audio 20-minute candidate — prepared; activation pending, 2026-10-12

Read [docs/AUDIO.md](docs/AUDIO.md) for authoritative behavior, limits, validation and evidence.
Exact compatible release ID: `audio-composer-v3-20261012`; isolated checkout:
`/home/codex/checkouts/linko-audio-20m-20261012`. Implements owner's 1200-second recording request,
removes textarea prompt, context label, composer note and recording prose; elapsed `MM:SS` starts at `00:00`. Preserves full-file Stop/transcribe/edit behavior and explicit Send.
Supports the larger clips with 32 MiB intake, increased probe/upload/provider/browser deadlines,
and consistent 64,000-character transcript/composer/chat intake. One-second encoder tolerance retained.

Manager routine brief/recent delivery: implemented and validated with synthetic 20-minute compressed
files and real Chromium accelerated boundary/full-Blob capture; fixture STT only, no paid provider call.
Fixed release validation and manifest are retained under the exact release directory named in AUDIO.md.
Detailed sanitized report: `/home/codex/.local/share/linko-bot-manager/deployments/audio-20m-20261012/REPORT.md`.
**Not activated; no new deployment request or succeeded receipt.** Owner requested functionality,
not deployment. Use controlled `deploy_release` only on subsequent owner activation request; no shell cutover.
Live remains `d87be8124ab9d7c2de325982061dcac3467994e6`, previous audio/key-load receipts succeeded.
Preserve working service, private key, memory/fetch fixes, existing dirty docs and production data.

# Codex account usage candidate — activation pending, 2026-10-12

Read [docs/CODEX_USAGE.md](docs/CODEX_USAGE.md) and its private report. This isolated candidate includes
the reviewed deployment-helper candidate and preserves the live startup/To-Do/Markdown baseline.
Use the combined usage release only through the helper external bootstrap/idle procedure.
The original helper release remains untouched; no self-restart or remote push.

# Deployment helper candidate — first activation pending, 2026-10-12

Read [docs/DEPLOYMENT_HELPER.md](docs/DEPLOYMENT_HELPER.md) for the controlled independent helper,
exact one-time external bootstrap and durable evidence. The combined startup/zero-badge/To-Do/Markdown
release is already live at 7999d1a, schema11; the pending text below is historical. This new candidate
preserves that complete release. No manager restart or active-worker interruption was performed.

# Startup / To-Do candidate — activation pending, 2026-10-12

Read [docs/STARTUP_TODO.md](docs/STARTUP_TODO.md) and the linked private REPORT.md for the reviewed isolated
candidate, current live baseline, preservation, verified backup/migration receipts and authorized external cutover.
Actual live release is 9e5c518, schema10; the older mobile-patch “pending” text below is historical.
The worker belongs to the manager service cgroup and cannot safely activate itself. No live assets/service or
production schema were modified. Startup follows durable context recency including global; zero Tasks badges are
hidden; durable To-Do is separate from execution and retains the deferred memory proposal via a source link.
Preserve the dirty live TASKS.md preimage before integration; no unrelated files may be reset. No remote push.

# Prepared mobile/project UI patch — 2026-10-12

This delivery is prepared in the private standalone checkout, not activated in the live source/service. Base is the already deployed combined chat-reliability/All Projects release bd757456ecc81d495270c5619b5f8da3fa76abc8. Preserve later external integration changes; apply/review this focused commit or patch against the actual live HEAD. No worker service restart or live asset modification was performed.

Composer now measures the visual viewport once and flexes below the measured navigation. Tasks/Agents refresh requests bind to the selected project with stale-response guards. Task creation and confirmed agent work-start timestamps display minutes in Asia/Tbilisi; Agents counts only confirmed running turns without pending questions or paused task state. A single durable execution predicate drives the count, independently of the displayed history limit.

Authorized archival is already persisted: all 65 pre-request inactive objectives remain in SQLite/audit history; unfinished old work was cancelled, completed work retained its lifecycle. Current/new/running work and the pending external release assignment are preserved. The clear-visible filter takes effect with this patch; do not dispatch/resume archived work. Future execution routes are now explicitly gpt-6.1-sol/medium; the active turn was preserved. Global instructions/native history/artifacts/dependencies were retained.

Full implementation, self-review, validation, cleanup receipts, verified pre-cleanup backup, model catalog, screenshots and external activation instructions: /home/codex/.local/share/linko-bot-manager/deployments/mobile-project-ui-20261012/REPORT.md. Task-linked evidence is retained for assignment 184139effe324fc7a3ae6a46e8834137. Physical Android/iOS keyboard behavior and the unavailable owner screenshot were not reproduced; Chromium covers reduced, overlay, panned and simulated safe-area viewports. Deployment remains an external-session action after the current worker finishes.

# Operations handoff

Read [README.md](README.md), [docs/operations.md](docs/operations.md) and [docs/AUTOMATIC_RECOVERY.md](docs/AUTOMATIC_RECOVERY.md) for the current workflow, deployment and validation.

The dedicated user service runs from this repository with its existing Python environment and private HTTPS listener. Preserve the production database, native Codex threads, owner notes, projects and unrelated services. Use disposable fixtures for tests. Historical unfinished assignments stay waiting until explicitly continued; no migration replays or merges old clones.

Earlier handoffs and deployment evidence remain in Git history. They describe superseded supervision paths and are not current instructions.

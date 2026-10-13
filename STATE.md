# Bundled To-Do / current-chat release — implementation candidate, 2026-10-13

Actual live base is succeeded `pwa-standalone-v1-20261013`, commit
`3bb554b7efa26d8fae695c5340e8a12366d75339`, durable receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/09f882af21fb28ab5870da74fee1c3b6.json`.
All 20 loopback served aliases matched the receipt. Earlier PWA-pending/live statements below are historical.

Candidate `todo-handoff-chat-trash-recording-agents-v1-20261013` is in
`/home/codex/checkouts/linko-todo-chat-20261013`. Preparation is established only by its exact
immutable deployment-helper manifest. No deployment is authorized or performed for this candidate.
Implements atomic To-Do-to-assignment archival, explicit Start through the manager queue,
per-context single-slot chat trash with reversible swap restore and separate right-side Empty Trash,
focus-independent recording expansion/Send, and stable expanded Agents output across polling.
Preserves PWA/cache/update, owner authentication, drafts/attachments, queue/recovery and helper workflow;
no schema/dependency change. Source entries and original conversation/task/native evidence remain retained.

Owner explicitly scheduled entries `91793f5c9e4e483c893bbdae80cdfd19`,
`f26f20a22342434695ee76c352a9dabc`, `5b8d6fb245e44153bb9abab6a0b40374` are archived
and linked to existing resumed assignment `46690e845e75461cbfe9309f5c734662`.
Old entry `5bdfa1dc8b7f42a6a766ab9f99574935` is archived solely on owner confirmation
that it is done, retaining reference `a01b6c008c19464f883d5709937fc906`;
no old-item investigation/reimplementation was performed. Other backlog entries are unchanged.

Authoritative updated contracts: docs/STARTUP_TODO.md, docs/chat-history.md, docs/AUDIO.md.
Full validation/status, limitations and exact release evidence: `/home/codex/.local/share/linko-bot-manager/deployments/todo-chat-20261013/REPORT.md` and sibling
`manager-summary.json`. Manager should use these for its routine brief/recent-delivery update;
keep implemented/verified/prepared separate from deployed. Deployment remains pending.

# PWA candidate — prepared release, deployment pending, 2026-10-13

Actual live base is `microphone-any-files-composer-v2-20261013`, commit
`35711af31b712370bec9fbd87b4ffd2bced71fe9`; durable succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/19c7b38baf0e78f924e04320c1a02d1d.json`.
Read-only loopback and private HTTPS checks matched all 16 served assets.
Older live/pending statements below are historical and superseded by this evidence.

Candidate `pwa-standalone-v1-20261013` in `/home/codex/checkouts/linko-pwa-20261013`
adds linked manifest, owner koi/binary icons, mobile metadata and content-hashed
immutable JS/CSS while retaining no-store HTML/API/aliases and existing behavior.
No service worker, dependency/schema change or deployment performed. Exact prepared
manifest and validation outcomes are retained in [PWA contract](docs/PWA.md) and
its private full report/manager facts. Physical Android acceptance and activation
remain pending. Use the existing controlled deployment helper only after a future
owner deployment instruction; current instruction explicitly says do not deploy.

# Integrated microphone/files candidate — 2026-10-13

Current candidate `microphone-any-files-composer-v2-20261013` integrates completed
composer release `compact-expanding-composer-v2-20261013` without competing edits.
Exact preparation requires its immutable manifest. Implemented; validation/report records
actual outcomes. Not deployed; owner request and durable succeeded receipt remain pending.
Report and manager facts: `/home/codex/.local/share/linko-bot-manager/deployments/microphone-any-files-20261013/REPORT.md`
and sibling manager-summary.json. Current live base is cd0b3ec, succeeded receipt
`deployment-helper/results/f62b8538f5233c8957cbf5da873a8772.json`.
See docs/chat-history.md and asset README for format/security/icon behavior and restoration.

# Current established state — 2026-10-13

Live `audio-format-pass-through-usage-v1-20261013`, commit
`cd0b3ecf69000475665cb124256e660883793c4b`, has durable succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/f62b8538f5233c8957cbf5da873a8772.json`.
Read-only inspection matched all 12 served assets to that receipt. It includes provider-decided
whole-file audio format handling, lowercase usage durations and all prior integrated functionality.

New candidate `compact-expanding-composer-v2-20261013` implements the compact/active/overflow
composer, best-effort keyboard-aware muted caret, identical gray activation border, blank empty
conversation, Upload-style Send and selected red category text without underlines.
Implementation/verification/preparation evidence: /home/codex/.local/share/linko-bot-manager/deployments/expanding-composer-20261013/REPORT.md.
Deployment remains pending a new owner request. No production restart/data mutation performed.
Current keyboard/browser limits and preserved flows: docs/chat-history.md.

Candidate `usage-compact-lowercase-v2-20261013` in
`/home/codex/checkouts/linko-usage-compact-20261013` changes only duration formatting:
`6h 12m`, Weekly `5d 12h 15m`, zero components omitted, `1m` for positive sub-minute,
`0m` expired, truthful `Unavailable`. Owner correction requires lowercase d/h/m.
Percentage typography, popup labels/freshness and chat scrolling remain preserved.
Exact preparation requires its helper manifest; deployment is pending owner request.
Full evidence/report: `/home/codex/.local/share/linko-bot-manager/deployments/usage-compact-20261013/REPORT.md`.
Contracts: docs/CODEX_USAGE.md and docs/chat-history.md. Production data, native histories,
owner holds, settings, audio/upload, memory/recovery and private hosting remain preserved.

Retained integrated effects: durable assignment follow-up and idle recovery respecting owner holds;
latest supported catalog version within the chosen family for new work, exact retained routes for
existing assignments/recovery; separate recording Stop removed while Record toggles stop/transcribe;
long Tasks names wrap; model-family buttons have distinct subtle red backgrounds; concise manager
pause/append scope; lean 5 hour/Weekly usage with last successful refresh time; project-menu layering
and input corrected. Existing 2×2 navigation, recording Upload/cancellation/replacement, drafts,
attachments, manual chat scrolling, rich-red theme, lifecycle controls and live base remain.
No schema/dependency change, separate router or new supervision service is introduced.

Owner pauses, waits, cancellations, deferred To-Do and unrelated unfinished work remain respected.
The History editor Cancel handler remains an unrelated pre-existing follow-up (Escape works).
Historical interruption was caused immediately by an authenticated interrupt request; triggering gesture
and STT-provider 502 cause remain unknown. Physical-device/native picker and paid STT acceptance are
not claimed. Original model/catalog observations are historical snapshots, not current settings claims.

Authoritative contracts: [recovery](docs/AUTOMATIC_RECOVERY.md), [models](docs/MODEL_SETTINGS.md),
[audio](docs/AUDIO.md), [controls](docs/AGENT_CONTROLS.md), [UI](docs/UI_THEME.md),
[usage/project popup](docs/CODEX_USAGE.md), [memory](docs/PROJECT_MEMORY.md),
[deployment](docs/DEPLOYMENT_HELPER.md). Full report, source mapping, verification and manager routine
brief/recent-delivery facts: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md` and sibling `manager-summary.json`.
Earlier state remains in Git and original private reports/receipts. No full memory reconciliation.

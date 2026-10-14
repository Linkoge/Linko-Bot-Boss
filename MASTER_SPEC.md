# Linko Bot Boss requirements

Current architecture, superseding prior supervision requirements as authorized on 2026-10-11:

**User → AI Manager → Codex worker → AI Manager → User**

- Conversation mode is read-only; Auto delegates requested work; Execute treats the message as an objective through the same manager path.
- Natural conversational requests; the manager decides when requested work should be delegated.
- One ordinary request becomes one complete objective, with necessary project/conversation context. Related Small requests may batch into a queued, never-started assignment while the combined scope remains Small, preserving selected attachment references, metadata and first-seen order.
- Native Codex tools handle investigation, editing, commands, dependency installation, builds, testing, debugging and Git.
- Validation has two levels: Normal tasks run targeted tests, smoke/startup/health and relevant build/lint; Deploy runs full backend once on the exact combined candidate at existing automatic preparation. One worker self-check, no independent reviewer or escalation loop. Same-assignment repairs are capped at three durably across retries/restarts; retain failure reporting and affected-check reruns. Contract: docs/VERIFICATION_TIERS.md.
- No separate planner, task DAG, verification controller, registered verification commands, permission profiles, capability broker or integration controller or independent reviewer.
- No routine owner approval. Preserve explicit confirmation for catastrophic broad destruction, using native interaction where available and conversational continuation otherwise.
- Automatic durable worker-result delivery to the originating manager conversation, including when the browser disconnects or another project is selected.
- Existing and future projects use one global sequential coding worker; historical opt-in/capacity cannot create overlap. Preserve isolated workspace/source ownership, controls/holds and serialized reconciliation for retained sources. Contract: docs/PARALLEL_EXECUTION.md.
- SQLite preserves conversations, assignments, execution history, question answers and result-delivery identity. Native Codex thread storage is also required for resumable model context.
- Restart recovery inspects prior outcomes before continuation. Never launch a competing worker while a prior owned process remains unresolved. Do not blindly replay a turn with unknown launch identity.
- Preserve deliberate pauses/cancellations, existing user edits, historical records and owner notes.
- While coding, Tasks/Agents expose Pause and Cancel with requested versus confirmed cleanup state. Pause promptly interrupts to a retained checkpoint; Cancel immediately signals owned execution. Retain partial work for review/resume. Manager pause and append-only instruction bundling resume the same unfinished assignment without competing workers. Contract: docs/AGENT_CONTROLS.md.
- Secure single-owner authentication, private hosting, CSRF/session protections and verified backup/restore remain.
- Native workers have broad same-user access; do not claim no-host-home isolation, transactional shell replay, source-preserving clones or deterministic prevention of arbitrary destructive code.
- No new orchestration framework, supervision AI calls, speculative policy engines or manager-created subagent hierarchy.

- Manager and Bot settings follow [model settings](docs/MODEL_SETTINGS.md): manual Manager, retained manual Bot plus independent Automatic policy, supported exact pairs enforced and durably selected before dispatch; no extra router.
- Navigation retains four 44px square controls in aligned upper project/Settings and lower chat-utilities/usage columns; only project/view tabs scroll. Recording uses the Record toggle to stop/transcribe and exposes Discard/Upload; cancellation retains capture, selection replaces and safely discards microphone capture through provider-decided whole-file STT without local extension/MIME restrictions. Preserve drafts, attachments, sequential append and context-bound focus. Contract: docs/AUDIO.md.
- Initial website load places loaded chat at the bottom once layout is ready; afterward chat scrolling is fully manual during sends, streaming and updates, with browser anchoring/restoration disabled. Preserve existing composer/audio/lifecycle/security and rich red theme.

Implementation uses existing FastAPI, SQLite, plain JavaScript and Codex App Server. Model settings contain only manager and worker roles. Old requirements are retained in Git history and historical stage reports.

## Project memory

- Manager planning/brainstorming uses one derived persistent compact project brief (purpose, goals, constraints, established state, active decisions/reasons, unresolved work, three recent meaningful deliveries and retrieval references). Target 1500 tokens, maximum 2000 excluding shared global instructions; overflow must preserve active requirements and surface a gap, never silently truncate.
- Authoritative specs/docs, complete historical evidence and archived transcripts remain retrievable; fresh chats exclude archived transcripts. Recent deliveries include date/change/effect/verification/deployment/report, archive oldest on the fourth and retain current lasting effects/unresolved blockers. Related fixes share one delivery ID.
- Worker assignments include objective/acceptance/relevant constraints/dependencies/selected context, with necessary source inspection and repository/security instructions; no automatic bulk brief/history injection. Completion preserves detailed evidence and updates relevant authoritative docs and meaningful established facts (no compulsory memory stage) with truthful proposed/implemented/verified/deployed status. Deployment needs durable success evidence.
- Full reconciliation is only an explicit project-scoped owner workflow: explain scope/unknown cost, Start, progress/actual reported usage, retained old/candidate versions, fact diff/references/unresolved conflicts, explicit Apply, retrievable prior version/rollback. Failed/cancelled review leaves active memory intact. Missing code/ambiguous conflicts cannot silently erase goals. No recurring/background reviews. Global owner communication preference persists across fresh/cleared chats. Conversation compaction is separate future scope.

Implementation contract and evidence navigation: docs/PROJECT_MEMORY.md.

- Related Small requests may be batched into one queued, unstarted Small assignment (`delegate batch=true`) only while the combined scope stays Small. Post-deploy health failure automatically rolls back to the exact old commit without restoring old data; a throwaway rehearsal is in tests/test_rollback_rehearsal.py.
- Validated local releases have a supported serialized immutable publication/import operation after worker cleanup, with pinned complete inventory, task/branch ownership, current-base and non-overwrite checks. Publication does not continue tasks, create intent or activate code. New V2 assignment bindings hash semantic scope and latest selected context, keeping unchanged lifecycle resumes stable; legacy V1 bindings are unchanged.
- Owner deployment intent must be recorded before asynchronous preparation/audit, survive result/restart handoff, and consume only one reviewed exact prepared release. Later owner corrections/holds revoke or suspend unused intent; internal messages grant no new permission. Contract: docs/DEPLOYMENT_HELPER.md.

- Successful worker completion proceeds to normal automatic handoff. Failed work retains transactional, bounded same-assignment follow-up; no separate review loop. Preserve owner holds, read-only mode and authorization boundaries. Contract: docs/AUTOMATIC_RECOVERY.md.
- New work resolves the latest visible supported model within the owner's selected Luna/Sol/Astra family from the authenticated catalog; keep existing assignment/recovery routes exact and policy-compatible. Contract: docs/MODEL_SETTINGS.md.
- Tasks and History remain within viewport width with complete long filenames/paths/unbroken text wrapping; local code scrolling stays contained. Family buttons have a subtle red gradient distinct from plain effort buttons, with accessible selected/disabled/focus states.

- Compact chat composer follows [chat UI contract](docs/chat-history.md): two-control idle,
  expanded +/Record/Send right stack, conditional per-request ⇈ Send beside ordinary Send, bounded draft scrolling;
  any nonempty draft remains expanded with Send after blur/keyboard closure; recording timer–Upload–Discard occupies one bottom-left row;
  muted caret follows supported keyboard signals, gray composer focus border, blank empty chat.
  Send shares Upload appearance; selected workspace category text stays red without an underline.

- General attachments accept arbitrary formats including SVG and unknown MIME/extensions,
  with existing size/resource/auth/storage protections. No unsafe SVG/HTML rendering or
  generic-to-STT routing. Only verified supported raster bytes become native image input;
  files/provider limitations remain honest. Owner microphone SVG and original recoverable
  asset provenance: ai_manager/static/icons/README.md; contract: docs/chat-history.md.

- Installable PWA follows [PWA contract](docs/PWA.md): owner artwork, standalone
  display with normal system bars, root URLs and existing private HTTPS/auth/state;
  uncached HTML plus content-hashed immutable JS/CSS, no service worker/offline
  cache. Implementation/device verification/deployment status remain distinct.

## Bundled owner-scheduled To-Do / current-chat candidate (2026-10-13)

The prepared manifest (not code changes alone) establishes release readiness; this candidate is not deployed.
To-Do CRUD remains nonexecuting. Explicit Start or owner manager scheduling consumes exact source revisions
only with accepted task creation/reuse/continuation, atomically retaining original source and durable task links.
Failure leaves entries active. Older done work is not automatically rescheduled. Contract: docs/STARTUP_TODO.md.
Clear retains one recoverable chat per context. Restore swaps with nonempty current chat; Empty/replacement
remove UI recovery only, retaining conversation/manager/task/native/archive evidence and unrelated chats.
Recording expands composer and exposes Send, using safe whole-file transcription/draft/attachment/FIFO semantics.
Expanded Agents output retains open/reading/selection state across updates until explicit close/navigation.
Contracts and validation limits: docs/chat-history.md and docs/AUDIO.md. Exact release/report: STATE.md.

## Project visibility

Project dropdown checkboxes persist navigation visibility independently of active/paused/archived
execution status. Unchecked projects appear under expandable Stashed projects; rechecking restores
them to main navigation. Preserve project files, IDs, conversations, drafts/uploads, tasks, memory
and native thread links. Stashing does not pause/resume work or suppress results/questions. Selected
stashing preserves the draft and switches to All Projects; active capture blocks switch/stash until
stopped. Rescans preserve visibility, new IDs default visible and only removed records are excluded.
Existing explicit Add/manual/startup discovery, including empty repositories, remains. See operations
for pending release status and verification evidence.

- Current live Codex/Claude provider switch is included in the merged parallel/composer release. New work uses active provider and its independent saved policy; existing tasks retain exact provider/model/thread/workspace. No provider/model setting is reset by integration or preparation. Contract: docs/MODEL_SETTINGS.md; current base/failure/evidence: STATE.md.

## Verified-work automatic deployment — second stage (2026-10-14)

Per-project **Auto-deploy verified work** is persisted in Settings; the configured Linko-Bot-Boss
project is seeded ON once at activation, preserving explicit later OFF values. ON treats the original
owner work request as standing recorded intent for its assignment's exact verified release, without
a second approval or unrelated-message consent churn. OFF retains manual deployment behavior.
Option B tier evidence bound to a clean commit, completed job follow-up and one full release gate
remain mandatory. Explicit owner holds/OFF and changed scope are checked before activation.
Schema/dependency releases require a copied-database migration rehearsal and stopped pre-activation
backup; health failure rolls back code plus data, and changed dependencies. Failure never retries
automatically. The independent helper v3 must first be installed externally; this implementation
is publication-ready only after the fixed validation gate and is not a live deployment claim.
Contract and evidence: [automatic deployment](docs/AUTO_DEPLOY.md).

Close readiness corrections: ON new work also uses automatic policy when its owner supplies an
explicit new-work deployment intent or deployment quote; the original intent/provenance is linked
and superseded without repeating approval. OFF/manual remains unchanged. Audits of an explicitly
fixed existing source/release do not substitute their own branches. Review-task readiness/holds
remain mandatory, but immutable release mappings contain only the owner's source tasks; V1/V2
hash semantics remain strict. Helper installers refuse manager/helper cgroups. Corrected artifact
preparation cannot refresh the old runtime's sealed scope or deploy the live application.

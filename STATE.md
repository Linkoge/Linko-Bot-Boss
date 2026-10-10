## 2026-10-10 — Compact navigation deployed

This update supersedes the pre-reboot task restrictions and old production baseline below. The owner explicitly authorized this focused UI change and private-service deployment.

- Replaced the sidebar, brand, portfolio breadcrumb, large project heading and ready indicator with exactly two sticky compact rows on desktop/mobile: project contexts, then existing workspace sections. Repository discovery/addition, rename/settings and sign-out remain available. All Projects is a separate global context, never a repository.
- Schema 4 adds optional display_name and durable last_used_at. Existing meaningful events backfill recency; submitted chat/goals, manager commands and task creation/worker assignment update it transactionally. Opening a project, viewing memory, renaming or polling does not affect recency. Stable names/IDs/paths/agents/tasks/threads and memory directories are preserved. Blank display names restore the original; names have an 80-character limit and reject controls. Duplicate labels are disambiguated with original names and short IDs.
- Project tabs scroll without wrapping. A fixed right dropdown includes every registered context, sorts display names case-insensitively, highlights selection, closes after selection, and supports Arrow/Home/End/Escape, native activation/Tab, touch and internal scrolling. Focus survives background navigation refreshes.
- Project switches preserve session drafts, clear stale history immediately and guard asynchronous history/memory responses. Documentation and Git observation load only when Memory is requested. Submitted messages bind to their original project even during switching. Existing chat persistence, SSE and worker/approval controls remain in place.
- Verification: **35 tests passed in 6.13s**, Ruff and JavaScript syntax passed; existing Starlette/httpx deprecation warning remains. Chromium checked 1440×1000, 393×851 and a reduced 393×420 keyboard-sized viewport, with no JS errors or horizontal page overflow. Visual screenshots inspected. At 393×851, content starts at **92px instead of 311.78px**; messages gain **249.25px (351 → 600.25px, approximately 71%)**. Desktop content starts at **92px instead of 192.98px**. Before/after comparison uses incoming checkpoint 990ed95fc35724a699e103ee7a238aba97ca22c2. Browser artifacts remain in /tmp/linko-before-{mobile,desktop}.png and /tmp/ai-manager-{mobile,desktop,overview}.png.
- Established deployment: verified private SQLite backup /home/codex/.local/share/linko-bot-manager/backups/manager-navigation-20261010-100802.sqlite3, then restarted only linko-bot-manager.service. Service active, PID594062, exit status0; private HTTPS8787 returns200 and exact tested asset hashes. Protected portfolio still returns401 without authentication. Existing Tailscale443/3000 mappings unchanged; no infrastructure/security/dependency changes.
- Post-deployment comparison retained all10 original project rows/identities,9 conversations (including thread associations),8 messages,11 tasks, owner/session records and policies. Schema4/integrity/foreign keys pass; all memory exports match SQLite. One project has historical meaningful usage. The current owner exists; older statements that production histories/owner are empty are historical. No active execution was present before restart. Existing scheduler configuration was preserved.
- Current filesystem check:217GiB total,172GiB available,18% used. This supersedes older space measurements but does not itself complete the separate durable storage/reboot acceptance tasks. No unrelated data was deleted. No VM reboot or remote Git push performed.
- Limits: mobile keyboard/safe-area behavior is tested with Chromium viewport resizing, not a physical Android device; authenticated production UI was tested against an isolated owner fixture, while deployed HTTPS/assets/data were checked directly. Drafts are kept per context for the current page session. Full original Stage6 acceptance remains separate.

# Verified state

Current: stages1–5 verified for supported capabilities; stage6 partially verified. Private service is live; full reboot/storage acceptance and initial owner setup remain open. Historical observations below retain the evidence available at each milestone.

## 2026-10-10 — Immediate pre-reboot checkpoint

This section supersedes earlier environment observations; older sections are milestone history. No application code changed, no services restarted/stopped, no reboot performed and no disk consumers deleted during this task.

- Repository /home/codex/projects/Linko-Bot-Manager; origin git@github.com:Linkoge/Linko-Bot-Manager.git; main; incoming HEAD 0ccfaa6ebd955efe02d6968301cb875c9b463e8f. Working tree was clean at entry. This checkpoint changes documentation only; resulting checkpoint hash is recorded in HANDOFF.md.
- Confirmed filesystem /dev/vda2: 41,953,755,136 total bytes, 38,956,138,496 used, 833,515,520 available (approximately 795MiB / 834 decimal MB), 98% usage. Previous verified report was ~445MB/99%. Owner reports approximately800MB available and some files manually deleted; names and exact deletion history were not independently verified. No cleanup was performed by this task. Remaining space is still tight. During final checks availability increased to1335345152bytes (~1.24GiB),97%; this later confirmed snapshot supersedes earlier measurements. No agent cleanup occurred and the cause of the increase was not independently verified.
- Live SQLite schema versions1/2/3 (current3), WAL, integrity ok, zero foreign-key errors. Ten projects, 50 memory rows/exports, two blocked tasks, one verified implementation checkpoint; all50 exports exist and match DB contents. No active manager-owned execution.
- Production conversations/messages/executions/thread IDs/approvals/operations/owner/policy overrides are all zero. Empty stores are confirmed, not missing or recovered histories. Durable storage/restart behavior for those stores is covered by existing fixture tests and historical real-model smoke evidence; no production chat/thread content is claimed.
- Effective global permission defaults remain in code; no overrides. Scheduler disabled by default, concurrency2; no model route override. Initial owner setup remains pending. Private auth-status endpoint returns setup_required=true and authenticated=false; real login cannot be verified until the owner supplies private credentials.
- Dedicated manager service running/enabled, daily backup timer active/enabled, latest scheduled backup succeeded, user linger=yes. tailscaled active/enabled. Correct absolute Codex path and secure-cookie/private-host settings confirmed. Optional service environment file absent. Full non-secret runtime inventory is in docs/inventory.md.
- All four existing dated backups passed integrity and foreign-key checks. Two oldest predate the outstanding tasks; latest two contain both. Fresh draft checkpoint backup278528bytes restored into an unused /tmp path; all20 application tables compared row-for-row equal, integrity ok, foreign keys clean, tenprojects/50memory/twotasks retained. Canonical final snapshot manager-pre-reboot-20261010.sqlite3 was then created (286720bytes), reopened and restored offline; all20 tables equaled both live DB and restored copy, all50 memory exports matched, checkpoint hash persisted in restored HANDOFF, no active executions. Paths/procedure in docs/inventory.md.
- Existing 31 tests passed again (7.56s); Ruff and JavaScript syntax passed. One known Starlette TestClient/httpx deprecation warning; no failing test. No browser/LLM turn was repeated during this documentation-only task. Previous desktop/mobile and real chat/worker/planning/coding/review results remain historical verified evidence in docs/verification.md.
- Confirmed pre-reboot boot ID e3a37e08-1b0b-4ac0-a923-8b6b97767873; boot since2026-10-08 17:26:16 local. New boot ID and post-boot health have not been verified. Previous controlled service-crash recovery passed; this task does not repeat it.
- Dependency installation, merge/push/deployment, infrastructure/network/credential/external-service/communication/payment worker capabilities remain unavailable. Trusted backend uses same-user permissions; current model/test boundaries are documented in docs/security.md.

### Current risks and next action

Run the ten-step read-only post-reboot checklist in docs/operations.md after the owner reboots. Record actual outcomes and blockers; do not mark Stage6 complete from enabled units alone. Storage, unset owner login, same-VM backups, unavailable sensitive capabilities and untested full reboot remain open. Identified disk consumers are measurements, not safe deletion recommendations; see docs/inventory.md. No currently failing application test or additional reproduced feature bug was found during checkpoint work.

## 2026-10-09 — Inventory

- Empty target repository found with expected origin and unborn `main` branch.
- Existing `../AI-Manager` is a Chatwoot deployment for customer email/Messenger, not a software-project manager. Reviewed README/upstream docs read-only; no reusable Codex orchestration backend. Its unrelated uncommitted changes were preserved.
- Python 3.14.4, uv 0.12.23, Git 2.53.0 and Codex CLI 0.161.0 available.
- Installed App Server supports stdio and local JSON schema generation. Schema generated into temporary directory, not vendored.
- Host systemd/tmux sockets inaccessible; Tailscale daemon inaccessible from this executor. No remote access configuration changed.
- Specification and stage plan persisted. Stage 1 implementation next.

## Blockers and limits

Host service installation, private Tailscale exposure and reboot verification require access to the actual VM runtime. ChatGPT account model access must be tested through App Server, not assumed from the catalog or subscription description.

Updated observation: approved execution provides host runtime access. Read-only checks confirmed user systemd running, existing tmux sessions intact, Tailscale Running/online (`codex-server.tail5ebb9d.ts.net`), port 8787 free. Existing private Serve ports 443 and 3000 route to Chatwoot and must be preserved. A minimal bubblewrap OS namespace probe succeeded. No network listener changed yet.

## Stage 1 verified checkpoint

- Four foundation tests passed (Python 3.12.15): discovery/source preservation, stale revisions/path boundaries, competing durable task claims/dependencies/verification, restart and backup restore integrity.
- Actual CLI inventory found ten repositories; overview works. Database WAL confirmed in tests.
- Private generated memory adapts existing documentation without modifying project source. SQLite remains authoritative; revision-checked memory edits and locked atomic Markdown exports prevent stale overwrite.
- Structured tasks, dependencies, execution metadata, verification checkpoints and event log persisted. Expired leases block for manual/process reconciliation.
- Stage 1 acceptance passed. Next: Stage 2 authentication, dashboard and verified App Server chat.

## Stage 2 verified checkpoint

- Nine backend tests passed, Ruff checks passed, JavaScript syntax check passed. Web tests require approved host execution because the sandbox stalls the test client's thread portal; two hung test runs were identified and stopped, leaving unrelated sessions intact.
- Argon2id owner passwords; one-use local setup token; hashed expiring/revocable session tokens; HttpOnly/SameSite cookies; CSRF and origin/host checks; durable login throttling; restrictive CSP. No default password or public signup.
- Responsive dashboard, project selector, scoped persistent history/search, durable SSE, settings for five validated model roles, registry and memory editing, task creation and execution monitor.
- Real App Server initialized against Codex 0.161.0 using existing ChatGPT login. Catalog reported GPT-6.1 Sol and other installed models. Verified GPT-6.1 Sol/low through an actual completed streamed `MANAGER_READY` turn (~4.3s), then a new process resumed the same thread and completed `RESUME_READY`. Thread/turn IDs, output, latency and token usage persisted in isolated smoke-test DB.
- Other catalog models/efforts are selectable but not claimed as inference-verified. No separate API key/provider introduced. Chat tools, connectors, plugins and automatic subagent effort disabled; unknown capability requests fail closed.
- Context is bounded to selected summary/tasks/memory snippets; Codex native persistent compaction supported and lifecycle events recorded.
- Playwright verified one-time owner setup/login, ten-project dashboard, project switching and memory. Desktop 1440×1000 and Android 393×851 screenshots inspected; no page errors/horizontal overflow.
- Temporary loopback server started on 8787, awaiting owner setup. No private VPN route installed yet. Next: Stage 3 isolated task workers and control/recovery tests.


## Stage 3 verified checkpoint

- Fourteen tests pass, including confirmed interruption versus uncertain transport loss, workspace preservation, secret/path/symlink denial and an OS command sandbox check. Standalone local Git clones provide workspace isolation without shared mutable Git admin state. Source repositories remain unchanged.
- Real GPT-6.1 Sol/medium worker launched in a disposable repository, read README through the controlled manager_workspace capability, recorded reported progress and emitted persisted output. Completion moved the task to verifying, never directly to completed.
- A fresh worker process resumed the same persisted thread and isolated workspace and read the expected marker again. Evidence: scripts/smoke_worker.py.
- Logical agent, thread/turn, process start identity, branch/base commit, workspace, output, model/effort, usage, latency and errors persist. Two-worker cap and one active task per project enforced transactionally. Leases, interruption and restart recovery distinguish confirmed pause from unknown outcomes.
- Start/resume/interrupt/cancel controls and monitor added. Clones use committed baselines; uncommitted source changes remain untouched. Capabilities initially permit read/list/progress only. Next: Stage 4 planning, scheduling and independent verification; policy-gated edit/test capabilities follow in Stage 5.


## Stage 4 verified checkpoint

- Eighteen tests passed: atomic/idempotent plans, dependency validation, scoped management actions, project registration freeze, portfolio priority and limited scheduling, sandbox test evidence and independent file inspection.
- Real subscription end-to-end acceptance passed: planning role created one task, scheduler assigned it, worker read/checkpointed, existing unittest ran in bubblewrap, independent verification role inspected files and approved acceptance. Task completed with a durable verification record and checkpoint.
- The first real review correctly blocked completion because the review input omitted persisted progress-checkpoint evidence. Added actual capability audit and workspace status; the rerun passed. Never weakened the verification gate.
- Task completion, evidence, checkpoint and STATE memory update now commit atomically. Generated Markdown is a recoverable projection. Single-manager data-directory lock prevents a second service from disturbing live work.
- Conversational manager_action supports explicit delegated planning, prioritization, safe-checkpoint pause, project resume, scoped completion batches and temporary freeze of new projects. Cross-project actions require All Projects context. Brainstorming stays conversational.
- Automatic scheduler is owner-controlled, defaults paused, respects dependencies/priorities and two-worker cap. Verification requires owner-configured trusted commands; missing evidence blocks instead of claiming completion.
- Next: Stage 5 exact-operation approvals, configurable policies, write/test/commit controls, notifications and documented enforcement boundaries.


## Stage 5 verified checkpoint

- Twenty-three tests and the desktop/mobile browser check passed. Policies, project overrides, approval center, exact operation preview, risk acceptance for unavailable capabilities, audit history and optional in-app daily summaries implemented.
- Real coding acceptance passed in a disposable repository: worker read calculator.py, requested an exact ASK edit with its current hash, no change occurred before the simulated fixture-owner approval, approval was consumed, registered tests passed and independent reviewer approved completion. Original source still contained the original bug, demonstrating isolation. Script: scripts/smoke_coding.py.
- Controlled read/edit/delete/test/isolated-commit capabilities are enforced in Python. Test commands run with verified bubblewrap mount/PID/network isolation, no host home/credentials, cleared environment, timeout and bounded output. File capabilities reject traversal, symlinks and known credential paths. Commit approvals bind workspace state; hooks/global Git config are disabled.
- Policy changes invalidate outstanding grants; approvals expire and are single-use; operation ledger blocks uncertain replay. Worker leases and concurrency include awaiting-approval tasks. Independent verification obeys the same action policies.
- Merge/push/deploy/install/infrastructure/network/credential/external-service/communication/payment capabilities remain unavailable even if a UI policy says ALLOW. These limitations are displayed rather than falsely claimed as enforced unrestricted execution. Trusted Python/App Server run as the host owner, while model tools are brokered. Exact trust assumptions: docs/security.md.
- No external notification or communication sent. Only existing subscription-backed inference used. Next: production hardening, private deployment, recovery/backup checks and operational documentation.


## Stage 6 partial verified checkpoint

- 31 backend tests, Ruff and JavaScript syntax checks passed. New hardening covers physical worker crash without replay, isolated concurrent clones/global cap, approval expiry, singleton service/secure cookies, low-storage guard, current human-readable task/handoff exports, scoped policy-controlled document retrieval, orphan planning/review recovery and verified patches that apply to the original baseline.
- Latest desktop/mobile Playwright check passed without page errors or horizontal overflow. Scope includes secure setup/login, ten-project overview, project switching and memory.
- Controlled failure of the actual dedicated manager service verified restart-on-failure: only its MainPID was killed, systemd replaced it, private HTTPS recovered, ten registry identities persisted, database integrity and foreign keys passed. No unrelated session was signaled.
- Actual daily backup service produced an integrity-checked local SQLite backup. Restoration into a fresh disposable path preserved ten projects and passed integrity/foreign-key checks. Workspaces and Codex thread storage need separate protected backup for execution resumption.
- Dedicated linko-bot-manager.service and linko-bot-manager-backup.timer enabled/active. User linger=yes. Secure cookies on; loopback127.0.0.1:8787; authenticated private HTTPS at https://codex-server.tail5ebb9d.ts.net:8787/. No real owner account created; owner must choose credentials privately.
- Added only private Tailscale8787 using scoped sudo Serve command after ordinary daemon configuration was denied. Existing443/3000 preserved; no public Funnel configured. Correct-Origin invalid login returned401 rather than bypassing security. All seven existing tmux sessions and unrelated AI-Manager dirty documentation preserved.
- Service boundary smoke initially exposed missing Codex on systemd default PATH; installer now detects and persists the absolute executable path. Real disposable coding/approval/test/independent-review acceptance repeated under NoNewPrivileges.
- SQLite connection lifecycle hardened; native checkpoint documentation updates merge owner text with managed structured summaries under DB serialization. On-demand project reads, auth-expiring streams, low-space UI and downloadable verified patches added. No remote push or integration performed.

### Gates still open

- VM disk99% full, approximately445MB free: not enough capacity for large-project execution. Clone footprint checks and backups preserve128MB reserve. No unrelated files were removed. Owner-selected expansion/cleanup needed.
- Actual VM reboot deliberately deferred because unrelated work is active; enabled units/linger alone do not verify reboot recovery. Coordinate a safe window and inspect recovery afterward.
- Initial owner password/setup and trusted per-project verification commands are owner configuration. Scheduler defaults paused; no real project execution was automatically started.
- Unsupported sensitive capabilities remain unavailable as documented in docs/security.md. Additional capability acceptance is future work, not claimed complete. Stage6 and the overall permanent specification remain open.


## Execution reliability — 2026-10-10 (current evidence)

A real isolated task completed browser → persistent planning → scheduler → Codex → exact owner-approved edit → bubblewrap tests → independent reviewer → completed dashboard → manager restart. Task 4961242df6d14bf49f9018933f593fb3; worker 650160c740a241d6a452b7469a9961c2; reviewer 248295415c1f4579be31100639ca38ff. 48 pytest tests, Ruff, JavaScript syntax, existing desktop/mobile browser smoke and new actual control checks passed.

Root causes: missing trusted verification stalled completed worker output and dependencies; Resume replayed worker turns rather than verification; Start announced success before process/turn confirmation; scheduler repeatedly reconciled ineligible tasks; existing worker capability scope cannot perform arbitrary host/browser investigations. Focused corrections and evidence are in docs/execution-reliability.md. Conversation and Execute modes now have separate enforced behavior; scheduler settings and policies remain authoritative.

Production is active after a verified backup and dedicated-service restart (PID 675325), with original tasks/history/approvals/policies/authentication preserved. All ten production projects still lack owner-configured verification commands. Actual isolated uv verification fails because the sandbox excludes home-installed uv/dependencies; no security weakening or arbitrary command registration. The accepted fixture used its explicitly approved documented unittest command.

Current boot ec59df2b-f599-4ae8-8d9e-ad50791e441a differs from the old boot; 171 GiB available/18% used and 7.8 GiB RAM supersede old storage measurements. Original reboot/storage tasks remain cancelled; five requested investigation tasks retain their actual blocked/pending dispositions. No duplicate investigation or uncertain replay was created. Historical sections above describe earlier observations; this section and docs/execution-reliability.md govern current execution status.

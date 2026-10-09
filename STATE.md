# Verified state

Current: stages1–5 verified for supported capabilities; stage6 partially verified. Private service is live; full reboot/storage acceptance and initial owner setup remain open. Historical observations below retain the evidence available at each milestone.

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

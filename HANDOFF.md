# Combined memory / fetch recovery release — activation pending, 2026-10-12

Both existing assignments are preserved in one candidate; owner deployment authorization persists. Use only `memory-fetch-combined-v1-20261012` after the durable combined receipt confirms readiness. Activation uses manager `deploy_release` and the established idle/backup/verify/rollback helper. Do not deploy the memory-only release. See [fetch recovery](docs/FETCH_RECOVERY.md), [project memory](docs/PROJECT_MEMORY.md) and the private combined report. Older activation authorization/status wording below is historical.

# Compact project-memory candidate — activation pending, 2026-10-12

Candidate: `/home/codex/checkouts/linko-project-memory-20261012`. Read [docs/PROJECT_MEMORY.md](docs/PROJECT_MEMORY.md) and its private report for tests, preservation, exact release and activation procedure. Owner authorized implementation/testing/preparation only. Do not deploy automatically or run full project reviews without the explicit project button/start workflow. Live checkout remains on its original commit; this candidate preserves the existing helper and account-usage deliveries.

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

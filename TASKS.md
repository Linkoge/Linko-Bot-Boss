# Current delivery — compact navigation, 2026-10-10

Completed under the owner's current authorization: two-row responsive sticky navigation, durable meaningful-use ordering, alphabetical accessible dropdown/global context, editable persisted display names with stable identity, compact conversation/composer, per-context session drafts and lazy Memory loading. Added migration, persistence, identity, chat/global-context tests and expanded browser coverage. All35 backend tests, Ruff, JavaScript syntax and desktop/mobile browser checks pass. Deployed to the existing private service and verified data/asset preservation. Git checkpoint covers this delivery.

Remaining UI validation: a physical Android keyboard/safe-area check is optional follow-up; no reproduced UI failure remains. Existing Starlette/httpx warning remains a dependency-maintenance item. Draft persistence across full browser reloads is not implemented.

The earlier checkpoint task restrictions below are historical and superseded by this authorized feature/deployment task. The owner account now exists and current storage is172GiB available; initial-owner and old low-space observations below are no longer the current baseline. Existing durable reboot/storage tasks require their own acceptance evidence and were not automatically completed. Preserve all unrelated task states and infrastructure.

# Outstanding tasks — pre-reboot checkpoint, 2026-10-10

No new features are authorized during checkpoint preparation. After the owner reboots, resume with the read-only checklist in docs/operations.md. Stages 1–5 remain verified for supported capabilities; Stage 6 remains open.

| Priority | Task / durable ID | Dependencies and blocker | Acceptance criteria |
| --- | --- | --- | --- |
| P0 | Verify actual VM reboot recovery; e6f17f3b2859416b94412f01f642a882 | Owner performs reboot; this checkpoint is not reboot authorization. Currently blocked in DB. | Record new boot ID; manager/private HTTPS/backup timer recover; SQLite integrity, project IDs, memory and task states preserved; uncertain executions block without replay. Complete all ten post-reboot checks and record evidence before completing task. |
| P0 | Resolve low storage; 9e43efbd36b645d4a5d3937769ba4739 | Owner selects cleanup/expansion resources. Manual deletions reported, filenames unknown. Currently blocked in DB. | Provide capacity for selected project clones, test environments and dated backups while preserving unrelated source/runtime data; record before/after df and resource changes. Initial~795MiB later increased to~1.24GiB/97%; remeasure and assess the selected workloads, rather than assuming storage acceptance. |
| P1 | Initial owner account and login acceptance | No real owner account exists; privately chosen password required. Depends on working private HTTPS after boot. | Private CLI or token setup succeeds; owner signs in/out; no credentials in repository/logs. Do not invent credentials. |
| P1 | Configure project objectives, trusted verification argv and policies | Owner's desired scope and available isolated dependencies. | Selected project has clear acceptance criteria, trusted commands and effective policy preview; a scoped task verifies independently. Scheduling stays paused until chosen. |
| P1 | Protected off-VM backup and capacity/retention plan | Owner-selected storage destination and access; current backups are on the VM. | Restore from protected copy preserves DB/memory plus necessary workspaces/Codex thread storage; retention never silently removes required data. |
| P2 | Controlled dependency environments | Separate design, scope and owner-approved resource plan; storage first. | Dependencies available in isolated tests without exposing manager credentials/home/network or arbitrary shell; capability policies and failure/recovery tests pass. |
| P2 | Controlled Git integration/push and deployment | Reviewed capability design, scoped credentials/resources and exact approvals; dependency/workspace verification first. | Verified isolated results integrate only through explicit controlled operations, with non-idempotent outcomes audited and no uncertain replay. Remain unavailable until tested. |
| P3 | TestClient dependency deprecation | Existing httpx/Starlette warning; no test failure. | Assess httpx2 compatibility during a future dependency maintenance task; 31 tests still pass. No dependency changes in this checkpoint. |

The two DB task blocker texts retain the original 99%-full/no-reboot-window observations. They are historical context, not current disk measurements. They remain blocked until actual acceptance evidence permits a deliberate state transition; do not rewrite them to imply success merely because a reboot is planned.

Do not reopen verified stages without new evidence of failure. Never delete disk consumers, restart unrelated services, or merge/push/deploy as an incidental cleanup step.


## Execution reliability follow-up — 2026-10-10

- [x] Trace production chat/tasks/scheduler/policies/processes/App Server/verification/UI with DB and journal evidence.
- [x] Reproduce Start/Resume/Cancel and missing-verification replay in actual Chromium fixtures.
- [x] Correct launch receipts, scheduler visibility/eligibility, verification retry, safe resume/retry, stale identity/lease handling and chat execution receipts.
- [x] Real owner-approved reversible Codex edit → trusted tests → independent review → completed dashboard → persisted restart records.
- [x] 48 regression tests and static/browser checks; verified production backup and dedicated service rollout preserving existing records.
- [ ] Owner selects sufficient trusted verification commands and an isolated offline runtime for each production project; documented uv checks are currently unavailable in bubblewrap.
- [ ] Review the existing 5db40e44 → c78c0078 → 8589648a → ae1ba454 investigation plan and 5b9a10fd follow-up plan before any task retry or supersession. Their original DB statuses are preserved; this maintenance does not certify their task-specific acceptance.
- [ ] Decide any further reboot/storage acceptance work using current measurements and existing follow-up tasks. Cancelled original tasks are terminal and retained.

See docs/execution-reliability.md for exact task IDs, evidence, supported scope and remaining owner decisions.


## Owner-reported button feedback — 2026-10-10

- [x] Confirm exact live URL and correlate owner clicks with actual backend rejection logs.
- [x] Reproduce offscreen task errors on desktop and phone; add visible per-task feedback retained during polling.
- [x] Add prerequisite navigation and explain why verification retry cannot remove a missing-configuration blocker.
- [x] Validate isolated real endpoint Start rejection, verification rejection, configuration dialog and accepted cancellation; rerun 48 tests and existing browser smoke.
- [x] Verify updated static assets at the live URL without restarting or mutating production tasks.
- [ ] Existing production verification/runtime/acceptance decisions above remain open. Repeated Start/Retry clicks do not satisfy prerequisites.

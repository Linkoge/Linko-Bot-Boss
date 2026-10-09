# Outstanding tasks — pre-reboot checkpoint, 2026-10-10

No new features are authorized during checkpoint preparation. After the owner reboots, resume with the read-only checklist in docs/operations.md. Stages 1–5 remain verified for supported capabilities; Stage 6 remains open.

| Priority | Task / durable ID | Dependencies and blocker | Acceptance criteria |
| --- | --- | --- | --- |
| P0 | Verify actual VM reboot recovery; e6f17f3b2859416b94412f01f642a882 | Owner performs reboot; this checkpoint is not reboot authorization. Currently blocked in DB. | Record new boot ID; manager/private HTTPS/backup timer recover; SQLite integrity, project IDs, memory and task states preserved; uncertain executions block without replay. Complete all ten post-reboot checks and record evidence before completing task. |
| P0 | Resolve low storage; 9e43efbd36b645d4a5d3937769ba4739 | Owner selects cleanup/expansion resources. Manual deletions reported, filenames unknown. Currently blocked in DB. | Provide capacity for selected project clones, test environments and dated backups while preserving unrelated source/runtime data; record before/after df and resource changes. ~795MiB available at checkpoint remains a constraint. |
| P1 | Initial owner account and login acceptance | No real owner account exists; privately chosen password required. Depends on working private HTTPS after boot. | Private CLI or token setup succeeds; owner signs in/out; no credentials in repository/logs. Do not invent credentials. |
| P1 | Configure project objectives, trusted verification argv and policies | Owner's desired scope and available isolated dependencies. | Selected project has clear acceptance criteria, trusted commands and effective policy preview; a scoped task verifies independently. Scheduling stays paused until chosen. |
| P1 | Protected off-VM backup and capacity/retention plan | Owner-selected storage destination and access; current backups are on the VM. | Restore from protected copy preserves DB/memory plus necessary workspaces/Codex thread storage; retention never silently removes required data. |
| P2 | Controlled dependency environments | Separate design, scope and owner-approved resource plan; storage first. | Dependencies available in isolated tests without exposing manager credentials/home/network or arbitrary shell; capability policies and failure/recovery tests pass. |
| P2 | Controlled Git integration/push and deployment | Reviewed capability design, scoped credentials/resources and exact approvals; dependency/workspace verification first. | Verified isolated results integrate only through explicit controlled operations, with non-idempotent outcomes audited and no uncertain replay. Remain unavailable until tested. |
| P3 | TestClient dependency deprecation | Existing httpx/Starlette warning; no test failure. | Assess httpx2 compatibility during a future dependency maintenance task; 31 tests still pass. No dependency changes in this checkpoint. |

The two DB task blocker texts retain the original 99%-full/no-reboot-window observations. They are historical context, not current disk measurements. They remain blocked until actual acceptance evidence permits a deliberate state transition; do not rewrite them to imply success merely because a reboot is planned.

Do not reopen verified stages without new evidence of failure. Never delete disk consumers, restart unrelated services, or merge/push/deploy as an incidental cleanup step.

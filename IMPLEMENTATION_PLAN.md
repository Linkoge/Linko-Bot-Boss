# Implementation plan

Permanent requirements: [MASTER_SPEC.md](MASTER_SPEC.md). Advance only after the preceding acceptance gate passes. Each stage has its own local Git checkpoint; publishing is separate.

| Stage | Status | Acceptance gate |
| --- | --- | --- |
| 1. Inventory and foundation | Verified | Real repository discovery, non-destructive registry CRUD, durable memory, reconciliation, transactional task state and restore tests; working CLI overview |
| 2. Web interface and chat | Verified | Authenticated responsive UI; isolated persistent conversations; real App Server streamed chat; validated role model settings; disconnection/restart tests |
| 3. Codex integration | Verified | Real task worker launch/resume/interrupt, persistent events and process identity, monitor; worker crash test |
| 4. Autonomous orchestration | Verified | Goal-to-task planning, dependency scheduling, two-worker cap, leases, worktree isolation, evidence-based verification and uncertain-outcome recovery |
| 5. Policies and controls | Verified for supported capabilities | Durable ALLOW/ASK/DENY and overrides, approvals bound to exact operations, policy audit, verified OS boundary and restricted capabilities, notifications |
| 6. Production hardening | Partially verified; storage/reboot gates open | Concurrent/recovery/security/restore tests; private Tailscale deployment, service restart and VM reboot verification; operational docs |

## Ordered work

1. Save requirements and inspect existing projects and runtime.
2. Implement schema, registry, managed memory, tasks, events, backups and CLI tests.
3. Add authentication and responsive UI with deterministic portfolio status first.
4. Verify installed App Server protocol/catalog/account; implement durable streaming chat.
5. Advance subsequent stages only after verified gates; record external blockers and continue independent work.

## Environment limits

The repository is `/home/codex/projects/Linko-Bot-Manager`. Approved native execution provides actual VM runtime access. Private service, HTTPS and backups are deployed and tested. Full reboot acceptance remains unperformed to preserve unrelated sessions; latest measured storage is98% full (~795MiB free), after owner-reported manual cleanup; the prior99% measurement remains historical. See TASKS.md and docs/verification.md. No unrelated repository/process/remote-access configuration may be changed.

## Local checkpoints

- Stage 1: e4ef65d
- Stage 2: 86f8452
- Stage 3: d1f0a6a
- Stage 4: fc3f6bf
- Stage 5: cfe5427
- Stage 6 partial hardening/deployment: 0ccfaa6ebd955efe02d6968301cb875c9b463e8f. Stage remains open until the external gates pass.

## 2026-10-10 reboot preparation

Documentation-only development checkpoint; all31 existing tests passed again. Service/startup, live integrity, 50 memory exports, empty production chat/thread stores and all existing backups inspected without restarting anything. Full inventory is in docs/inventory.md; post-reboot acceptance commands in docs/operations.md; checkpoint reference in HANDOFF.md. The owner plans to reboot; no reboot or service restart is authorized in this task. Stage6 remains partially verified until actual post-boot evidence and storage acceptance pass.


## Execution reliability acceptance — 2026-10-10

Completed the focused runtime investigation and control corrections. Actual acceptance fixture proves owner browser command → persistent task → configured scheduler → real Codex process/thread/turn → hash-bound ASK edit → approved isolated unittest → independent reviewer → completed dashboard → restart persistence. Production rollout preserved existing records. Validation: 48 pytest, Ruff/JS and real Chromium controls/mobile smoke.

Current production readiness remains project-specific: all ten projects need owner-configured adequate verification; documented uv commands fail inside the existing isolated runtime; host/browser investigations exceed worker capability scope. Preserve pending/blocked/cancelled histories and reconcile acceptance before explicit verification retry or owner-selected supersession. Do not weaken the sandbox or replay uncertain work. Detailed evidence and decisions: docs/execution-reliability.md.


## Visible control feedback follow-up — 2026-10-10

Completed the owner-reported live UI investigation: correlate real requests/rejections, reproduce offscreen errors, implement per-card receipts and prerequisite navigation, exercise real isolated API responses at desktop/phone viewports, and verify served asset hashes. Validation passed: 48 pytest, Ruff, JS syntax, existing browser smoke and new task feedback regression. Production task state and security/verification gates remain intact. Adequate production verification and original task acceptance remain the previously documented external prerequisites.


## High autonomy upgrade delivery — 2026-10-10

All seven development phases have been executed incrementally using the existing architecture (checkpoints6511cb3,c1ec386,3e24e70). Baseline/activation contract preceded policy implementation. Profiles/permissions, reusable command discovery/approval, task-specific evidence, lease-safe dispatch/recovery, completion-safe local integration and two-row dashboard controls are implemented. Verification:74 regression tests, static checks, desktop/mobile Chromium and a real ChatGPT-authenticated Codex fixture that completed and survived its service restart; unauthorized deployment was blocked. Full implementation/evidence and exact consolidated proposal:docs/autonomy-upgrade.md.

Remaining operational gate is the owner's explicit rollout/global High Autonomy activation decision, required by this request. On approval, preserve live records with an integrity-checked online backup, apply additive schema5 via dedicated backend rollout, activate the fresh authenticated policy proposal, verify live backend/frontend consistency and refresh project memory. No broader policy/command activation or production service restart occurred during development. Existing scheduler scope/budget are preserved; adequate reusable production verification, supported capabilities and project merge targets remain owner-scoped prerequisites, not conclusions inferred from successful fixtures.

Final completion-state checkpoint: 5de4607. Verified workers and verification-controller records now reach completed before the locked handoff export; the 74-test regression confirms backend/history/memory consistency.


## Latest operational state — approved autonomy rollout, 2026-10-10

Owner approval was applied. Dedicated backend is live on schema5; global High Autonomy is active until revoked, inherited by all10projects. Existing scheduler manager-only scope/limit6 and technical isolation/sensitive boundaries were preserved. Verified pre-rollout backup and exact migration comparison preserved every existing row/column; restart preserved completed task, grants, commands, receipts, all original IDs/events/messages/criteria/blockers and owner/session data. Service restarted PID966467→976839; private HTTPS/TLS and asset hashes pass; auth remains enforced.

Manager runtime blocker is resolved with an immutable scoped offline Python3.12/dependency/Ruff package, config-hash binding and read-only runtime mount. Its two reusable commands run67portable tests +Ruff inside no-network/no-home isolation; full native suite82tests passes. Fifteen host-runtime tests remain owner-supervised outside the nested sandbox. Real deployed Codex task3684e98011fb4bf8bcc5a5f6776ab89c automatically dispatched without ASK, passed independent commands/actual-file review and completed/verified with unchanged files/commits; persistence passed a real service restart.

Fourteen obsolete nonterminal requests were cancelled with individual evidence/receipts; three prior cancelled tasks remained terminal. All original history is retained and none was falsely completed. Queue now has17historical cancelled and1verified completed, no pending/blocked/running work or pending approvals. Real chat administration execution0181b4e239334e96a272041b686b334b produced event100269/requestowner-chat-manager-discovery-20261010. Scoped grants permit supported manager administration through chat or owner CLI; dashboard can inspect/revoke them, workers cannot grant authority. Other-project commands/runtime and explicit merge targets remain project-scoped prerequisites; sensitive/external capabilities stay unavailable.

Details, exact scopes/commands/dispositions and private evidence paths:docs/autonomy-upgrade.md; ~/.local/share/linko-bot-manager/autonomy-rollout-evidence-20261010. Implementation checkpointsb4a6ff7,f3edc9c. No push, unrelated-project edits, credential exposure, host security/network change or paid API was introduced. Existing earlier pending-approval/blocker sections above are historical.

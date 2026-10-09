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

The repository is `/home/codex/projects/Linko-Bot-Manager`. Approved native execution provides actual VM runtime access. Private service, HTTPS and backups are deployed and tested. Full reboot acceptance remains unperformed to preserve unrelated sessions; storage is 99% full. See TASKS.md and docs/verification.md. No unrelated repository/process/remote-access configuration may be changed.

## Local checkpoints

- Stage 1: e4ef65d
- Stage 2: 86f8452
- Stage 3: d1f0a6a
- Stage 4: fc3f6bf
- Stage 5: cfe5427
- Stage 6 partial hardening/deployment: next local checkpoint; identify using git log. Stage remains open until the external gates pass.

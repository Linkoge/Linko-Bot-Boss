# Deployment helper — first activation pending, 2026-10-12

Controlled helper, scoped manager request/status, failure/rollback/concurrency/idle checks and external
bootstrap are prepared. See [deployment helper](docs/DEPLOYMENT_HELPER.md) and its private report.
The combined startup-selection/zero-badge/To-Do/Markdown release is already deployed and preserved.
Do not restart an active releasing worker. Deferred memory compaction remains in To-Do, unchanged.

# Development priorities

Current architecture and evidence: docs/RADICAL_SIMPLIFICATION_IMPLEMENTATION.md.

The radical simplification is implemented. Preserve the native manager → Codex → manager workflow and essential durability/authentication/project ownership. Diagnose actual failures before adding components, AI roles, policies, states or abstractions. Historical acceptance checklists for deleted supervision machinery are superseded and retained in Git history.

## Deferred backlog — not scheduled

The authenticated **To-Do** view and SQLite `todos` records are the authoritative future-work backlog.
The original note “Keep project memory concise; archive detailed history” is linked by source assignment
`2b15765038784009b8f6ad8e0e7008ea` and migrated once at schema 11 activation, including its full details
and recording objective. Original assignment/execution evidence remains preserved. Memory redesign remains
deferred, requires an architectural proposal when scheduled, and is not authorized by this UI delivery.

# Current work

Minimal delivery pipeline implemented (see [STATE.md](STATE.md), [docs/AUTO_DEPLOY.md](docs/AUTO_DEPLOY.md)); pending
manager merge and the owner's one-time `python3 scripts/install_deploy.py`. Earlier delivery/deployment task notes
described the retired helper workflow and are in Git history.

# Development priorities

Current architecture and evidence: docs/RADICAL_SIMPLIFICATION_IMPLEMENTATION.md.

The radical simplification is implemented. Preserve the native manager → Codex → manager workflow and essential durability/authentication/project ownership. Diagnose actual failures before adding components, AI roles, policies, states or abstractions. Historical acceptance checklists for deleted supervision machinery are superseded and retained in Git history.

## Deferred backlog — not scheduled

The authenticated **To-Do** view and SQLite `todos` records are the authoritative future-work backlog.
The original note “Keep project memory concise; archive detailed history” is linked by source assignment
`2b15765038784009b8f6ad8e0e7008ea` and migrated once at schema 11 activation, including its full details
and recording objective. Original assignment/execution evidence remains preserved. Memory redesign remains
deferred, requires an architectural proposal when scheduled, and is not authorized by this UI delivery.

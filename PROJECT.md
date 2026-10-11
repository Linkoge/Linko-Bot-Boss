# Linko Bot Manager

A single-owner conversational interface and lightweight orchestrator for a software portfolio.

The current architecture is User → AI Manager → native Codex worker → AI Manager → User. The manager delegates complete objectives and receives results automatically. Codex performs its own investigation, implementation, validation, debugging and Git work with broad autonomous access.

FastAPI, plain browser assets, SQLite and Codex App Server remain. Authentication, durable history, project registry, exclusive project claims, process ownership, native thread continuation and backups are essential infrastructure. Planning, DAGs, independent verification/review, capability brokers, permission profiles, command registration, custom command isolation and integration controllers have been removed.

MASTER_SPEC.md describes current requirements. The radical simplification plan and implementation record describe the change. Older STATE/HANDOFF/stage reports retain historical observations. Runtime state is outside Git; historical SQLite evidence and private owner notes are preserved.

The existing private hosting and unrelated projects/services remain intact. Deployment affects only the dedicated manager service.

# Linko Bot Manager

A single-owner conversational interface and lightweight orchestrator for a software portfolio.

The current architecture is User → AI Manager → native Codex worker → AI Manager → User. The manager delegates complete objectives and receives results automatically. Codex performs its own investigation, implementation, validation, debugging and Git work with broad autonomous access.

FastAPI, plain browser assets, SQLite and Codex App Server remain. Authentication, durable history, project registry, exclusive project claims, process ownership, native thread continuation and backups are essential infrastructure. Planning, DAGs, independent verification/review, capability brokers, permission profiles, command registration, custom command isolation and integration controllers have been removed.

MASTER_SPEC.md describes current requirements. The cleanup report records current validation and deployment. Historical milestones remain in Git. Runtime state is outside Git; historical SQLite evidence and private owner notes are preserved.

The existing private hosting and unrelated projects/services remain intact. Deployment affects only the dedicated manager service.

Project-memory behavior and budget/review contract: [docs/PROJECT_MEMORY.md](docs/PROJECT_MEMORY.md). The brief is derived current navigation; this file and existing authoritative docs retain their roles.

# Operations handoff

Read [README.md](README.md), [docs/operations.md](docs/operations.md) and [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md) for the current workflow, deployment and validation.

The dedicated user service runs from this repository with its existing Python environment and private HTTPS listener. Preserve the production database, native Codex threads, owner notes, projects and unrelated services. Use disposable fixtures for tests. Historical unfinished assignments stay waiting until explicitly continued; no migration replays or merges old clones.

Earlier handoffs and deployment evidence remain in Git history. They describe superseded supervision paths and are not current instructions.

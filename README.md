# AI Manager

Self-hosted Python manager for a single owner's software portfolio. Requirements: [MASTER_SPEC.md](MASTER_SPEC.md). Progress: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [STATE.md](STATE.md).

## Local foundation

```bash
uv sync --locked
uv run python -m ai_manager.cli discover
uv run python -m ai_manager.cli overview
uv run pytest -q
```

Discovery defaults to `~/projects`. Configure colon-separated `AI_MANAGER_PROJECT_ROOTS` and `AI_MANAGER_DATA` if needed. Runtime database and generated Markdown memory live in `~/.local/share/linko-bot-manager`, outside Git. Source repositories are read only during discovery. Removing a registry entry retains history and never deletes source files. Re-add a removed entry by changing its status through administrative controls; automatic discovery will not resurrect it.

SQLite WAL and full synchronous commits keep short writes durable. Revisions reject stale updates; task claiming is transactional, respects dependencies, and permits one editing task per project with a global cap of two. An expired lease blocks for reconciliation; it never automatically repeats an uncertain operation.

## Backups

```bash
uv run python -m ai_manager.cli backup /path/to/new-backup.sqlite3
# Stop the service before switching databases. Restore to a NEW path:
uv run python -m ai_manager.cli restore /path/to/new-backup.sqlite3 /path/to/fresh-data/manager.sqlite3
```

The online SQLite backup includes authoritative memory, conversations, tasks and audit events; integrity is checked. Generated Markdown exports can be recreated from database memory. Never copy a live database without its WAL or overwrite a running database. Keep the Codex thread store independently backed up on the host for thread resume; do not include authentication tokens in repository backups.

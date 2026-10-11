# Current state

The service uses direct manager delegation, native autonomous Codex workers, a global project queue and durable automatic manager continuation. Task lifecycle: queued → running → completed/failed; waiting and cancelled preserve operational pauses and user intent. Conversation, Auto and Execute use the same manager path.

Current validation, compatibility and deployment evidence are in [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md). Runtime records live in SQLite outside Git. Earlier milestone observations remain in Git history.

# Compact project-memory candidate — activation pending, 2026-10-12

Versioned compact manager brief, focused worker context, recent-delivery archive and explicit owner review/diff/apply/rollback are implemented for testing/preparation. No deployment is requested. Current known behavior and detailed evidence: [project memory](docs/PROJECT_MEMORY.md). Existing production acceptance and historical evidence remain unchanged.

# Current state

The service uses direct manager delegation, native autonomous Codex workers, a global project queue and durable automatic manager continuation. Task lifecycle: queued → running → completed/failed; waiting and cancelled preserve operational pauses and user intent. Conversation, Auto and Execute use the same manager path.

Stage 3 adds deterministic automatic recovery for workers and manager conversations, persisted backoff and shared account usage gating (schema 9). Current validation and deployment evidence are in [docs/AUTOMATIC_RECOVERY.md](docs/AUTOMATIC_RECOVERY.md). Runtime records live in SQLite outside Git. Earlier milestone observations remain in Git history.

# Architecture decisions

Use the underlying Codex agent for investigation, implementation, validation and Git. The manager owns conversation, complete-objective delegation, persisted execution state and automatic result delivery. Keep one coding worker per project and one global capacity limit.

Retain authentication, history, project settings, model/effort settings, explicit pauses/cancellation, catastrophic confirmation, native-thread recovery and backups. Removed supervision machinery stays removed; add complexity only for a demonstrated requirement.

Historical decisions and evidence remain in Git history; [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md) records this cleanup.

- 2026-10-12: Use a derived versioned project brief in existing private SQLite memory/settings, preserving authoritative docs/history. Bound default context conservatively with a UTF-8 byte token upper bound; reject overflow and retain explicit condensation/resolution provenance. Full reconciliation is exclusively owner-started with candidate comparison, explicit Apply and rollback; routine delivery updates remain separate. Do not copy the full brief/history or manager-only reply preference into workers. Details: docs/PROJECT_MEMORY.md.

- 2026-10-13: Persist full-job follow-up on existing assignment IDs in settings, separately from worker outcomes. Reuse native manager/result delivery, transactional claims, idle grace and retry backoff. Never synthesize tickets, replay old work or infer owner permission from worker results. New model selections resolve catalog family versions; existing execution/assignment routes remain exact. Docs: AUTOMATIC_RECOVERY.md and MODEL_SETTINGS.md.

# Architecture decisions

Use the underlying Codex agent for investigation, implementation, validation and Git. The manager owns conversation, complete-objective delegation, persisted execution state and automatic result delivery. Keep one coding worker per project and one global capacity limit.

Retain authentication, history, project settings, model/effort settings, explicit pauses/cancellation, catastrophic confirmation, native-thread recovery and backups. Removed supervision machinery stays removed; add complexity only for a demonstrated requirement.

Historical decisions and evidence remain in Git history; [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md) records this cleanup.

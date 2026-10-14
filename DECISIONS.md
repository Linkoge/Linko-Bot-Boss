# Architecture decisions

Use the underlying Codex agent for investigation, implementation, validation and Git. The manager owns conversation, complete-objective delegation, persisted execution state and automatic result delivery. Keep sequential-default project admission and one global capacity limit. The 2026-10-14 owner approval supersedes unconditional exclusivity with explicit per-request, isolated parallel admission; docs/PARALLEL_EXECUTION.md defines dependency/conflict and serialized integration guards.

Retain authentication, history, project settings, model/effort settings, explicit pauses/cancellation, catastrophic confirmation, native-thread recovery and backups. Removed supervision machinery stays removed; add complexity only for a demonstrated requirement.

Historical decisions and evidence remain in Git history; [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md) records this cleanup.

- 2026-10-14: Adopt Option B deterministic Small/Normal/Risky verification for new assignments. Paths, request text and assignment kind set a floor; manager requests and continuations may only raise it. Reclassify actual branch changes after completion. Every tier runs fast smoke; Small adds touched-file tests/build/lint, Normal adds the full backend suite once at the end, Risky adds one cheap read-only diff review. Review findings, unavailable reviews and escalations go to the manager for follow-up rather than certifying completion. Preserve pre-tier assignments and owner model policy. Batch only related queued, never-started Small requests from the same conversation that remain Small together. Keep worker replies short and full evidence internal. Retain the helper's existing automatic rollback on health failure, verified in a throwaway copy. Contract and validation evidence: [docs/VERIFICATION_TIERS.md](docs/VERIFICATION_TIERS.md). No deployment in this assignment.

- 2026-10-12: Use a derived versioned project brief in existing private SQLite memory/settings, preserving authoritative docs/history. Bound default context conservatively with a UTF-8 byte token upper bound; reject overflow and retain explicit condensation/resolution provenance. Full reconciliation is exclusively owner-started with candidate comparison, explicit Apply and rollback; routine delivery updates remain separate. Do not copy the full brief/history or manager-only reply preference into workers. Details: docs/PROJECT_MEMORY.md.

- 2026-10-13: Persist full-job follow-up on existing assignment IDs in settings, separately from worker outcomes. Reuse native manager/result delivery, transactional claims, idle grace and retry backoff. Never synthesize tickets, replay old work or infer owner permission from worker results. New model selections resolve catalog family versions; existing execution/assignment routes remain exact. Docs: AUTOMATIC_RECOVERY.md and MODEL_SETTINGS.md.

- 2026-10-13: Rename current repository/product to Linko-Bot-Boss / Linko Bot Boss; retain Bot Boss display name, existing project ID/path, auth/runtime/storage identities and original prepared artifacts. Integrate naming over latest pending To-Do/chat release in a separate candidate; metadata changes are live, code activation is pending and this assignment must not deploy. See STATE.md and private repo-rename report.

- 2026-10-14: Owner explicitly approved bundled opt-in same-project parallel execution after design.
  Consent belongs to one current owner request, with refinements staying on the original assignment.
  Extend existing manager/native worker architecture with deterministic Git/SQLite ownership and normal
  manager assessment; no additional AI router/reviewer. Serialize source integration/combined validation
  in an existing retained worker workspace, preserve conflict branches and exact deployment receipts.
  Any nonempty draft independently retains expanded composer/Send; timer and audio actions share the
  bottom-left row with conditional ⇈/Send at right. Contract: docs/PARALLEL_EXECUTION.md and docs/chat-history.md.

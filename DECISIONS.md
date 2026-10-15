# Delivery decision — 2026-10-15

Owner explicitly increased the permanent repair/follow-up limit from three to five on 2026-10-15,
preserving cumulative usage and existing holds. Existing executions keep their injected budgets.
Activation and held model/recording continuation: [docs/AUTO_DEPLOY.md](docs/AUTO_DEPLOY.md).

Owner (single user, private Tailscale) wants deployment simple and automatic. Replaced the heavyweight machinery
(hash-pinned helper service, immutable release bundles/manifests/evidence, deployment intents/approvals, exact-commit
binding, risk tiers, automatic AI security review) with: worker smoke + targeted tests once and at most five repair
rounds; sequential merge of passed work to main; one deploy script (idle wait, SQLite backup, restart, health check,
automatic code+database rollback, short persisted result). Auto-deploy ON means merge and deploy, OFF neither; explicit
owner holds/pauses are honored; one global coding worker; usage-limit gate stays removed. Full backend runs once, in the
deploy script. Destructive deletion/force-push needs confirmation; ordinary edits proceed. Authorized prompts may
contain secrets. Memory updates follow meaningful facts, not a per-task stage. Contract: docs/AUTO_DEPLOY.md.
Earlier dated decisions below are historical; those about tiers, helper, publication, intents and manifests are superseded.

# Architecture decisions

Use the underlying Codex agent for investigation, implementation, validation and Git. The manager owns conversation, complete-objective delegation, persisted execution state and automatic result delivery. Keep sequential-default project admission and one global capacity limit. The 2026-10-14 owner approval supersedes unconditional exclusivity with explicit per-request, isolated parallel admission; docs/PARALLEL_EXECUTION.md defines dependency/conflict and serialized integration guards.

Retain authentication, history, project settings, model/effort settings, explicit pauses/cancellation, catastrophic confirmation, native-thread recovery and backups. Removed supervision machinery stays removed; add complexity only for a demonstrated requirement.

Historical decisions and evidence remain in Git history; [docs/CODEBASE_CLEANUP.md](docs/CODEBASE_CLEANUP.md) records this cleanup.



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

- 2026-10-14..15: Tiered verification, immutable publication, deployment intents and the helper service were built and then retired by the decision above (see Git history).

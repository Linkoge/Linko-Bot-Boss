# Security boundaries

Linko is a private single-owner coding service. Argon2 owner authentication, one-time local setup, hashed/revocable secure sessions, CSRF/origin/host checks, login throttling and CSP remain. Private hosting remains loopback plus the existing Tailscale HTTPS listener. No public signup or new API billing provider is introduced.

The manager runs read-only Codex threads with a small orchestration tool. Workers use native Codex tools in the registered project directory with broad access (`danger-full-access`, on-request user interaction). There is no capability broker, action-policy matrix, independent verification or mandatory code reviewer. Codex chooses validation and reports limitations.

Workers run as the service account and can potentially access other host files, manager data and credentials available to that account. Read-only manager settings and database file permissions do not isolate a malicious full-access worker. Old bubblewrap no-host-home isolation and per-command resource budgets no longer apply. A global capacity limit, bounded stored output and process ownership remain; they are not equivalent quotas.

Codex is instructed to preserve user work and ask before genuinely catastrophic broad destruction: wiping a repository/volume, dropping a production database or destroying shared infrastructure. Native questions are stored durably, surfaced in the conversation and answered explicitly. Routine development needs no approval. This boundary depends on the agent recognizing the action; neither native full-access execution nor regex analysis guarantees interception of arbitrary destructive programs. A completed native turn is not independent proof that its objective succeeded.

The queue uses transactional sequential-default project claims, unique isolated-workspace ownership and a single-manager lock. Explicit current-request parallel permission can admit safe isolated same-project workers; docs/PARALLEL_EXECUTION.md defines backend admission and shared-write restrictions. Integration/preparation/deployment stay serialized. These are same-user ownership safeguards, not a shell sandbox. Claims are retained while a detached owned process remains unresolved. Native thread inspection is used after restart; uncertain launch identities are retained for inspection rather than blindly replayed. Arbitrary external side effects are not transactionally deduplicated.

Existing historical verification, integration, approval and operation records remain evidence of the old architecture, not active execution requirements. Legacy unfinished tasks are not automatically replayed during migration. Owner-authored private notes remain readable; generated Markdown projections stop updating.

Database backups preserve structured state. Native Codex thread storage and actual project files/Git state must also be retained for complete recovery. Restoring a database cannot undo edits, commits or external effects.

Deployment is automatic per project (Auto-deploy ON) or on an explicit owner `deploy` request; worker prose and internal
notices never request it. Owner holds, pauses and OFF stop merges and deploys before anything changes. The deploy script
backs up SQLite, health-checks the restarted service (auth boundary included) and rolls code and database back on failure;
failed or rolled-back deploys are never retried automatically. Contract: [delivery pipeline](AUTO_DEPLOY.md).

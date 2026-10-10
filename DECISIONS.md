# Decisions

- 2026-10-09: Use FastAPI and SQLite instead of reusing the unrelated Chatwoot stack. Keeps the Python backend and installation simple; supports streaming without new infrastructure.
- 2026-10-09: Store generated project memory under the manager's private data directory. Read existing project documentation and preserve it; do not write across discovered repositories during inventory. Owner-controlled changes may later export memory to source repositories.
- 2026-10-09: Bind loopback by default. Host systemd and Tailscale integration remains a measured deployment gate. Do not alter existing VPN listeners.
- 2026-10-09: Use the installed Codex App Server and its existing authentication. Do not introduce a separately billed API or copy credentials.
- 2026-10-09: Use standalone shallow local clones rather than shared Git worktree admin storage. Preserves source checkouts and makes scoped Git capabilities easier to audit; reviewed patches can be downloaded without merging.
- 2026-10-09: Broker a small execution capability set and run trusted test argv in bubblewrap. Unsupported sensitive actions remain unavailable regardless of policy setting. App Server is trusted same-user transport, not a separate Unix-user security boundary.
- 2026-10-09: Add only private HTTPS port8787 to existing Tailscale Serve; preserve Chatwoot ports443/3000. Install dedicated user service/backup timer; persist absolute Codex path because systemd's default PATH omits ~/.local/bin.
- 2026-10-09: Keep full reboot acceptance open to preserve unrelated active sessions. Preserve a storage reserve and report the real 99%-full VM rather than deleting unrelated content. Local backups have no implicit destructive retention or external transmission.

- 2026-10-10: Treat reboot preparation as documentation/persistence work only. Preserve MASTER_SPEC.md byte-for-byte and existing documentation structure; no feature, dependency, policy or service changes. Re-test existing persistence fixtures and inspect the live DB with read-only connections instead of restarting it.
- 2026-10-10: Distinguish measured795MiB/98% disk availability from prior445MB/99% and the owner's manual-cleanup report. Directory sizes do not prove data is disposable; no pruning/deletion is authorized by checkpoint inventory.
- 2026-10-10: Production chat/thread/owner/approval stores are genuinely empty. Record this explicitly and retain smoke-test evidence separately; do not invent production history or claim actual reboot survival before post-boot checks.
- 2026-10-10: Use an online verified private SQLite backup plus an isolated restore test; keep runtime DBs/logs/authentication and test artifacts outside Git. Database backups do not replace protected Codex rollout/workspace backups.

- 2026-10-10: Use the existing FastAPI/SQLite/plain-JavaScript stack for a focused two-row sticky navigation. Keep every existing section and move repository/session controls into Settings and the project menu.
- 2026-10-10: Keep original project name immutable through rename; add optional display_name and last_used_at in schema4. Recency updates atomically with meaningful events and backfills historical usage. All Projects remains a null global context. Permit duplicate display names and disambiguate labels with original name/short stable ID rather than changing identities or paths.
- 2026-10-10: Fetch documentation/Git observation only on Memory navigation; keep drafts per context in session memory and guard asynchronous context switches/submission completion. Use visualViewport/dynamic height plus safe-area spacing for composer access without adding a third navigation row.
- 2026-10-10: Deploy only the dedicated manager service after a verified private backup; compare durable records and deployed asset hashes. Preserve existing listeners/auth/policies/scheduler settings. Browser fixture credentials never touch production. Physical Android testing and original Stage6 acceptance remain separate from simulated viewport checks.


## 2026-10-10 — Execution evidence and recovery controls

A launch acknowledgement means accepted/queued; only a recorded App Server process/thread/turn establishes execution. Completed worker output and independent verification have separate retry controls. Missing trusted commands gate completion, not initial dispatch. Saving owner verification configuration never silently unblocks or replays existing work.

Resume selects the actual latest worker, preserves rejected task state, validates isolated workspace and PID/start identity, and requires explicit reconciliation for uncertain outcomes. Safe launch retry requires no persisted thread/turn. Startup preserves terminal dispositions and clears obsolete leases. Detached expired work blocks instead of replaying.

Conversation mode rejects mutation. Execute mode deterministically creates a persistent plan and attempts dispatch under the existing scheduler policy; Auto uses manager tools and adds authoritative receipts or an explicit no-task result. No autonomy/policy grants are inferred from model promises. Existing host/browser investigation tasks remain capability-limited; do not add unrestricted shell merely to satisfy them.

The owner approved only the fixture's documented unittest command and exact calculator subtraction→addition edit. Actual ASK was consumed once, independent tests/review passed, and restart retained completion. No production verification command or policy was approved by that fixture decision. Documented uv test commands need a trusted isolated runtime; do not expose host home/network as a shortcut. See docs/execution-reliability.md.


## 2026-10-10 — Task feedback must be visible at the action

A status unchanged after HTTP 409 is correct but insufficient feedback. Show requesting and authoritative accepted/rejected/failed responses in the originating task card, retain them across polling, and scroll the receipt into view. Explain blockers before the action and provide direct navigation to unfinished prerequisites. Keep the backend dependency, approval and verification gates unchanged. UI fixture rows are explicitly display-only data and never evidence of Codex execution. Static-only deployment requires verifying the live served bytes, without a service restart.


## 2026-10-10 — Autonomy upgrade decisions

Retain legacy permissions until explicit consolidated owner activation; a tested recommended profile is not an implicit grant. Preserve owner overrides and scheduler scope/budget. Policy precedence is technical capability/workspace boundaries, explicit project policy, explicit global policy, selected profile defaults; authenticated owner overrides are recorded with prior effective state. Profile hashes/epochs and revocation prevent stale authorization, and workers have no security-administration tool. Maximum policy declarations cannot create missing infrastructure/external capabilities.

Bind reusable command approvals to exact argv, cwd, sandbox environment and configuration hashes. Discovery never executes repository scripts or approves candidates. Renewal may use confirmed isolated worker configuration without mutating source. Use system runtimes/offline project artifacts and fail closed when the isolated environment is inadequate. Disposable build copies allow generated artifacts while preserving original inputs; systemd cgroups/time/log/file guards bound execution. The monitored build growth budget is not a disk quota.

All task kinds require explicit acceptance and independent evidence, not an AI success claim. Record partial/failed/blocked/unverified separately. Feed independent reviewers consumed approval receipts and operation states; unsupported host state and unfetched external claims remain limitations. Recover only known safe blockers after prerequisite identity changes; do not replay uncertain worker/integration operations. Required merge/commit obligations finish before completion/checkpoint. Clean, baseline-matching fast-forward source merges need explicit project target authorization; blocked integration needs owner reconciliation and fresh verification.

Use installed ChatGPT-authenticated Codex catalog/routes, not invented model/cost assumptions or separately billed APIs. The actual gpt-6.1-sol/medium fixture passed; catalog names alone do not prove subscription access. Preserve old-backend frontend compatibility while approval-gated backend rollout is pending. Keep private evidence outside Git and preserve all historical docs/MASTER_SPEC.md. See docs/autonomy-upgrade.md.

Final completion-state checkpoint: 5de4607. Verified workers and verification-controller records now reach completed before the locked handoff export; the 74-test regression confirms backend/history/memory consistency.

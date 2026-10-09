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

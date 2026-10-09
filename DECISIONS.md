# Decisions

- 2026-10-09: Use FastAPI and SQLite instead of reusing the unrelated Chatwoot stack. Keeps the Python backend and installation simple; supports streaming without new infrastructure.
- 2026-10-09: Store generated project memory under the manager's private data directory. Read existing project documentation and preserve it; do not write across discovered repositories during inventory. Owner-controlled changes may later export memory to source repositories.
- 2026-10-09: Bind loopback by default. Host systemd and Tailscale integration remains a measured deployment gate. Do not alter existing VPN listeners.
- 2026-10-09: Use the installed Codex App Server and its existing authentication. Do not introduce a separately billed API or copy credentials.
- 2026-10-09: Use standalone shallow local clones rather than shared Git worktree admin storage. Preserves source checkouts and makes scoped Git capabilities easier to audit; reviewed patches can be downloaded without merging.
- 2026-10-09: Broker a small execution capability set and run trusted test argv in bubblewrap. Unsupported sensitive actions remain unavailable regardless of policy setting. App Server is trusted same-user transport, not a separate Unix-user security boundary.
- 2026-10-09: Add only private HTTPS port8787 to existing Tailscale Serve; preserve Chatwoot ports443/3000. Install dedicated user service/backup timer; persist absolute Codex path because systemd's default PATH omits ~/.local/bin.
- 2026-10-09: Keep full reboot acceptance open to preserve unrelated active sessions. Preserve a storage reserve and report the real 99%-full VM rather than deleting unrelated content. Local backups have no implicit destructive retention or external transmission.

# Decisions

- 2026-10-09: Use FastAPI and SQLite instead of reusing the unrelated Chatwoot stack. Keeps the Python backend and installation simple; supports streaming without new infrastructure.
- 2026-10-09: Store generated project memory under the manager's private data directory. Read existing project documentation and preserve it; do not write across discovered repositories during inventory. Owner-controlled changes may later export memory to source repositories.
- 2026-10-09: Bind loopback by default. Host systemd and Tailscale integration remains a measured deployment gate. Do not alter existing VPN listeners.
- 2026-10-09: Use the installed Codex App Server and its existing authentication. Do not introduce a separately billed API or copy credentials.

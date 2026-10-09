# Operating the manager

## Owner setup and private access

Choose the owner password yourself; the service creates no default account. Either run `uv run python -m ai_manager.cli setup-owner` on the server (private password prompt), or read `~/.local/share/linko-bot-manager/setup-token` privately and enter it once in the web setup form with your chosen username/password. The token disappears when setup succeeds. Never put it in a URL, repository or log.

The application listens only on loopback port 8787. Its separate private Tailscale HTTPS listener is deployed at `https://codex-server.tail5ebb9d.ts.net:8787`. Existing Chatwoot listeners 443 and 3000 are separate and must remain intact. Funnel/public exposure is not configured.

## Service

```bash
uv sync --locked
uv run python scripts/install_service.py --start
systemctl --user status linko-bot-manager.service
journalctl --user -u linko-bot-manager.service -n 80
systemctl --user restart linko-bot-manager.service
```

Installation detects and persists the absolute Codex executable path; systemd normally omits ~/.local/bin from PATH. The dedicated user service uses a private umask, no new privileges, a single manager process, restart-on-failure and cgroup cleanup. `AI_MANAGER_DATA`, `AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS` and cookie settings can be configured in `~/.config/linko-bot-manager.env` (0600). Do not set API keys/providers. Secure cookies stay on for private HTTPS. Use `AI_MANAGER_SECURE_COOKIE=0` only for local HTTP development.

```bash
# Preserve existing private listeners; add only the dedicated manager port:
sudo -n tailscale serve --bg --https=8787 http://127.0.0.1:8787
tailscale serve status
```

Do not run `tailscale serve reset` or modify SSH/RustDesk routes. A user service survives logout/reboot only if that user's systemd manager is started at boot (typically linger). Verify with `loginctl show-user "$USER" -p Linger`. Do not reboot a VM with unrelated active work merely to test this application.

## Daily flow

1. Select a project. Set its objective, instructions and priority.
2. Register trusted verification commands in Settings. Default test isolation exposes `/usr` runtimes and the standalone clone, with no home/network. Dependency-heavy projects need an explicitly designed isolated dependency environment; dependency installation is not currently a worker capability.
3. Talk naturally, explicitly delegate a goal, or add tasks. Brainstorming does not launch work.
4. Enable a scoped or limited scheduler batch. It starts at most two execution workers by default and respects dependencies.
5. Review exact edits/commits/deletions in Approval center. Change global/project policy when desired.
6. Review verification evidence and results in Agent monitor. Completed code lives in an isolated clone/branch; source checkouts and remote repositories remain untouched. Integration/merge/push/deployment require a future controlled capability or deliberate owner action.

## Interrupted work

Browser/SSH disconnects leave background jobs running. SSE replays persisted events. A confirmed interruption pauses a task; transport loss or service failure blocks it for reconciliation. Check process start identity, saved thread/turn, Git state and isolated workspace. Resume only through persisted-worker controls after the previous physical worker is no longer observed. Never clear a blocker by assuming that a missing connection means a process stopped.

Supervisor-owned process groups and the systemd cgroup identify the manager's workers; unrelated Codex/tmux sessions are never signaled. A crash cancels outstanding approval grants. Work never automatically replays an uncertain operation.

## Backup and restore

```bash
uv run python -m ai_manager.cli backup /private/backups/manager-2026-10-09.sqlite3
systemctl --user stop linko-bot-manager.service
uv run python -m ai_manager.cli restore /private/backups/manager-2026-10-09.sqlite3 /private/fresh-data/manager.sqlite3
```

Verify SQLite integrity and foreign keys, select the fresh data directory via the service environment, then start and inspect state before enabling scheduling. Memory Markdown exports rebuild at startup. Back up the workspace directory and Codex persisted thread store separately if executions must be resumed; a database backup alone cannot restore deleted source clones or Codex rollouts. Authentication tokens should remain in protected host storage, not project archives. Revoke sessions after restoring an old snapshot with `uv run python -m ai_manager.cli revoke-sessions` against the selected restored data directory.

Keep at least daily dated backups and copies off the VM under owner-controlled access. The database backup CLI never overwrites an existing destination and restore never overwrites a live database. The dedicated daily local backup timer is installed and enabled. It does not transmit data to external storage automatically.


## Known storage gate

On 2026-10-09 the actual VM disk was 99% full (about 445MB free). The dashboard reports low space. Workspace creation estimates committed file footprint and preserves a 128MB reserve; backups also preserve that reserve. No unrelated files/images/volumes are deleted automatically. Expand/free storage before enabling large-project execution. The local backup timer creates a verified dated backup daily and never removes old backups; the owner must manage capacity and off-VM copies.

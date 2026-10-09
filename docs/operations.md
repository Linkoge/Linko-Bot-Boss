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

On 2026-10-09 the actual VM disk was99% full (about445MB free). On 2026-10-10 the first confirmed check showed98% full,833515520bytes available (~795MiB), after owner-reported manual cleanup; deleted paths were not verified. No cleanup was performed during checkpoint preparation. The dashboard reports low space. Workspace creation estimates committed file footprint and preserves a 128MB reserve; backups also preserve that reserve. No unrelated files/images/volumes are deleted automatically. Expand/free storage before enabling large-project execution. The local backup timer creates a verified dated backup daily and never removes old backups; the owner must manage capacity and off-VM copies.

## Post-reboot verification checklist — checkpoint2026-10-10

These are instructions for the owner/fresh agent **after an owner-performed reboot**. No reboot/restart/stop was performed during checkpoint preparation. Start with read-only checks; do not rerun the installer, reset Serve, replay tasks, or restore over the live database merely because a check fails. Record time and outcome in STATE.md; inspect the current unit/DB path if the environment changed.

Use the existing codex account and repository:

```bash
cd /home/codex/projects/Linko-Bot-Manager
```

1. **VM startup and remote access.** Confirm SSH/Tailscale and any existing RustDesk access still work. Record a new boot ID (pre-reboot e3a37e08-1b0b-4ac0-a923-8b6b97767873); inspect failed units without changing them.

```bash
date -Is
uptime -s
cat /proc/sys/kernel/random/boot_id
systemctl --failed --no-pager
systemctl --user --failed --no-pager
loginctl show-user codex -p Linger
systemctl is-active tailscaled
```

2. **Available disk space.** Confirm the root/home filesystem remains writable with free space. Initial checkpoint measurement was833515520 bytes (~795MiB),98%; new measurements supersede it. Keep scheduling of large work paused if capacity remains insufficient. Do not clean anything as part of verification.

```bash
df -h / /home/codex
df -B1 /home/codex/projects/Linko-Bot-Manager
```

3. **Manager service.** Expected active/running and enabled. The user manager must start at boot (linger=yes). Check the dedicated units only. If absent/failed, record the error and review locally; do not blindly reinstall/restart.

```bash
systemctl --user is-enabled linko-bot-manager.service linko-bot-manager-backup.timer
systemctl --user show linko-bot-manager.service -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p FragmentPath
systemctl --user status linko-bot-manager.service --no-pager
journalctl --user -u linko-bot-manager.service -b -n 50 --no-pager
```

4. **Database integrity and persistence.** Open the actual database read-only (no constructor/migrations). Expected schema3, integrity ok, zero foreign-key errors, the ten original project IDs retained,50 original memory records and the two blocked tasks preserved; newly discovered projects may increase counts. Verify private permissions and disk exports. The production baseline has no owner/chat/thread/execution/approval rows; do not interpret that as lost history.

```bash
stat -c '%a %n' /home/codex/.local/share/linko-bot-manager /home/codex/.local/share/linko-bot-manager/manager.sqlite3
.venv/bin/python - <<'PY'
import json, sqlite3
from pathlib import Path
from contextlib import closing
root=Path('/home/codex/.local/share/linko-bot-manager')
with closing(sqlite3.connect((root/'manager.sqlite3').as_uri()+'?mode=ro', uri=True)) as c:
    c.execute('BEGIN')
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not c.execute('PRAGMA foreign_key_check').fetchall()
    print('schema', c.execute('SELECT max(version) FROM schema_version').fetchone()[0])
    print('journal', c.execute('PRAGMA journal_mode').fetchone()[0])
    print(json.dumps({t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('projects','memory','tasks','conversations','messages','executions','approvals','owner')}))
    rows=c.execute('SELECT project_id,name,content FROM memory').fetchall()
    assert all((root/'memory'/pid/name).is_file() and (root/'memory'/pid/name).read_text()==text for pid,name,text in rows)
    print('memory exports matched', len(rows))
    backup=root/'backups/manager-pre-reboot-20261010.sqlite3'
    if backup.exists():
        with closing(sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True)) as before:
            original={r[0] for r in before.execute('SELECT id FROM projects')}
            current={r[0] for r in c.execute('SELECT id FROM projects')}
            assert original <= current
            print('original project identities retained', len(original))
PY
```

5. **Private web interface.** Check loopback and HTTPS. Browser access requires connection to Tailscale. Preserve the existing Chatwoot443/3000 listeners. No public Funnel exposure is expected.

```bash
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8787/api/auth-status
curl --fail --silent --show-error --max-time 10 https://codex-server.tail5ebb9d.ts.net:8787/api/auth-status
tailscale serve status
```

Open https://codex-server.tail5ebb9d.ts.net:8787/ on desktop/Android. Expected secure setup screen until owner creation. If loopback works but HTTPS fails, inspect Tailscale/proxy state before changing it.

6. **Login functionality.** No real owner account existed at checkpoint. The owner must choose credentials privately; this is an uncompleted configuration gate, not an agent-selected password. If setup_required=true, the owner may run the existing CLI below (mutates only authentication state). Otherwise use the existing account; do not rerun setup. Verify sign-in, project page, logout, and refusal of protected APIs after logout. Do not put passwords/tokens in shell arguments, HTTP examples, Git or agent logs.

```bash
uv run --offline python -m ai_manager.cli setup-owner
```

Alternatively use the one-use local setup-token privately in the UI. Agent must not print/read that secret. Session expiration/revocation and12h cookie behavior remain as implemented; no password reset capability is claimed.

7. **Project discovery.** Compare persisted overview with the ten baseline names in docs/inventory.md. Startup discovery may register additional current repositories but must retain original identities and source files. Registry removal never deletes source files. Select multiple projects in the UI and verify memory/current tasks; empty conversation history is the known baseline.

```bash
uv run --offline python -m ai_manager.cli overview
```

`discover` is an available administrative command but writes registry state; use only if a deliberate refresh is needed after inspecting startup. Do not infer that every folder is a Git project.

8. **Existing task/worker recovery.** Expected two blocked gates, no active worker, no pending approval and scheduler disabled. A reboot does not by itself satisfy storage/recovery task criteria. If new execution occurred after this checkpoint, inspect saved PID/start identity/thread/turn/branch/workspace and reconcile actual effects. Unknown outcomes must block instead of automatically replaying. Approval grants from before service startup must not remain valid.

```bash
.venv/bin/python - <<'PY'
import json, sqlite3
from pathlib import Path
from contextlib import closing
path=Path('/home/codex/.local/share/linko-bot-manager/manager.sqlite3')
with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as c:
    c.row_factory=sqlite3.Row
    for label,sql in (
        ('tasks','SELECT id,status,lease_owner,lease_until,blocker FROM tasks'),
        ('unfinished executions','SELECT id,task_id,status,pid,process_start,thread_id,turn_id,worktree FROM executions WHERE finished_at IS NULL'),
        ('unresolved operations',"SELECT operation_key,action,state FROM operations WHERE state!='completed'"),
        ('live grants',"SELECT id,status,expires_at FROM approvals WHERE status IN ('pending','approved')")):
        print(label, json.dumps([dict(r) for r in c.execute(sql)]))
    row=c.execute("SELECT value FROM settings WHERE key='scheduler_enabled'").fetchone()
    print('scheduler_enabled', json.loads(row[0]) if row else False)
PY
```

Do not clear blockers or signal arbitrary stored PIDs. After recording actual acceptance evidence, deliberately reconcile/complete the reboot gate; handle storage separately. No code merge/push/deploy capability becomes available by rebooting.

9. **Codex worker connectivity.** First initialize a new owned App Server using the persisted absolute binary path and read the account type/catalog without running a model turn. The command closes only its own process. It should report chatgpt and a non-empty model catalog; never print raw account/auth/config contents. This verifies transport/auth/catalog, not resumed task inference.

```bash
AI_MANAGER_CODEX=/home/codex/.local/bin/codex .venv/bin/python - <<'PY'
import asyncio
from ai_manager.codex import AppServer
from ai_manager.config import Settings
async def check():
    server=AppServer(Settings())
    try:
        await server.start()
        assert server.account_type=='chatgpt' and server.catalog
        print({'account_type':server.account_type,'available_model_count':len(server.catalog)})
    finally:
        await server.close()
asyncio.run(check())
PY
```

If unavailable, leave tasks blocked and diagnose existing Codex authentication locally; do not add an API key/provider. A deliberate subscription inference/resume acceptance can later use `uv run --offline python scripts/smoke_codex.py` and disposable worker scripts once capacity is adequate. No real thread is present in the production baseline to resume.

10. **Backup service and integrity.** The timer should be active/enabled. The oneshot backup service may be inactive between successful runs; this is normal. Persistent=true may trigger a missed job after boot. Inspect last result, schedule and backups without starting jobs blindly. All current snapshots must reopen cleanly. Local backups do not protect against failure of the entire VM disk.

```bash
systemctl --user show linko-bot-manager-backup.timer -p ActiveState -p UnitFileState -p LastTriggerUSec -p NextElapseUSecRealtime
systemctl --user show linko-bot-manager-backup.service -p Result -p ExecMainStatus
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
from contextlib import closing
for p in sorted(Path('/home/codex/.local/share/linko-bot-manager/backups').glob('*.sqlite3')):
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro', uri=True)) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        print(p.name, 'integrity ok')
PY
```

After all checks, update STATE.md/IMPLEMENTATION_PLAN.md with verified reboot evidence, retain unresolved gates, update private project memory and create a local Git checkpoint. No automatic feature implementation, cleanup, push or deployment is implied by this checklist.

### Recover from the checkpoint backup, only if needed

Do not perform this during routine verification. Test restoration is safe into an unused offline destination; switching a production DB requires explicit operational authorization to stop/switch/start the dedicated manager. Preserve the damaged/current DB/WAL and isolated workspaces for diagnosis; do not overwrite them.

```bash
cd /home/codex/projects/Linko-Bot-Manager
uv run --offline python -m ai_manager.cli restore /home/codex/.local/share/linko-bot-manager/backups/manager-pre-reboot-20261010.sqlite3 /home/codex/.local/share/linko-bot-manager-recovery-20261010/manager.sqlite3
```

The destination must not exist. Verify it read-only using the integrity commands above adjusted to that path. For an authorized production switch, set AI_MANAGER_DATA to the fresh directory in the0600 service env file, use the same data selection for `revoke-sessions`, then start the dedicated service and inspect recovery before enabling scheduling. Restore/preserve original `workspaces/` and protected Codex rollouts separately; database-only recovery cannot resume missing workspace/thread files. Never commit the recovered DB or authentication state.

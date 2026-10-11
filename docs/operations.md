# Operations

The existing dedicated `linko-bot-manager.service` serves the repository using its Python environment, loopback port 8787 and existing private Tailscale HTTPS listener. Keep unrelated services and network listeners unchanged.

## Daily use

Choose a project and describe the objective naturally. Conversation is read-only; Auto delegates requested work; Execute sends an objective through the same manager path. Discussion does not require a coding assignment. Requested coding work is delegated as one complete objective; results return automatically in chat. Different projects execute concurrently, one worker per project. No scheduler scope, batch budget, permission profile or verification commands need configuration.

Tasks shows durable objectives and waiting reasons. Continue resumes the same objective with a new attempt and retained native thread; Stop interrupts live execution. Pause preserves deliberate waiting. Cancel is terminal. Project pause prevents future dispatch; stop an active worker separately when needed. Agents retains native output, errors and thread/turn references. Completed means worker-reported completion, not independent certification.

Question prompts bind the exact native request to the execution. Explicit answers are required. Stale questions expire when the worker ends; continuation asks afresh. Archived origin results are delivered in that exact archived conversation. Clearing a conversation preserves its assignments but discards later delivery to the deleted origin.

Historical unfinished tasks remain waiting after the simplification migration. Continue explicitly only when that objective is still wanted; Codex receives historical workspace/output references and inspects actual source before proceeding. Old completed/cancelled/failed dispositions and supervision evidence remain historical. There is no automatic clone merge or old-operation replay.

## Restart and recovery

Temporary network/process failures and account usage limits now recover automatically through the existing scheduler. Tasks keep their original assignment/thread and project claim while waiting; manager responses and pending results remain in the exact original conversation. Persisted backoff and an account-wide single-probe gate prevent repeated model calls while unavailable. Stop and deliberate pauses remain deliberate; permanent errors are reported without an endless retry loop.

Startup reconciles native outcomes before resuming coding. Surviving proven owned App Server groups are stopped before another writer starts; uncertain unprovable ownership remains exclusive. See [automatic recovery](AUTOMATIC_RECOVERY.md) for error handling, schema 9, backoff, tests, limits and deployment evidence.

## Backups and restoration

The existing daily timer invokes `scripts/backup_daily.py`. It creates a fresh private SQLite backup and checks integrity/foreign keys; it never prunes unrelated data. Keep native Codex thread storage and project files/Git backups separately. Do not print authentication stores or copy them into Git.

```bash
uv run python -m ai_manager.cli backup /new/private/path/manager.sqlite3
uv run python -m ai_manager.cli restore /private/backup.sqlite3 /new/private/path/restored.sqlite3
```

Restore only to a fresh destination. Stop manager execution before switching databases. Revoke sessions after a restore when appropriate. Rollback requires the old code, database and project/native-thread context; restoring SQLite alone does not undo worker actions.

## Configuration and validation

Keep global `AI_MANAGER_DATA`, `AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE` and absolute `AI_MANAGER_CODEX`. Default worker capacity is two; persisted `max_workers` remains the global capacity setting. Only two model routes remain: interactive manager and execution worker.

Run `pytest`, Ruff and JavaScript syntax checks. `scripts/browser_smoke.py` uses a temporary authenticated HTTP fixture. `scripts/smoke_minimal.py` uses disposable data/projects and real subscription inference. Read the implementation record for actual release validation and deployment evidence; older stage reports describe earlier architecture.

## Deployment

Prepare and test a separate checkout. Create an online SQLite backup and rehearse startup against its copy. When no active execution needs interruption, stop only `linko-bot-manager.service`, fast-forward this repository to the tested commit, and start the same user service. Its existing `.venv`, environment, daily backup timer and Tailscale listener are retained. Check service PID/start time, loopback/private HTTPS, served asset hashes, SQLite integrity and preserved records. Do not run acceptance assignments against production data.

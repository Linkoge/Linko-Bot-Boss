# Operations

The existing dedicated `linko-bot-manager.service` serves the repository using its Python environment, loopback port 8787 and existing private Tailscale HTTPS listener. Keep unrelated services and network listeners unchanged.

## Daily use

Choose a project and describe the objective naturally. Discussion does not require a coding assignment. Requested coding work is delegated as one complete objective; results return automatically in chat. Different projects execute concurrently, one worker per project. No scheduler scope, batch budget, permission profile or verification commands need configuration.

Tasks shows durable objectives and waiting reasons. Continue resumes the same objective with a new attempt and retained native thread; Stop interrupts live execution. Pause preserves deliberate waiting. Cancel is terminal. Project pause prevents future dispatch; stop an active worker separately when needed. Agents retains native output, errors and thread/turn references. Completed means worker-reported completion, not independent certification.

Question prompts bind the exact native request to the execution. Explicit answers are required. Stale questions expire when the worker ends; continuation asks afresh. Archived origin results are retained in the archive and delivered to the fresh conversation in the same context. Clearing a conversation does not delete assignments; later results use its project's current conversation.

Historical unfinished tasks remain waiting after the simplification migration. Continue explicitly only when that objective is still wanted; Codex receives historical workspace/output references and inspects actual source before proceeding. Old completed/cancelled/failed dispositions and supervision evidence remain historical. There is no automatic clone merge or old-operation replay.

## Restart and recovery

The dedicated service uses its existing restart policy. On startup, unfinished native attempts are inspected for terminal results. Recorded completion is delivered without rerunning work. An interrupted authorized worker resumes through the ordinary queue. An observed detached process keeps the project claimed until accounted for. Unknown launch identity remains waiting; do not launch a second writer to clear a status badge.

Manager result delivery is persisted transactionally with worker output. Pending deliveries resume when the originating conversation is free. Known completed manager turns are recovered from native history. Uncertain manager delivery is retained with its execution references; a database association is not a guarantee of exactly-once inference across every crash window.

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

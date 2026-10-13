# Operations

The existing dedicated `linko-bot-manager.service` serves the repository using its Python environment, loopback port 8787 and existing private Tailscale HTTPS listener. Keep unrelated services and network listeners unchanged.

## Daily use

Choose a project and describe the objective naturally. Conversation allows deferred To-Do recording and otherwise is read-only; Auto delegates requested work; Execute sends an objective through the same manager path. Discussion does not require a coding assignment. Requested coding work is delegated as one complete objective; results return automatically in chat. Different projects execute concurrently, one worker per project. No scheduler scope, batch budget, permission profile or verification commands need configuration.

Tasks and Agents expose Pause/Cancel while coding, with requested versus confirmed cleanup state. Pause retains progress and Resume queues the same assignment/thread; Cancel immediately interrupts owned execution and retains partial work for review. Manager Auto/Execute can pause and append instructions into that same assignment. Project pause prevents future dispatch. See [agent controls](AGENT_CONTROLS.md) for partial-work limits, latency, recovery and release evidence. Completed remains a worker-reported outcome.

Question prompts bind the exact native request to the execution. Explicit answers are required. Stale questions expire when the worker ends; continuation asks afresh. Archived origin results are delivered in that exact archived conversation. Clearing a conversation preserves its assignments but discards later delivery to the deleted origin.

Historical unfinished tasks remain waiting after the simplification migration. Continue explicitly only when that objective is still wanted; Codex receives historical workspace/output references and inspects actual source before proceeding. Old completed/cancelled/failed dispositions and supervision evidence remain historical. There is no automatic clone merge or old-operation replay.

## Future ideas

To-Do immediately follows Agents. It is the authoritative project backlog, distinct from executable Tasks.
All Projects lists every project's ideas; adding there requires a project. Add/Edit records future work without
scheduling it. Select entries and Delete selected to archive only those ideas, retaining their data and audit.
Changed revisions reject stale edits/deletion atomically. The manager uses list_todos/save_todo in every mode
for future requests; explicit later scheduling is a separate assignment. The linked memory-compaction proposal
remains deferred. Startup chooses the highest existing meaningful-use context, including global, with the same
name/ID tie ordering as navigation; viewing, reloading, polling and To-Do edits do not manufacture recency.

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

Keep global `AI_MANAGER_DATA`, `AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE` and absolute `AI_MANAGER_CODEX`. Default worker capacity is two; persisted `max_workers` remains the global capacity setting. Two roles remain: manual Manager and manual/automatic Bot. Global role selections, independent per-model/per-level policy and safe pinned assignment continuation follow [model settings](MODEL_SETTINGS.md).

Run `pytest`, Ruff and JavaScript syntax checks. `scripts/browser_smoke.py` uses a temporary authenticated HTTP fixture. `scripts/smoke_minimal.py` uses disposable data/projects and real subscription inference. Read the implementation record for actual release validation and deployment evidence; older stage reports describe earlier architecture.

## Deployment

The controlled independent helper is documented in [deployment helper](DEPLOYMENT_HELPER.md).
It has durable succeeded releases; current navigation/audio success is referenced in STATE.md.
Owner has authorized `integrated-recovery-append-popup-v1-20261013`; the manager reviews and calls `deploy_release`.
Use its exact preparation manifest and succeeded deployment receipt. The integration worker does not restart or deploy. The older manual procedure below is historical
context for code-only releases and requires an external quiet operator window.

Prepare and test a separate checkout. Create an online SQLite backup and rehearse startup against its copy. When no active execution needs interruption, stop only `linko-bot-manager.service`, fast-forward this repository to the tested commit, and start the same user service. Its existing `.venv`, environment, daily backup timer and Tailscale listener are retained. Check service PID/start time, loopback/private HTTPS, served asset hashes, SQLite integrity and preserved records. Do not run acceptance assignments against production data.


## Attachment storage and recording recovery (prepared composer V3)

`chat-ui-composer-v3-20261012` adds immutable owner-uploaded files under
`DATA/chat-workspace/attachments`, private 0700/0600 directory/files. Upload metadata and execution
references use existing SQLite settings. Four files/message, 8 MiB/file, 512 MiB storage cap;
PNG/JPEG/WebP photos up to 20 megapixels; arbitrary file interpretation depends on native tools.
User clear/archive/remove controls retain bytes. Upload errors never claim success. Review retained
storage with the owner before deleting anything, including orphaned/unsent uploads; no automated GC.

`scripts/backup_daily.py` creates the verified DB snapshot and a sibling `.attachments` directory
with copies matching snapshot metadata and SHA256 manifest. Back up both together. Restore uploaded
files to their original generated names under the original DATA path, with private permissions,
verify checksums and SQLite integrity, and preserve newer accepted uploads/native work before any
rollback. Existing older database-only/helper backups do not capture these bytes; for controlled
activation/rollback, explicitly preserve attachment storage alongside the helper/native backups.
Deployment does not replace/remove this DATA directory. The daily backup change takes effect only
when this candidate is activated; this preparation makes no production backup or data mutation.

Unfinished recording checkpoints are browser-local IndexedDB; draft/recovery keys live in per-tab
sessionStorage. Same-tab reload recovery is best effort, not a server audio archive. Stop or Download
before leaving long mobile recordings; Android/browser suspension and storage eviction cannot be
prevented by this app. No background service is installed.


## Unfinished job follow-up candidate

See [automatic recovery](AUTOMATIC_RECOVERY.md) for assignment-level review/actionable/owner-wait/deferred/
complete state, 15-second metadata-free SQL checks, 30-second idle grace, deduplicated internal wakeups,
persisted retry delays and explicit hold exclusions. These settings and exact executions are included in
existing SQLite backups; no separate recovery store/service is introduced. New work resolves catalog
family versions; retained exact routes never silently upgrade. These candidate behaviors require deployment.

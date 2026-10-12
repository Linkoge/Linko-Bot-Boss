# Controlled deployment helper — 2026-10-12

Implementation is prepared in `/home/codex/checkouts/linko-deploy-helper-20261012`.
**First manager activation is pending.** The independent helper can be installed while a worker is active;
installation starts only `linko-deployment-helper.service` and never restarts the manager.
The observed combined startup-selection/zero-Tasks-badge/To-Do/Markdown release is already live at
`7999d1a5b4dbaeeeaea506553b90d58090cb6fce`, schema11. Existing pending-release docs are historical;
its private `deployments/markdown-20261012/deployment-receipt.json` records actual activation.
This helper candidate descends from that complete release and preserves its assets, data and deferred To-Do.

## Protocol and scope

The trusted preparation CLI takes a clean reviewed checkout and records its exact commit, actual live base,
asset hashes, Git bundle hash and validation log hash. It runs backend tests, Ruff and JavaScript syntax checks.
It refuses dependency or `db.py` changes. This is intentionally a code/assets capability with no schema migration,
dependency installation, service selection, arbitrary shell, dynamic argv, remote push, pruning or network changes.
Preparation is local worker administration; the conversational manager accepts only a prepared release ID.
The helper executable/config and bundles reside privately outside the live source tree. Future helper updates
require separate versioned review; application releases never replace the independent helper automatically.
Same-user workers are trusted; this is a narrow manager capability, not a Unix-user security boundary.

`manager_action deploy_release` in Auto/Execute records an idempotent request, nonce and originating conversation/
execution. Wrong-project requests, unknown IDs and overlapping requests are rejected. The request immediately
closes browser mutation admission and new chat submission. Existing assignments, recovery and automatic result
reporting continue. Pending questions can be answered, execution interrupted and tasks stopped/paused/cancelled.
Every queued task, including a paused project's queue, blocks acknowledgement; nothing is silently cancelled or
resumed. Settings can display status and cancel only the exact still-unacknowledged nonce, allowing an indefinite
idle/recovery wait to be abandoned. Read/auth/CSRF boundaries remain enforced.

Scheduler acknowledgement requires no in-flight mutation, manager/worker job, unfinished execution, project lease,
queued task, pending question, pending/running/waiting delivery or streaming/queued message. The durable protocol
file is fsynced before the helper acts. Acknowledgement fences dispatch/recovery and every mutation across restart.
The independent user service checks its own cgroup, obtains an exclusive helper lock, rechecks source/receipts/idle,
takes verified online SQLite/native-history/config/source backups, stops only the manager, rechecks idle and source,
and takes a final stopped SQLite snapshot. It fast-forwards only to the prepared commit and starts the same service.
It verifies loopback and existing private HTTPS release/scheduler/auth boundaries and all assets, SQLite integrity/
foreign keys, every original owner/session/request/To-Do/conversation/message/execution/task row and a temporary
scoped authenticated probe. It removes the probe session in a finally block. It creates no production assignment.
Native rollout copies are checked against the original prefix; unrelated active native sessions are not stopped.

Success releases admission. Failed activation stops the fenced candidate and checks out the exact old commit
(detached, preserving the forward main branch), then verifies old code/assets against the preserved live database.
It never restores an old SQLite snapshot over new data. A successful rollback releases admission and reports the
failure. An interrupted helper or failed rollback stays fenced in `rollback_failed`; it never guesses success,
replays an uncertain activation or kills potentially active workers. External inspection/forward repair is then
required. After successful detached rollback, prepare the next release against actual HEAD; review branch state.

Each request/result is durable under `deployment-helper/results/<nonce>.json`; backup paths and service identity
are recorded there. Restarted manager reports exactly once in the originating conversation and retains a
`deployment.result` execution event/reference. Deleted conversations are not recreated. Manager context and
`deployment_status` expose retained results; Settings provides a status check. File-lock contention is retried.
There is no timeout that forces an idle cutover and no destructive backup retention.

## One-time external bootstrap

The currently live code predates the cooperative gate. An independent process alone cannot prevent its new work
admission, and a worker inside its control-group cannot safely restart it. No service escape or live monkey-patch
is used. The first activation therefore requires a brief operator terminal **outside** the manager cgroup after
this worker and its manager result-reporting turn finish, with no new submissions during cutover.

Prepared release ID: `deployment-helper-v1-20261012`. The independent helper unit must be active before bootstrap.
The commands below are exact for this installation; no Codex session is necessary, only an external terminal.

1. Arrange the quiet submission window. Inspect `/proc/$$/cgroup` and wait for all live workers/manager turns,
   queued work, claims, questions and automatic deliveries to settle. Do not stop any active worker.
2. In the external terminal, check persistent idleness without initializing/migrating the production database:

   ```bash
   cd /home/codex/checkouts/linko-deploy-helper-20261012
   PYTHONPATH=. /home/codex/projects/Linko-Bot-Manager/.venv/bin/python -c 'import sqlite3; from ai_manager.deployment import idle; c=sqlite3.connect("file:/home/codex/.local/share/linko-bot-manager/manager.sqlite3?mode=ro",uri=True); counts=idle(c); print(counts); assert not any(counts.values())'
   ```

3. Only in that quiet, verified idle window, stop the existing manager, then run the bootstrap gate. This command
   itself refuses the worker cgroup, a still-active service, dirty/moved live source, altered receipts or nonidle DB:

   ```bash
   systemctl --user stop linko-bot-manager.service
   PYTHONPATH=. /home/codex/projects/Linko-Bot-Manager/.venv/bin/python scripts/bootstrap_deployment.py /home/codex/.local/share/linko-bot-manager/deployment-helper/config.json deployment-helper-v1-20261012
   ```

   If this refuses before acknowledgement, start the original manager with
   `systemctl --user start linko-bot-manager.service`, preserve the request/evidence and resolve the stated blocker.
   Never change task state to manufacture idleness. If acknowledged, the independent helper takes over and starts
   the tested candidate. Do not manually restart or modify it during activation.
4. Read the durable `deployment-helper/active.json` result and
   `systemctl --user status linko-deployment-helper.service linko-bot-manager.service --no-pager`.
   `succeeded` is the acceptance gate. `rolled_back` means original code recovered and this release remains pending.
   `rollback_failed` requires inspection while admission stays fenced. Full receipts remain private/readable by manager.

After this bootstrap, owner-requested deployment from conversation uses a reviewed prepared release ID and needs
no external stop/restart. Subsequent preparation remains a worker's local fixed-check CLI step. Memory compaction,
archiving, reboot acceptance and unrelated services/listeners/credentials are outside this delivery.

Full technical evidence: `/home/codex/.local/share/linko-bot-manager/deployments/deployment-helper-20261012/REPORT.md`.

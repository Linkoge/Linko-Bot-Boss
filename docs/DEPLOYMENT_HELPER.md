# Reviewed schema12/helper v2 bundle — 2026-10-14

Candidate `parallel-composer-integrated-v3-20261014` includes the rename/stash/owner-intent fixes and
all succeeded To-Do/chat/PWA functionality. Preparation uses actual succeeded live base 6c42d5b,
receipt `deployment-helper/results/2bf6554aecb9fd66c9ab49554bfb95ac.json`. Older PWA-only and waiting-idle
observations below are historical. The owner explicitly says do not deploy or push this new bundle.

The installed independent helper remains v1, unchanged. It refuses any db.py diff. Reviewed v2 permits
ONLY the pinned schema11→12 db.py byte hashes in `ai_manager/deployment.py`: three nullable assignment
workspace columns and the shared/isolated unique index contract. Unknown schema/dependency changes
still reject. The manifest records exact schema contract and packaged `helper-v2.py` SHA256; snapshot
verification compares all original columns/rows, requires new workspace columns NULL on cutover,
checks exact indices and allows only marker12. Immediate fenced rollback preserves additive columns,
original rows and old code; no old database snapshot overwrites later data. A later rollback after
isolated tasks exist requires separate ownership-aware review, not this immediate cutover fallback.

Release preparation is serialized by its private file lock and receipt-bound SQLite reservation;
manager integration cannot acquire its global reservation during preparation. Fixed tests/browser
checks validate the exact combined commit before immutable bundle/manifest creation. Installed v1
cannot activate this release until reviewed helper v2 is separately installed. The packaged helper
is installed only by an explicit future operation using `scripts/install_deployment_helper_v2.py`
with exact release ID and existing --data. It verifies artifact hash/config/unit, rejects pending
handoffs/lock contention, preserves the old helper/unit receipt and restarts only the independent
helper service. This task does NOT invoke that installer, a deploy request, service restart or push.
Then normal owner-authorized exact release deployment retains all idle/drain/backup/auth/data/asset
checks. Only durable matching succeeded evidence establishes activation. Current facts/report:
[STATE.md](../STATE.md). Old blanket no-schema capability wording below describes installed v1.

# Scoped owner deployment handoff — candidate, 2026-10-14

Exact integrated candidate: `owner-deploy-intent-stash-v1-20261014`, isolated checkout
`/home/codex/checkouts/linko-deployment-intent-20261014`, descended from completed
stash commit `751ac9374f0c41ee40ffcf6737d11a7170092888`. It preserves rename, To-Do/chat,
recording/Agents and all prior PWA changes. Deployment remains pending; readiness requires
its immutable manifest. Full report and exact verification receipts:
`/home/codex/.local/share/linko-bot-manager/deployments/deployment-intent-20261014/REPORT.md`.
Historical live/pending claims below are evidence for their original date; current live is
PWA commit `3bb554b7efa26d8fae695c5340e8a12366d75339`, succeeded nonce
`09f882af21fb28ab5870da74fee1c3b6`. The separate older To-Do/chat request nonce
`2bf6554aecb9fd66c9ab49554bfb95ac` is waiting_idle, not success for this candidate.

## Owner source and scope contract

The manager must record intent in the actual Auto/Execute owner turn before asynchronous
assignment work. `record_deployment_intent(project_id, owner_deployment_quote, release_id)`
freezes one existing prepared release and its manifest/commit/base/bundle/validation evidence.
For existing preparation assignments, use fixed `task_ids` instead of release_id. For a NEW
prepare-and-deploy assignment, `delegate(owner_deployment_quote, ...)` records its owner
source and deterministic assignment request before transport startup/worker scheduling.
An existing exact-release intent automatically links a same-owner delegation as its audit;
passing `intent_id` explicitly also documents this relationship. No intent is inferred from
worker output, old docs, internal notifications or owner requests that merely implement features.

Records use existing transactional SQLite settings/events, with owner ID, exact message,
message digest, source execution/conversation/project, fixed scope, status and review evidence.
The manager interprets the natural-language owner's deployment instruction within the existing
trusted manager-tool boundary; source provenance and scope are enforced by code. There is no
regex permission guessing, historical approval migration, new AI reviewer or separate service.
Workers remain trusted same-user preparation agents, not isolated security principals.

For assignment intent, trusted preparation includes repeatable `--task-id <exact-ID>` flags.
The manifest binds only those assignment IDs and hashes of their project/objective/acceptance/
request/context evidence. Manager review requires matching captured assignment hashes and
completed, inactive, unheld preparation with a completed execution. Failure or later changed
assignment scope cannot authorize deployment. No CLI flag creates owner intent.

Retrieve full results/coverage evidence, then call
`review_deployment_intent(project_id,intent_id,release_id,reason)` to freeze/accept the exact
prepared candidate. Call `deploy_release(project_id,intent_id,release_id)` afterward. A worker
result/recovery turn can review/consume the existing matching owner intent; it cannot create
or preserve one, authorize a different release/project/conversation, or resume a project.
An exact-release intent never expands to a new integrated release. Choose fixed authorized
assignment scope before preparation when a new candidate is necessary.

Every newer accepted owner request in that project, original conversation or All Projects,
including queued/unprocessed requests, holds dispatch until reviewed. The latest actual owner
turn may `revise_deployment_intent(decision=preserve,reason=...)` only when its correction
retains the original scope. Cancel/wait/pause/changed scope means revoke; changed scope needs
a new explicit owner deployment request. Preserve does not reset a revoked/failed/consumed
intent. Editing/removing original source also blocks consumption. Task pause/cancel/wait,
owner_wait/deferred follow-up, project pause/archive/remove and owner Stop reply revoke their
unused associated intents. Unrelated tasks/projects are unaffected; visibility is unaffected.

Settings shows unused intents and an authenticated CSRF-protected cancellation button.
After dispatch, use the existing exact pending nonce cancellation; it remains available only
before acknowledgement. Cancellation reconciles a request-file/SQL crash gap before claiming
an unused intent revoked. An acknowledged cutover cannot be silently cancelled or resumed.

Consumption is serialized in SQLite and retains the existing fixed-operation helper protocol.
Nonce receipts/origin include intent/source/candidate. Restart recovery and duplicate calls
return the retained exact receipt, even after active.json moves to another request. Failed,
rolled-back, rollback_failed, cancelled and succeeded receipts never cause another activation.
Dispatch errors retain dispatch_failed and require a new explicit owner instruction/prepared
receipt rather than automatic retries. A protocol request written before SQL consumption is
recovered from durable origin; cancellation also closes that crash gap. Existing idle/admission,
backup, base/schema/dependency, concurrency, rollback, auth and held-work guards remain.

Native manager compatibility hashes instructions plus tool schema. Activation rotates future
manager threads to this contract through the existing path; running turns are not rewritten.
Intent summaries appear only when relevant, preserving the lean ordinary prompt.

## Safe activation while the new code is not live

This worker performs no activation, restart, push, pending-request cancellation or permission
backfill. The original owner message `a1ee54202d4841b7a00db31a1fb9e9df` authorized releases
pending at that time. It did not authorize later rename/stash/this fix. Renewed message
`bab671e918f44f35935ea52f0f0884a9` already produced the exact older To-Do/chat request above;
that request remains unchanged. Let its existing helper idle path settle after actual jobs,
queues/questions/deliveries end, or have the owner cancel its exact pending nonce in Settings
if they choose to replace the target. Never cancel held work or manufacture idleness.

The current live manager lacks these intent tools and rejects deployment from internal result
turns. To activate THIS integrated candidate before the fix is live, the owner must issue a
fresh Auto/Execute deployment instruction naming `owner-deploy-intent-stash-v1-20261014`
after any existing request has settled or been owner-cancelled. That OWNER turn reviews the
already prepared evidence and immediately uses the EXISTING legacy
`deploy_release(release_id="owner-deploy-intent-stash-v1-20261014")`, with the manager project
selected; do not delegate another audit and then attempt this legacy call from a result turn.
The controlled helper still waits for real idleness, backs up, verifies and rolls back as usual.
No shell/bootstrap/manual runtime bypass is needed. A pending request is not deployment success.
If actual live HEAD advances (including the older pending request succeeding), reprepare the
same reviewed source against that actual base under a fresh immutable release ID, then obtain
owner instruction for that exact ID; never overwrite a manifest or bypass base checks.

Only after a durable matching succeeded receipt activates the new candidate can new owner
turns use its durable intent contract. No older message can be retroactively turned into an
intent from an internal turn. Future helper installation changes remain separately reviewed;
the independent helper executable is not changed by this application release.

# Controlled deployment helper — 2026-10-12

Implementation is prepared in `/home/codex/checkouts/linko-deploy-helper-20261012`.
**The helper is active; first activation succeeded.** Current live is navigation/audio v2,
with exact succeeded receipt linked in [STATE.md](../STATE.md). The bootstrap procedure below is historical;
this integrated delivery uses manager-controlled `deploy_release` after its required preparation gate. The independent helper can be installed while a worker is active;
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

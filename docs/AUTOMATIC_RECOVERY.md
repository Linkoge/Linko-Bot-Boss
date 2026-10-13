# Unfinished job recovery candidate — 2026-10-13

Exact release: `unfinished-recovery-family-ui-v2-20261013`. Implemented and fixture-tested in
`/home/codex/checkouts/linko-unfinished-recovery-20261013`; deployment is pending a **new owner request**.
Preparation is established only by its helper `manifest.json`, and activation only by a later exact succeeded receipt.
Current live remains `nav-audio-upload-v2-20261013`, commit `08f0ba712f194ea0f985740ef408c9e033511b98`,
proven by [/home/codex/.local/share/linko-bot-manager/deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json](/home/codex/.local/share/linko-bot-manager/deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json) and matching served assets. Older pending/live statements below are historical.
Full evidence and manager delivery facts: [/home/codex/.local/share/linko-bot-manager/deployments/unfinished-recovery-20261013/REPORT.md](/home/codex/.local/share/linko-bot-manager/deployments/unfinished-recovery-20261013/REPORT.md).

## Assignment workflow, without a separate ticket system

Worker `completed` is an execution outcome, not full job completion. Existing assignments retain an
`assignment_workflow:<task_id>` value in the existing settings table: state, bounded reason, update time,
idle-since, next eligible time, retry count and wake execution ID. Assignment creation/explicit continuation
atomically opens actionable follow-up; uncontrolled worker finish changes open work to review. Nothing
closes it merely because the worker or manager response finishes. `read_task` exposes this state.
The existing manager tool adds `job_followup(task_id,followup_state,reason)`: `complete` after result review
and all authorized follow-up, `actionable` for concrete outstanding work, `owner_wait` before asking an
owner question or waiting for deployment authorization, and `deferred` for later work. Only inactive
completed/failed assignments can become actionable/complete; explicit continue retains its normal controls.
Manager instructions require recording a disposition before ending result review or asking an owner question.
This does not infer obligations from prose or invent historical work. Existing pre-candidate assignments
without workflow metadata stay unchanged; old pending results still use their durable delivery path.

Every 15 seconds, the existing scheduler performs a SQLite-only follow-up check. It never calls a model
or provider while checking. Eligible work must remain actionable/review with an active project, retained
origin, inactive completed/failed worker, no held project lease (including uncertain ownership), no pending
question, no unfinished origin manager/worker, and no existing pending/running result delivery. Queued/running
coding uses existing dispatch/recovery. Legacy/archived assignments and deleted origins are excluded.
A fresh observed idle interval must last 30 seconds, including after busy work/holds disappear.
After grace/backoff, submit rechecks everything under BEGIN IMMEDIATE and atomically queues one manager
execution and stores its wake identity. Competing checkers cannot queue another pending wake; the existing
service lock, conversation head ordering and runner cleanup guards serialize execution. On restart, native
outcome reconciliation resumes the saved execution rather than creating a replacement wake.

Follow-up retries have persisted delays of 60, 120, 240 seconds and so on, capped at 30 minutes; an
unfinished existing manager response instead uses the existing native recovery/backoff/account gate.
No arbitrary retry limit drops an outstanding job. A manager disposition resets eligibility; `complete`,
owner-wait/deferred and explicit controls stop retries. Success without a disposition leaves review open,
so a missed manager follow-up cannot disappear. This is a bounded safeguard, not deterministic understanding
of completion or an exactly-once guarantee for external side effects.

Pause, cancel, project pause, owner-wait, To-Do and explicit manager Stop never authorize resumed work.
Manager Stop atomically holds open assignments in its origin and records `chat.stop_requested`; stopped
result delivery remains held until an explicit owner follow-up/continuation. Task pause/cancel/continue and
manager wait update workflow in their existing control transaction. Pending native owner questions also block
worker restart inference until answered. Paused/waiting/cancelled completion reports use Conversation mode;
internal result/recovery tools cannot resume a held target or project or request deployment. Ordinary owner
Auto/Execute retains explicit continuation/deployment behavior. Worker results are not permission grants.

Internal wake notices have no user/worker chat message row, no owner Send/recency event, no bulk worker
output/objective/history transport, and a compact task/state/evidence reference in the normal manager prompt.
The manager's natural answer remains an ordinary assistant response. Existing result rows remain hidden by
provenance. Existing focused context and on-demand evidence retrieval remain unchanged. No dependency,
schema, queue service, ticket architecture, supervisor or extra routing AI role is added.

## Interruption investigation

Reply `c15b0690b4364daba3be90a10b67d28a` began 2026-10-13 12:02:31 +04 and ended interrupted at
12:03:07 +04. SQLite records `stop_requested=1`; journal shows successful
`POST /api/executions/c15b0690b4364daba3be90a10b67d28a/interrupt` at 12:03:07.
This establishes the immediate cause: the authenticated interrupt endpoint set the durable stop flag
and interrupted that reply. It was the model-version response, not the deployed navigation release.
The journal separately records transcription 502s at 12:02:52, 12:03:10 and 12:03:13; it does not establish
that those requests caused the stop. No chat Send appears between initial request and stop in this window.
Only Stop reply handlers reference the manager interrupt endpoint; Record/picker/STT/Discard handlers do not.
No gesture trace or pointer coordinates were retained, so an accidental Stop reply activation versus another
browser request cannot be distinguished. Do not claim recording/upload interrupted the manager or claim
an audio interruption defect was fixed. Add a durable endpoint-specific stop audit for future diagnosis.
Chromium checks real capture/record toggle/picker cancellation while a fixture manager is active, zero
interrupt requests throughout audio flows and unchanged explicit Send behavior. Physical-device/native
picker accidents and the historical STT-provider 502 root cause are not established by these tests.

## Validation

Regression tests cover grace/no checking inference, competing checks/transactional dedup, restart after
queue commit, interrupted result delivery, exact original response identity, failed-review retry/backoff,
completed-worker-pending-review, owner holds/To-Do states, project pause, valid/uncertain leases, unanswered
questions after restart, pause after queue but before launch, internal deployment/paused-worker rejection,
and Conversation read-only behavior. Existing tests retain native process-death/lost-start reconciliation,
project exclusivity, policy recheck and control races. Release validation retains exact test/browser output.
Audio and latest-family details: [AUDIO.md](AUDIO.md), [MODEL_SETTINGS.md](MODEL_SETTINGS.md).

---

# Automatic recovery

The existing path remains **User → Manager → Codex → Manager → User**. Recovery runs in the existing scheduler with SQLite eligibility checks; no recovery model, supervisor, queue service, project configuration or routine approval is added.

## What changed

Previously, worker transport errors became failed assignments; quota details were reduced to text. Startup inspected known native outcomes but could leave uncertain work waiting indefinitely. Manager completion delivery had a stable worker-message ID and transactional claim, but temporary delivery failures created separate replies after a fixed 30-second delay. Interrupted owner-request turns were not resumed automatically.

Those working protections remain. Temporary interruptions now retain the same task, execution, conversation, repository and native thread. The scheduler resumes eligible work automatically, including interrupted owner requests and sequential continuations. Healthy execution adds no inference calls or recovery instructions.

| Failure | Behavior |
| --- | --- |
| DNS, connection reset/timeout, transport loss, HTTP 408/5xx, upstream overload | Preserve partial output and edits; wait with backoff; reconcile native history; resume unfinished work. |
| Native usage/quota/rate limit, HTTP 429 | Preserve the assignment and thread; persist an account-wide inference gate and retry eligibility; allow one availability probe when eligible. |
| Backend crash, scheduler/service restart or reboot | Recover durable claims and unfinished manager responses. Collect a completed native result instead of rerunning it. Resume interrupted work once its original process is accounted for. |
| Dedicated App Server termination | Transport EOF wakes attached jobs; recovery inspects the saved turn and resumes the original thread. |
| Manager unavailable after worker completion | Keep the worker result and original response claimed; resume that same response in the original conversation. |
| Invalid objective/model/parameters, unrecoverable authentication, policy/context errors, worker-concluded failure | Finish meaningfully without automatic indefinite retry. Worker failures are sent to the manager for interpretation. If the manager itself cannot execute, show its error and retain the actual worker result with blocked delivery. |
| Explicit Stop, task/project pause or cancellation | Preserve user intent. A user stop finishes the attempt; project pause prevents resumed coding dispatch. Historical unfinished assignments remain deliberate waits. |

Classification prefers native `codexErrorInfo`, JSON-RPC data, HTTP status and OS error codes. If a terminal turn omits its error body, the exact turn’s persisted error notification supplies the details; a successful native internal retry does not retain a stale failure or schedule another application retry. There is a small compatibility fallback for older message-only connection/limit errors. Unknown errors are permanent rather than assumed transient. Native `error` notifications with `willRetry` are recorded; application retries happen after a terminal turn or transport failure, leaving native internal retries alone.

## Resume versus retry

Before starting inference after an interruption, read native thread history. A completed turn supplies its saved result and receives no additional coding or reporting inference for that turn. A permanently failed turn finishes normally. A transiently failed or interrupted turn gets a follow-up on the same native thread and source directory, asking it to inspect partial changes and continue only unfinished actions.

Each new native turn start first persists its input and a unique `clientUserMessageId`. If the reply is lost before its turn ID is saved, locate that input in native history by its client ID (or canonical input for older servers). A confirmed rejection/prelaunch failure can be retried without pretending a turn was accepted. A missing known turn or historical launch without sufficient evidence remains a meaningful uncertain failure/wait for the manager; it is not blindly replayed.

The application does not reset files, replace worktrees, revert commits or create a replacement assignment for recovery. Prior native turns and persisted events retain partial output and action history. A resumed manager uses its original execution identity so replayed delegation/continuation tool calls reuse their existing assignments, even if regenerated native call IDs differ.

## Backoff and usage limits

- Connection/process recovery: approximately 4–5 seconds initially, doubling to a 4–5 minute cap, with jitter.
- Usage limits without a reset: approximately 4–5 minutes initially, doubling to a 4.8–6 hour cap. These are next-attempt times, **not claimed quota reset times**.
- Structured reset timestamps or `Retry-After` take precedence over fallback backoff; attempts never precede the provided eligibility time.
- After an actual usage-limit error, one bounded `account/rateLimits/read` metadata call can retrieve exhausted primary/secondary window reset timestamps. No model call is made to ask when quota resets. Reset credits are not consumed and fallback models are not enabled.
- The shared gate uses the existing settings table because all workers/managers use the same configured ChatGPT account. It survives restarts. One actual unfinished turn probes availability after eligibility; others wait. Successful inference clears that gate. Already-running requests can still encounter the same limit before the first failure is known.
- Each scheduler tick checks persisted eligibility. Waiting produces no repeated AI calls, errors in conversation or retry notifications. Retryable conditions have bounded intervals but no arbitrary attempt count that would abandon a long outage.

Task and manager badges show the waiting reason and next eligible attempt. Actual dispatch can be later because of project pause, worker capacity or the shared probe. Reporting stays conversational: the manager receives a compact exact-result reference, retrieves relevant retained evidence and decides how to report it or continue authorized incomplete work.

## Intentional Pause / Cancel (prepared 2026-10-12)

[Agent controls](AGENT_CONTROLS.md) extends the existing stop-request field without migration.
Requests retain the project lease until owned-process cleanup confirms paused/cancelled state,
bypass automatic retry and never replay after restart. Legacy Stop remains deliberate waiting.
The new release is prepared only; earlier deployed recovery receipts below remain historical evidence.

## Persistence, startup and ownership

Schema **9** adds execution fields: `retry_at`, `retry_count`, `recovery_reason`, `mode`, `launch_input`, `launch_key`, `stop_requested`. Existing task states are unchanged. An unfinished recovering execution has no `finished_at`; its task uses `waiting` and retains its original `lease_owner`. Recovering workers do not occupy active capacity, but their project claim prevents another assignment from writing concurrently. Terminal completion atomically saves output and pending delivery; release happens after the owned process closes.

Startup acquires the existing exclusive service lock and starts the scheduler without waiting for upstream access. Scheduler passes reconcile disappeared processes, completed attempts with abandoned leases, due recoveries, pending manager responses and completion delivery. Migration preserves all old rows and derives unfinished manager modes/launch inputs from saved context events when available. This preserves Conversation mode across an upgrade.

A surviving detached process retains exclusivity. Only a receipt-matched PID/start time, same-user process, dedicated process-group leader with an `app-server` argument can be terminated as an orphan. It receives SIGTERM, then SIGKILL on a subsequent eligible pass if it survives. Unprovable process ownership is retained rather than risking a second writer. Normal systemd shutdown retains `KillMode=control-group`; graceful application shutdown parks work for automatic reconciliation instead of publishing a false failure.

Before releasing a live worker's claim, App Server cleanup also kills residual children in its dedicated process group, including when the parent has already died. A saved process birth identity prevents signaling a recycled parent PID. Commands that deliberately detach outside that group remain subject to the limitations below.

## Duplicate protections and limits

SQLite immediate transactions serialize task claims. The project running index, retained leases and application service lock prevent overlapping project writers. In-memory launch guards prevent repeated launches from one scheduler. Manager conversations serialize across running **and recovering** responses. Only events for the current native turn can finish a retry; delayed older-turn completions are ignored. Worker completion and manager finish are idempotent; `result:<execution_id>` is the stable worker-message ID. The same manager reply is resumed instead of inserting another response. Completed native turns and terminal tasks are not automatically restarted.

This is not a guarantee of exactly-once external side effects. Native history can be lost/corrupted, commands can detach outside an App Server process group, and a crash can occur between an external effect and its native receipt. The worker must inspect existing edits/actions when resuming; uncertainty without sufficient evidence is reported naturally. Proven ownership, available native history and the unchanged single-owner service boundary are required for automatic recovery. An unrecoverable login/configuration error still requires fixing the account/configuration. A deliberately cleared origin discards delivery; archived origins retain their exact conversation.

## Validation

Run the documented development checks using the repository Python environment:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. .venv/bin/python scripts/browser_smoke.py
PYTHONPATH=. .venv/bin/python scripts/smoke_minimal.py
```

`tests/test_recovery.py` injects network/quota failures, repeated outages, backend and graceful manager restarts, lost start replies, actual disposable App Server process termination, manager outage during delivery, five sequential assignments interrupted between steps, duplicate completion/replayed tools, permanent failures, independent projects, capacity and deliberate pause/stop. It checks stable identities, retained partial files/history, one final result, shared gate persistence/single probe, authoritative and unknown reset handling, schema migration and no inference while waiting. Fixtures never disconnect production or exhaust a real account.

Real installed Codex acceptance passed in **45.5 seconds**, with one coding task, local edits/tests/commit and automatic manager reporting; no approvals or duplicate manager report. Browser acceptance passed existing conversation, selection/copy/archive, model settings and desktop/mobile/narrow layouts without JavaScript errors or overflow. **82 regression tests passed**; Ruff, JavaScript syntax and Git whitespace checks passed. Browser fixtures also verify persisted worker/manager recovery reasons, retry timing, conversation serialization and responsive waiting badges. A real disposable parent/child test reproduced the old orphan-child failure and now verifies cleanup after parent death; another test verifies recycled-PID protection. Deployment receipts are recorded below after release verification.

## Release

Implementation commits: **`299013ad20fd9657f54ee99051c42fe2c9cded94`** (recovery), **`f11b30f9c72d027ff3f6075783f8658620c64f3f`** (native-event reconciliation hardening), and **`71d8e2979958a4fcf1dfc24cce221988c81a7a35`** (owned command-child cleanup). Deployed version: **`automatic-recovery-stage3-20261011.2`**, schema **9**, which includes the implementation and this documentation receipt. The exact release commit is resolved by that Git tag, exposed as startup-captured `/api/auth-status.release_commit`, and recorded in the private `running-release.json` receipt below.

The documented separate-checkout/verified-backup/idle-cutover procedure was used. The implementation cutover at **2026-10-11 20:54:42 +04** changed service PID **1871357 → 1934152**. Only `linko-bot-manager.service` was stopped/started. Its existing environment, Python dependencies, service unit, daily backup timer and Tailscale listener were retained. The final tagged release also includes the tested current-turn matching, notification-only error safeguards and owned child cleanup. This receipt’s documentation commit adds no further executable changes.

Verified private backup:

```text
/home/codex/.local/share/linko-bot-manager/backups/automatic-recovery-20261011T172706Z/
```

The original pre-migration backup also remains at `automatic-recovery-20261011T165252Z` alongside this fresh final-release backup. The final backup contains the integrity/foreign-key-checked `manager.sqlite3`, copied service/environment settings, native state/history SQLite backups, **all 63 referenced native rollout histories**, and private hash/HTTP/cutover receipts. There were no missing native references. Schema migration and complete application startup were rehearsed against separate copies using mock workers and a disposable allowed project root.

After deployment, integrity and foreign keys passed. **Every original table, column value and row hash remained identical**, including **11 projects, 57 tasks, 10 conversations, 56 messages, 120 executions and 111,352 events**, plus owner/sessions, settings, notes and historical evidence. Schema 9 is the only new schema-version entry. No unfinished execution, held project claim or pending/running completion delivery was introduced; all previous task dispositions were preserved.

The backend reports the release commit and an active scheduler. Loopback and existing private HTTPS returned **200** for the API status and interface; protected portfolio requests remained **401** without authentication. Served HTML, JavaScript and CSS matched the committed application files. Startup completed without errors; subsequent idle scheduling intervals produced no queue failure. The initial deployed checkout's real disposable Codex acceptance passed in **47.5 seconds**, with one worker, preserved tests, local edits/commit and automatic manager continuation in the originating conversation, without approvals or duplicate reporting. Native-event hardening also passed the real disposable acceptance in **42.5 seconds**. Additional regressions cover delayed older-turn completions, notification-only quota errors after restart, successful native internal retries without an extra application attempt, and owned child cleanup after parent death. No production acceptance task, chat reset, model/account change or unrelated service restart was used.

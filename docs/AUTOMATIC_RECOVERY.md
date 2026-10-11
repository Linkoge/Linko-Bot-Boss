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

Classification prefers native `codexErrorInfo`, JSON-RPC data, HTTP status and OS error codes. There is a small compatibility fallback for older message-only connection/limit errors. Unknown errors are permanent rather than assumed transient. Native `error` notifications with `willRetry` are recorded; application retries happen after a terminal turn or transport failure, leaving native internal retries alone.

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

Task and manager badges show the waiting reason and next eligible attempt. Actual dispatch can be later because of project pause, worker capacity or the shared probe. Reporting stays conversational: the manager receives the actual final worker result and decides how to report it or continue authorized incomplete work.

## Persistence, startup and ownership

Schema **9** adds execution fields: `retry_at`, `retry_count`, `recovery_reason`, `mode`, `launch_input`, `launch_key`, `stop_requested`. Existing task states are unchanged. An unfinished recovering execution has no `finished_at`; its task uses `waiting` and retains its original `lease_owner`. Recovering workers do not occupy active capacity, but their project claim prevents another assignment from writing concurrently. Terminal completion atomically saves output and pending delivery; release happens after the owned process closes.

Startup acquires the existing exclusive service lock and starts the scheduler without waiting for upstream access. Scheduler passes reconcile disappeared processes, completed attempts with abandoned leases, due recoveries, pending manager responses and completion delivery. Migration preserves all old rows and derives unfinished manager modes/launch inputs from saved context events when available. This preserves Conversation mode across an upgrade.

A surviving detached process retains exclusivity. Only a receipt-matched PID/start time, same-user process, dedicated process-group leader with an `app-server` argument can be terminated as an orphan. It receives SIGTERM, then SIGKILL on a subsequent eligible pass if it survives. Unprovable process ownership is retained rather than risking a second writer. Normal systemd shutdown retains `KillMode=control-group`; graceful application shutdown parks work for automatic reconciliation instead of publishing a false failure.

## Duplicate protections and limits

SQLite immediate transactions serialize task claims. The project running index, retained leases and application service lock prevent overlapping project writers. In-memory launch guards prevent repeated launches from one scheduler. Manager conversations serialize across running **and recovering** responses. Worker completion and manager finish are idempotent; `result:<execution_id>` is the stable worker-message ID. The same manager reply is resumed instead of inserting another response. Completed native turns and terminal tasks are not automatically restarted.

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

Real installed Codex acceptance passed in **45.5 seconds**, with one coding task, local edits/tests/commit and automatic manager reporting; no approvals or duplicate manager report. Browser acceptance passed existing conversation, selection/copy/archive, model settings and desktop/mobile/narrow layouts without JavaScript errors or overflow. **74 regression tests passed**; Ruff, JavaScript syntax and Git whitespace checks passed. Browser fixtures also verify persisted worker/manager recovery reasons, retry timing, conversation serialization and responsive waiting badges. Deployment receipts are recorded below after release verification.

## Release

Release version: **Stage 3 — automatic recovery, schema 9**. The implementation commit and actual deployed commit, backup location, preservation checks and service verification will be appended after the documented deployment procedure completes.

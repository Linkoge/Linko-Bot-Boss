# Linko Bot Boss

A private, single-owner conversational manager for software projects: **you → manager → one worker → merge → deploy**.
FastAPI + SQLite + plain JavaScript; Codex or Claude does the thinking and coding. The installed names stay unchanged
(`ai_manager`, `AI_MANAGER_*`, the service, the data directory and the private URL
`https://codex-server.tail5ebb9d.ts.net:8787/`). Repository: `Linkoge/Linko-Bot-Boss`.

## How work runs

One global queue, strictly sequential. States: **queued → running → done | failed**. A **held** task is a queued task with
the hold flag set; it is skipped until released. Order is the only dependency. The code is `ai_manager/core.py`; the rules
both the manager and every worker read are [RULES.md](RULES.md).

1. **Worker.** The oldest eligible queued task gets an isolated Git worktree and branch
   (`linko/assignment-<id>`, kept on disk, never deleted). The worker implements, adds the permanent tests for its change
   and commits. Projects that are not Git repositories run in place, with no checks and no merge.
2. **Checks.** After every worker turn the application runs the permanent checks: the one fixed smoke suite
   (`python -m pytest -q -m smoke`, 180 s) plus the test files the branch added or changed (600 s). Only permanent
   repository suites can block delivery; one-off probes are informational. Probes run under a 60 s timeout. Checks run
   with disposable data directories.
3. **Repair rounds** (durable on the task row, never reset by retry, restart or continue). The first 3 failing rounds go
   back to the same worker. When the 3rd round still fails the core compares it with the round before: the failing check
   changed or there are fewer failures means progress and grants +3; the same check failing the same way means a loop
   and the task fails with a plain reason. The same checkpoint runs at 6. The hard cap is 9. Every checkpoint decision
   and reason is stored on the task (`checkpoints`).
4. **Merge and deploy.** A passing branch merges into `main` under the checkout fence (one at a time). Main is first merged
   into the branch; a conflict goes to the same worker as a repair round of the same task (no separate integration step).
   With the project's **Auto-merge and deploy finished work** ON, the merge happens and, for the manager's own project,
   the deploy script is requested automatically. OFF means no merge and no deploy; the manager's `deploy` action
   (optionally with a `task_id`) merges a finished task and deploys on request.
5. **Queue runner** (`ai_manager/runner.py`; independent of any open chat or owner message). A tick every second starts
   the oldest eligible queued task, one at a time. Not eligible: held, project paused, waiting for its retry time, or a
   deploy request pending. On service start, whatever was running is resumed without penalty (same branch, same worker
   thread, repair counters untouched) and stale retry times are forgotten, so a restart or reboot always retries at
   once. A provider usage limit or a lost connection puts the task back in the queue with a visible retry time: the
   provider's reset time when it gave one, else backoff (usage limit 5 min doubling to 1 h, connection 30 s doubling to
   10 min), 10 waits, then failed. The watchdog (every minute) requeues a dead or hung worker (30 min without activity)
   once, then fails it; fails queued tasks of a removed project; expires an unconsumed deploy request after 7 h.
   Nothing stays blocked without a visible reason.

Owner controls on a task: **Hold / Release** (queued), **Cancel** (queued or running; the branch is kept),
**Continue** (failed or done task: appends instructions, re-queues at the end, counters stay). A worker may ask the owner a
question; the task stays running and says so. Finished results return to the originating chat automatically.

## Deploy

`scripts/deploy.py` is the only deploy mechanism: full backend suite once, wait for idle, back up SQLite, restart the
service on `main`, health check, and automatic rollback of code and database on any failure after the stop. The short
result is `DATA/deploy/last-deploy.txt`. No new work starts and nothing merges while a request is pending (the
checkout fence, `ai_manager/fence.py`). One-time operator bootstrap, no sudo:
`python3 scripts/install_deploy.py`. Manual deploy: `python3 scripts/deploy.py --now`.
Concurrent consumers wait on the deployment lock and re-read the request after it is released; a systemd consumer can
resume a request abandoned by a manual caller. During the idle wait, `DATA/deploy/state.json` records the deploy PID,
blocker counts and bounded record identities, refreshed at least every 30 seconds (also in `deploy.log`). Queued,
held and retry-delayed tasks remain durable and do not block restart; unfinished executions, task leases, running
tasks, streaming messages and running deliveries do. Stale leases are reconciled by the queue runner only after
both the runner and its recorded process are gone, preserving workspaces and limiting recovery retries.


## Product behaviour that is unchanged

- Chat with Conversation (read-only except To-Do), Auto and Execute modes; composer with recording/transcription,
  Discard/Upload, arbitrary attachments (only verified raster images become native image input), drafts and sequential Send.
- Model settings: one manager choice and one worker choice, each any Codex or Claude model and effort; what the panel shows
  is what is saved (`model_choice`) and what is dispatched. A task pins its worker pair when it is created. Two manager favorites.
- Project list with display names, visibility/stash, pause, discovery; To-Do backlog (never executes by itself; Start
  hands exact revisions to a task); project memory brief with owner-run review; chat clear/trash/archive/search.
- Installable PWA (`/` scope, no service worker), account usage display, Tailscale-only private hosting.
- Security: Argon2 owner login, one-time setup token, revocable sessions, CSRF/origin/host checks, CSP, throttling.
  Workers run as the service user with broad access; the rules ask them to confirm before catastrophic destruction.
  That is an agent convention, not a sandbox.

## Whole-session audio recording

Microphone capture requires a secure context (HTTPS, or localhost for development),
MediaRecorder, microphone permission and a supported recording format. One app-level
session retains its original project, conversation and mode across internal views,
project switches and stashing. The persistent bar names that destination and exposes
Stop, Discard, and (after interruption) Download/Retry. Stop transcribes the whole
recording once into the original draft; explicit composer Send retains the existing
whole-recording send flow. Navigation suppresses automatic Send. Before insertion,
the original conversation is checked; a replacement chat never receives its transcript.

Changing browser tabs/apps or document visibility does not stop capture. Delayed timers
are not evidence that the microphone stopped. Where available, a screen wake lock is
requested during visible recording, released on hide/stop/discard, and requested again
on return. Denial, low-power policy and missing wake-lock support leave capture usable;
the bar shows whether the screen is being kept awake. This API keeps a visible screen
awake; it does not grant background execution. See the
[Screen Wake Lock specification](https://w3c.github.io/screen-wake-lock/).

Recorder errors/stops, ended tracks and page freeze/departure retain captured audio
for the existing review/retry flow and remove the active indicator. A muted microphone
shows a pause warning, continues the same recorder where possible, and requires review
on Stop because audio may be missing. All MediaRecorder data events are assembled into
one Blob and one transcription request after capture ends: no separately transcribed
segments or partial transcript insertion. Existing IndexedDB recovery checkpoints are
best effort; final data may never arrive if the browser/process is killed. Download
important interrupted recordings before leaving. Stop/Discard releases microphone,
timers and screen lock, including a lock granted after the session has ended.

Permanent regression coverage: `tests/test_audio_continuity.py` uses real headless
Chromium capture with a fake microphone and transcription provider, internal navigation,
a second browser tab, visibility events, recorder/track interruption, and simulated denied/unsupported wake
locks. `scripts/browser_audio.py` additionally covers the existing upload, Send, retry,
recovery and narrow-layout flows. Visibility simulation does not establish mobile OS
background support. Mobile app switching, screen lock, browser freezing, calls,
permission revocation and power policy can pause or terminate microphone capture.
Physical iOS/Android locked-screen behavior has not been verified here. Neither a PWA
nor screen wake lock bypasses OS/browser suspension; keep the app visible for the best
chance of continuity. Existing 20-minute/32-MiB limits still apply.

## Whole-message Copy and Delete

Each whole owner/assistant message in Chat and saved/archive history has one small, low-opacity
lower-right arrow after its text and attachments. Expand it to reveal Copy and Delete underneath.
There is no Edit. Keyboard activation is supported. Copy includes the entire source text across all
rendered paragraphs, lists, tables and code blocks; during streaming it includes all text received so far.
The existing clipboard fallback applies.

Delete opens a confirmation dialog with Cancel focused. Queued/running/recovering responses and their
owner messages must finish or be stopped first. The owner/CSRF-protected endpoint independently checks
confirmation and exact project/conversation/message membership. Deletion persists across restart and
removes the whole display message from Chat, saved/archive readers, search and chat copy exports.
Idempotent SQLite tombstones and an audit event retain native/provider history, internal prompt context,
execution output, tasks and attachments; this is not provider-side erasure. Polling removes deleted IDs
while preserving older undeleted rows outside the rolling history tail. Clear/archive/trash confirmation
uses the visible tail after deletion.

Permanent checks: `tests/test_message_actions.py`, `tests/test_chat_history.py` and
`scripts/browser_markdown.py` (streamed multi-block responses, owner/assistant copying, Cancel/confirm,
reload, saved-reader removal, keyboard and narrow layouts). Historical source and assignment evidence:
[retained Copy/Delete report](docs/MESSAGE_ACTIONS_978211.md).

## Run and develop

```bash
uv sync --locked
uv run playwright install chromium               # required by audio browser regression tests
uv run python -m ai_manager.cli setup-owner     # first time only
uv run pytest -q -m smoke                       # the fixed smoke suite, every task
uv run pytest -q                                # full suite: run by the deploy script
uv run ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. uv run python scripts/browser_smoke.py   # also browser_tasks.py and the other browser_*.py checks
```

Configuration: `AI_MANAGER_DATA` (default `~/.local/share/linko-bot-manager`), `AI_MANAGER_PORT` (8787),
`AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE`, `AI_MANAGER_CODEX`,
`AI_MANAGER_CLAUDE`, `AI_MANAGER_REPO` (live checkout, tests only). Daily database backup: `scripts/backup_daily.py`
(restore only to a fresh path with the service stopped; native thread storage and project files are backed up separately).

## Data and history

Existing projects, chats, To-Dos and task/execution history rows are preserved. Schema 13 (the task queue) added columns to
`tasks` and closed every older non-final task once (failed, "Cancelled: closed when the task queue was rebuilt"; branches and
workspaces untouched). Old columns (`lifecycle`, `legacy`, …) and old settings keys are ignored history; the old per-provider model
settings (`model_settings`, `model_settings:claude`) are adopted once (the most recently edited one) into `model_choice`. The earlier
release/helper/tier/reviewer/integration/follow-up machinery is gone; its history is in Git.

`ai_manager/static/icons/README.md` records the owner artwork provenance.

## Account usage

The usage button follows the selected provider (Codex or Claude). Details show reported five-hour
and weekly remaining percentages and reset countdowns; reset timestamps are available to assistive
technology and on hover. Missing windows remain Unavailable. Cached readings are marked stale after
three minutes, a passed window reset, or refresh failure. Claude readings retain their original event
time across disk reads and restarts; reading saved metadata does not refresh the observation.

The metadata-only `account/rateLimits/read` request includes available reset-credit details. The dialog
shows the provider's available reset count, individual available-credit expiration dates, and reported
credit balance/unlimited status. Counts are never inferred from the detail list: it may be partial or
unavailable. Explicit null expiration means no expiration; absent/invalid expiration is Unavailable.
Balance expiration is unavailable in the current protocol. Claude's retained event adapter supplies
windows only, so reset credits, balance and expiration remain Unavailable unless authoritative metadata
is supplied. Unsupported data is never represented as zero. Account/provider changes discard old readings.
There is no reset/redeem action. Cached limits, empty balances, unavailable metadata and refresh failures
never disable Send or preflight a message. Real provider errors still follow the existing queue behavior.

Permanent regressions: `tests/test_account_usage.py`, `tests/test_usage_display.py`, and
`tests/test_claude_provider.py`; responsive interaction checks remain in `scripts/browser_account_usage.py`.
Protocol reference: installed Codex 0.162.1 generated `GetAccountRateLimitsResponse`,
`RateLimitResetCreditsSummary`, `RateLimitResetCredit`, and `CreditsSnapshot` (also available in the
[upstream schema](https://github.com/openai/codex/tree/main/codex-rs/app-server-protocol/schema/typescript/v2)).

Selected source: To-Do `4e9e0e7b530543e3a19dee56acddb351`, revision 0, implemented by assignment
`a51aadfc0f6f455d8873efe1c67ab91c` after inspecting the retained implementation and
[original assignment handoff](docs/MESSAGE_ACTIONS_978211.md). Its selected description is:
“Fix Claude usage/reset display to match available Codex information. Expose available reset
counts/credits and expiration for both where authoritative data exists. Information only, no reset
action; unsupported fields honest; never block Send.” The original assignment
`978211a9c69a48dfa9347b3620b4db1b` runs only Copy/Delete (revision 8); this sequential assignment does
not amend or resume it. Original descriptions, links, screenshot and archived records remain preserved
in that handoff and commit `11ff4765c8fcd897bc03baf99cde1d19bdd31087`. This worker does not consume or
archive the source To-Do; the application owns disposition only upon successful acceptance.

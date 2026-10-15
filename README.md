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
5. **Watchdog** (every minute). A dead or stalled worker (30 min without activity) is requeued once, then failed with a
   plain reason. A provider usage limit makes the task wait in the queue and retry with bounded backoff taken from the
   provider's reset time (else 5 min doubling to 1 h, 8 waits, then failed). A removed project fails its queued tasks;
   an unconsumed deploy request expires after 7 h. Nothing stays blocked without a visible reason.

Owner controls on a task: **Hold / Release** (queued), **Cancel** (queued or running; the branch is kept),
**Continue** (failed or done task: appends instructions, re-queues at the end, counters stay). A worker may ask the owner a
question; the task stays running and says so. Finished results return to the originating chat automatically.

## Deploy

`scripts/deploy.py` is the only deploy mechanism: full backend suite once, wait for idle, back up SQLite, restart the
service on `main`, health check, and automatic rollback of code and database on any failure after the stop. The short
result is `DATA/deploy/last-deploy.txt`. No new work starts and nothing merges while a request is pending (the
checkout fence, `ai_manager/fence.py`). One-time operator bootstrap, no sudo:
`python3 scripts/install_deploy.py`. Manual deploy: `python3 scripts/deploy.py --now`.

## Product behaviour that is unchanged

- Chat with Conversation (read-only except To-Do), Auto and Execute modes; composer with recording/transcription,
  Discard/Upload, arbitrary attachments (only verified raster images become native image input), drafts and sequential Send.
- Model settings: manager and bot (Manual or Automatic) for Codex and Claude, two favorites, exact pairs pinned per task.
- Project list with display names, visibility/stash, pause, discovery; To-Do backlog (never executes by itself; Start
  hands exact revisions to a task); project memory brief with owner-run review; chat clear/trash/archive/search.
- Installable PWA (`/` scope, no service worker), account usage display, Tailscale-only private hosting.
- Security: Argon2 owner login, one-time setup token, revocable sessions, CSRF/origin/host checks, CSP, throttling.
  Workers run as the service user with broad access; the rules ask them to confirm before catastrophic destruction.
  That is an agent convention, not a sandbox.

## Run and develop

```bash
uv sync --locked
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
workspaces untouched). Old columns (`lifecycle`, `legacy`, …) and old settings keys are ignored history. The earlier
release/helper/tier/reviewer/integration/follow-up machinery is gone; its history is in Git.

`ai_manager/static/icons/README.md` records the owner artwork provenance.

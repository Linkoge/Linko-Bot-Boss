# Radical simplification implementation

Historical deployment record. Current modes, schema creation and validation are described in [CODEBASE_CLEANUP.md](CODEBASE_CLEANUP.md); archived results now stay in their exact origin and cleared origins are discarded.

Date: 2026-10-11. Implements [the audited proposal](RADICAL_SIMPLIFICATION_PLAN.md).

## Delivered architecture

User → conversational AI Manager → native Codex worker → AI Manager → User.

Direct delegation stores one objective and its conversation origin. The always-on queue claims one worker per project under a global capacity limit. Native Codex investigates, edits, installs, executes, tests and uses Git in the actual project directory. Worker output and pending manager delivery are committed together; the result returns through an ordinary manager turn. No separate planner, DAG, verification controller, reviewer or integration pipeline remains.

Deleted ten backend modules: administration, capabilities, policies, integration, memory projections, verification, verification runtime, verification strategies, verification workspace and custom workspace/command isolation. Removed related API/UI/CLI configuration, three unused model roles, chat modes, scope/batch scheduling, daily-summary supervision and old controller-specific harnesses/tests. Kept authentication, private hosting, archive/search, project navigation, owner notes, transactional claims, process ownership, native history inspection and backups.

Backend falls from **22 modules / 6,631 lines to 12 modules / 2,213 lines**, a **66.6% net reduction** including comments and formatting. Active task lifecycle has six values: queued, running, waiting, completed, failed and cancelled. Legacy SQL status columns and supervision tables are frozen historical data; only `lifecycle` drives new execution. There is no dual scheduler.

## Persistence and migration

Schema 7 adds assignment origin/replay/native-thread/lifecycle fields, execution input/delivery associations and durable native questions. It preserves all original tables, IDs, column values, histories and private notes. Historical completed/cancelled/failed tasks retain their disposition; other legacy assignments become waiting in the new lifecycle and are not replayed automatically. Original historical status values remain unchanged. Continuation reuses the same task and supplies previous workspace/output references for inspection.

A migration rehearsal against an online production backup passed integrity/foreign-key checks and compared every original column of every table other than the version marker: all rows and values matched. Rehearsed task counts were 21 cancelled, 3 completed, 3 failed and 18 waiting. The backup is private under `~/.local/share/linko-bot-manager/backups/radical-simplification-20261011-122521/`; its migration copy and hash manifest remain there.

Owned worker processes are closed before project claims are released. Startup and the ordinary queue inspect detached claims; surviving processes keep their slot. Known persisted terminal results are recovered without launching another worker. Missing/uncertain turn identity remains waiting instead of being blindly replayed. Authorized active work stopped during an orderly shutdown returns to the ordinary queue with its native thread. Deliberate pauses and cancellations remain paused/cancelled.

Result delivery serializes with user turns. Archived origins retain the raw result and forward it to the fresh conversation in the same context. Cleared origins fall back to that project's current conversation. Exactly-once SQLite associations are provided; uncertain manager inference is retained for inspection rather than claiming universal exactly-once execution or external-side-effect deduplication.

## Validation

- **19 targeted regression tests passed**: authentication/CSRF/session protections, saved/archive history, migration preservation, replay keys, project exclusivity and future-project defaults, native assignment/result flow, restart delivery, uncertain launch handling, detached ownership, shutdown/pause behavior, explicit question answers and backup/restore. The old controller/profile tests were removed with their requirements. One existing Starlette/httpx deprecation warning remains.
- Ruff, JavaScript syntax, unique DOM IDs/reference checks and Git whitespace checks passed.
- Real native Codex smoke passed in **43.1 seconds**: a natural manager request created exactly one assignment; Codex fixed addition, preserved tests, ran them, made a local commit, and the manager returned the result automatically. Two manager turns and one worker execution; no independent verification result or routine approval. Disposable repository/database only; no push.
- Chromium passed login, chat, archive/search, rename and the two model routes. Desktop 1440×1000 and mobile 393×851 had no JavaScript errors or horizontal overflow. Screenshots were inspected; fixture artifacts remain at `/tmp/linko-minimal-{desktop,mobile}.png`.
- A real catastrophic fixture returned “Awaiting explicit confirmation. No files have been modified.” Its protected file remained intact. Native blocking question routing and explicit decline were separately covered by regression tests. The real conversational return check also passed: the manager marked the same task waiting and asked for explicit confirmation naming the exact disposable directory; its protected file remained unchanged.

Nested-sandbox HTTP tests stalled; the identified isolated pytest process was stopped and the suite passed outside that nested sandbox. No unrelated process was stopped.

## Operating tradeoffs

Workers now have broad native same-user access. Old no-host-home bubblewrap isolation, source-preserving clones, per-command preimages/resource budgets, command registration, operation replay ledger and independent acceptance certification are gone. Git/files/native history replace manager-generated recovery copies, without promising equivalent transactional recovery. Completion is reported, not independently certified.

Catastrophic confirmation is agent-recognized. Native full access cannot deterministically intercept every destructive program. If a worker returns a conversational request instead of a native blocking question, the existing manager turn records the task as waiting and asks the user; their answer continues the same assignment. No extra reviewer/intent model or worker tool framework was introduced.

## Deployment

Release `9e246c2d3a905e5cdecf52a8daba343621f0caff` was fast-forwarded into the live repository. Only the dedicated `linko-bot-manager.service` was stopped/started, entering active/running state at **2026-10-11 12:30:37 +04**, PID **1656742**, exit status **0** (previous PID 1585576). No active execution existed immediately before cutover. No network listeners, dependency environment, credentials or unrelated services were changed.

Post-deployment loopback and private HTTPS health returned **200**; unauthenticated portfolio returned **401**. Served HTML/JavaScript/CSS hashes exactly matched the tested release. SQLite schema is **7**; integrity and foreign-key checks passed. Every original row and original column value outside the schema-version marker matched the predeployment hash manifest: **11 projects, 45 tasks, 56 messages, 87 executions, 108,619 events, one owner and two sessions** preserved, along with historical evidence/settings/notes. No coding worker was active and no legacy objective was replayed. New lifecycle counts match the rehearsal: 21 cancelled, 3 completed, 3 failed, 18 waiting.

Private `before.json`, `after.json`, the integrity-checked predeployment backup and migrated rehearsal copy remain in the backup directory named above. No production test message, password/session mutation, remote push or merge of legacy clone output was performed. The complete behavioral smoke and authenticated browser checks ran only against disposable fixtures. This deployment check confirms health/assets/data preservation, not a new real production user assignment.

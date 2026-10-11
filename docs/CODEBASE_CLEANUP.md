# Codebase cleanup

Date: 2026-10-11. Baseline: `20f008f` (automatic manager continuation). Implementation and verified deployment receipt are recorded below.

## Findings and changes

The preceding release `9e246c2` already deleted the planner, task DAG, independent verifier/reviewer, integration controller, capability broker, permission profiles and custom command runner. This cleanup does not claim those previous deletions as new work. All twelve remaining backend modules serve the current application.

Removed fresh-database creation of ten obsolete tables: `dependencies`, `checkpoints`, `plans`, `verification_results`, `policies`, `approvals`, `operations`, `notifications`, `verification_commands`, and `integration_results`. Removed fresh task fields for priority, permission/verification metadata, task kinds, recovery identity, blocker codes and lease expiration, plus the unused execution `base_commit` migration and legacy queue index. Existing databases retain these tables/fields and their original records.

Removed the unused `STATUSES` constant, task-feedback/task-action browser wrappers and associated state, old history-feature detection, obsolete sidebar/dashboard/task-gate CSS, the unused `/api/audit` endpoint and unused model-catalog response fields. Events and usage remain available through execution history; model/effort settings and catalog validation are unchanged. The task claim now reads its workspace from the existing project join instead of a second database lookup.

Shortened manager instructions from 1,213 to 728 characters and worker instructions from 596 to 377. Removed repeated result-report directions from every delivery message and duplicated operational claims from the manager tool description. Kept assignment context, native tools, requested-work semantics, result handling, preservation of user edits and the catastrophic-action boundary. No generic intelligence instructions or token-optimization framework was added.

Restored the required Conversation/Auto/Execute modes that the previous simplification removed. All three use the existing manager turn: Conversation blocks mutations in code, Auto delegates requested work, and Execute treats the message as an objective. No planner call, execution shortcut or additional hierarchy was restored. Chat submissions now carry the displayed conversation ID so stale submissions cannot land in a replacement conversation.

Delegation uses one replay key per manager turn/project instead of one per tool call. Exact repeated unfinished objectives in the same conversation reuse their existing task. Reuse receipts in the existing event history preserve replay identity even after that task completes. Distinct projects and completed objectives remain independent; paraphrased objectives still depend on manager judgment.

Consolidated real-worker acceptance into `scripts/smoke_minimal.py` and deleted the duplicate `scripts/smoke_manager_continuation.py`. The consolidated script tests file creation/modification, native commands/tests/Git, the real background scheduler, automatic delivery and repeated completion. Fixed browser acceptance to select its named archive instead of whichever archive/current conversation sorted first.

Removed superseded research/controller/runbook documents: `AGENT_ORCHESTRATION_RESEARCH.md`, `STAGE2_ACCEPTANCE_PREPARATION.md`, `autonomy-upgrade.md`, `execution-reliability.md`, `inventory.md`, and `verification.md`, and seven Stage 1/2 JSON snapshots. Replaced accumulated STATE/HANDOFF/DECISIONS instructions with current references. Stage 1/2 and radical-plan documents now summarize their historical context and link to their complete original versions in Git. Updated current autonomy and operations documentation, including exact archived/cleared-origin behavior. Historical audit evidence remains retrievable at baseline `20f008f`; production private notes were not changed.

No backend module or package dependency was deleted in this pass. FastAPI/Uvicorn serve the application, Argon2 protects owner authentication, and every development dependency supports an existing test/lint/browser check. Installation, service templates, backup/restore, discovery and read-only CLI reconciliation remain useful operational tools.

## Production execution path

| Step | Modules, calls, persistence and transition |
| --- | --- |
| User submission | `static/app.js` → authenticated/CSRF-protected `POST /api/chat` in `web.py`. `auth.py` checks owner/session; `config.py` supplies global runtime paths/capacity. Message contains selected project, current conversation ID and mode. |
| Manager turn | `Chat.submit` in `chat.py` resolves project/conversation through `registry.py`, validates configured model/effort against the App Server catalog, serializes the conversation, and transactionally inserts user/assistant messages, manager execution and `chat.queued`. One background asyncio job runs the manager turn. |
| Native manager context | `codex.py` starts the owned stdio App Server, checks the existing ChatGPT account and model catalog, and starts/resumes a persistent read-only thread. `Chat._run` stores thread/process/turn identity, streams output/events and supplies mode, project/global instructions, owner notes and current assignments. Thread-schema rollover uses saved conversation history. Only the small `manager_action` tool can mutate manager state. |
| Complete delegation | `Scheduler.manager_action` in `orchestration.py` calls `Tasks.add` in `tasks.py`. A transaction persists one objective, conversation origin and replay key, or returns the existing assignment. No planner/model call or dependency graph. Assignment becomes `queued`. |
| Global scheduling | `web.py` lifespan holds the single-manager file lock, recovers recorded native attempts, discovers repositories, loads the existing global capacity, and starts `Scheduler.run`. The one-second loop inspects unresolved worker ownership, atomically claims eligible active projects through `Tasks.claim`, then drains pending manager results. Claims create an execution and set task `running`/`lease_owner`. All existing/future registered projects participate; one coding worker per project and global capacity remain enforced. |
| Autonomous worker | `Workers.launch/_run` in `workers.py` creates an owned native App Server in the registered project directory. Existing worker model/effort, objective, global/project instructions and owner notes accompany one persistent thread/turn. Codex reads/edits files, runs commands, installs dependencies, validates and uses Git itself. Streamed output, usage and native events persist in SQLite. No broker, verification registry, reviewer or routine approval controller. |
| Questions and outcome | Native information/catastrophic questions persist in `questions`, move the task to `waiting`, and require an explicit answer through the existing authenticated endpoint. A completed/failed/interrupted turn calls `Workers.finish`, atomically persisting output/error/timing, task outcome and `delivery='pending'`. The owned process closes before the claim is released; questions expire when execution ends. |
| Automatic continuation | `Chat.deliver_results` scans persisted pending results. It waits for the original conversation and project claim to be free, then `Chat.submit` atomically claims delivery and inserts a stable worker-result message plus manager execution. It resumes that manager thread with actual objective/output/status/error/native references. No model polling while the worker runs. |
| User report | Normal manager output streams to the original conversation; `Chat._finish` persists the report and marks delivery delivered. The browser uses existing events/history polling. Failed reports retry after the persisted 30-second delay; recovered completed turns are not replayed. The manager can continue incomplete authorized work on the same task. |

The ordinary successful Auto/Execute flow has three top-level model turns: manager → worker → manager. There are no separate planning, verification, review or summarization turns. Native worker tool/inference steps remain Codex's responsibility. Other background work is the existing daily backup timer, not an agent/controller.

## Lifecycle and compatibility

The useful six-state lifecycle remains: `queued → running → completed/failed`; `waiting` represents deliberate pause, interruption, missing information or uncertain ownership, and `cancelled` represents explicit cancellation. No new task state, queue or recovery controller was added. Execution/delivery statuses describe individual attempts and reporting, not extra task supervision stages.

Retained original task `status` and `acceptance` columns for old-schema insert/display compatibility. Only `lifecycle` schedules work. Older schema migrations still add operational columns and preserve old terminal dispositions; unfinished historical tasks stay waiting until explicitly continued. Existing native threads, old clone references, execution output, approvals/operations/evidence and conversations retain their IDs and contents. The history endpoint reads old verification/integration tables only if present; a fresh database returns empty history arrays without creating these tables.

Archive results stay in their exact archived origin. A deliberately cleared origin discards later result delivery rather than leaking it into the replacement conversation. Clearing does not erase task/execution history. Shutdown/resume retains the same task/thread; detached owned processes retain exclusivity. Unknown launch identity is not blindly replayed.

An online production backup was created at `~/.local/share/linko-bot-manager/backups/codebase-cleanup-20261011-171441/before.sqlite3`. New initialization against its private `rehearsal.sqlite3` passed integrity/foreign-key checks and matched every original table, column and row hash. The rehearsal retained 11 projects, 51 tasks, 10 conversations, 64 messages, 105 executions and 109,981 events, plus all authentication/settings/private notes/historical evidence. No production database reset or task migration was required; its version remains 8.

## Validation

- `pytest -q`: **34 passed** in 3.22 seconds. Covers all modes/read-only enforcement, replay and repeated-goal reuse, current/future project claims, actual concurrent worker jobs/global capacity, native result flow, restart recovery, archived/busy/global/project/cleared origins, retry/backoff, duplicate completion/delivery, long output, uncertain/detached launches, interruption/pause, explicit catastrophic answers, backups/restoration, authentication and saved history. Fresh schema and retained historical evidence both have regression coverage.
- `ruff check ai_manager scripts tests`, JavaScript syntax, unique DOM IDs/reference checks and `git diff --check`: passed.
- Real installed Codex acceptance: **passed in 43.1 seconds** using a disposable calculator repository/database and normal background scheduler. One request produced one task/worker; Codex fixed addition, created exact `hello.txt` content, ran tests and made a local commit without approval. Two manager turns automatically acknowledged/reported in the original conversation. Repeated worker completion produced no extra report. No independent verification table/model or production assignment was involved.
- Chromium acceptance: passed login, chat, named archive/search, rename, three mode options and existing two model/effort routes. Desktop 1440×1000 and mobile 393×851 had no JavaScript errors or horizontal overflow; screenshots were inspected. The first attempt exposed nondeterministic test archive selection; selecting the named fixture archive fixed it.
- Existing Starlette/httpx test-client deprecation warning remains. No dependency migration was introduced just to silence it.

## Measured size

Counts compare baseline `20f008f` with this release, including essential new regressions. Text line counts are physical lines; minified CSS is also measured in bytes.

| Measure | Before | After |
| --- | ---: | ---: |
| Backend Python modules | 12 | 12 |
| Backend Python lines | 2,264 | 2,214 |
| Backend Python bytes | 108,455 | 104,190 |
| Application/test/script files | 24 | 23 |
| Application/test/script lines | 3,696 | 3,690 |
| Application/test/script bytes | 208,504 | 202,067 |
| Fresh application tables (excluding SQLite internal table) | 24 | 14 |
| Stylesheet bytes | 14,798 | 10,682 |
| Manager/worker instruction characters | 1,213 / 596 | 728 / 377 |

Documentation/snapshot text drops from 5,808 lines / 667,829 bytes to approximately 400 lines / 50 KB including this report. The major backend-module deletion happened in the preceding architectural release; this pass removes remaining dead schema/context/UI/script/documentation while adding essential preserved-workflow coverage. It does not trade code deletion for new orchestration components.

## Deliberately retained and remaining limits

Authentication/private hosting protect a powerful single-owner service. SQLite transactions, task/execution history, original conversation identity, file lock, project claims, native process/thread inspection, explicit questions and backups support correctness and useful recovery. Waiting/cancelled states and one global capacity setting express actual user/operational conditions. Memory reads retain owner-authored historical notes without restarting generated Markdown projections. Existing model/effort settings stay intact; no new model picker was added.

Frozen historical columns/tables remain physically present in production because deleting them would erase requested history. Their existence is not an active subsystem. General stream/transport/interrupt/restart code is retained because it handles real process/crash windows. No unused runtime package, extra planning call, task DAG or independent verifier remains. Native outcomes are worker-reported, and catastrophic detection remains agent-recognized. Paraphrased repeated objectives and incomplete-result interpretation still depend on the manager, and arbitrary external side effects cannot be exactly-once guaranteed. These limitations do not justify a new framework in this stage.

## Commit and deployment

Implementation/running application code commit: **`e21241b3b31c6c74abe05f326f28e6d17ef71cbb`**. The existing live `main` checkout was fast-forwarded to this tested release. Only `linko-bot-manager.service` was stopped/started, with no active execution or claim at cutover. It is active/running, PID **1806541** (previous **1728649**), started **2026-10-11 17:19:29 +04**, exit status **0**, using `/home/codex/projects/Linko-Bot-Manager` and its existing Python environment. This follow-up documentation receipt advances repository HEAD without changing the running application tree.

Both loopback and [private HTTPS](https://codex-server.tail5ebb9d.ts.net:8787/) returned **200** for the interface and `/api/auth-status`, and **401** for unauthenticated portfolio access. Served HTML/JavaScript/CSS bytes matched the committed files on both routes. Process working directory and restart timestamp confirm the new application was loaded. Application startup completed through scheduler initialization; ten subsequent idle scheduling intervals produced no queue failure or traceback. The real disposable end-to-end run separately demonstrated background scheduling and automatic manager continuation; no artificial production assignment was created.

After restart, SQLite integrity/foreign keys passed and **every original table, column, row and hash matched the predeployment snapshot**, including all 11 projects, 51 tasks, 10 conversations, 64 messages, 105 executions and 109,981 events, owner/sessions, settings, private notes and historical evidence. Schema remains **8**. Backup/rehearsal and private `before.json`/`after.json` receipts remain in the backup directory above. No project permission/scheduler setup, database reset, task replay, native-thread deletion, backup-timer change, remote push or unrelated service/listener change occurred.

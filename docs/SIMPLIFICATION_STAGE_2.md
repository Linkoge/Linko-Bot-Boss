# Simplification Stage 2: complete worker assignments

Date: 2026-10-11. Scope: delegation contract, unfinished-task reuse, and isolated validation. Stage 3 was not started. No production data was read or modified, task history reset, service restarted, or deployment performed. Pre-existing uncommitted chat-history, navigation, database, UI and documentation changes were preserved and excluded from Stage 2 commits.

## 1. Investigation and root causes

Stage 1 is documented in [SIMPLIFICATION_STAGE_1.md](SIMPLIFICATION_STAGE_1.md). Its live planning comparison produced two code tasks for a single implementation objective before and after the instruction simplification. Source inspection established these paths:

| Surface | Existing behavior and source |
| --- | --- |
| Conversation | `Chat.submit` / `Chat._run` supply the mode and project context. The dynamic-tool handler rejects mutating delegation before reaching the scheduler. Read-only document/approval lookups and administration snapshots remain available. |
| Auto | The interactive Codex model chooses whether to invoke `manager_action` using its message, native thread history, project context and SQLite bootstrap history. No deterministic intent classifier initiates delegation. |
| Execute | `Chat._run` calls `manager_action(plan_goal)` directly, passing the current message and preceding conversation as JSON when history exists. This preserves Stage 1's interpretation of “do it.” |
| Planning | `Scheduler.manager_action` calls `Planner.plan`, which starts a separate `structured_turn` using the unchanged planning model/effort route. Its input is the goal, project identity/objective/instructions, and up to 40 existing task records. |
| Task JSON | `PLAN_SCHEMA` originally allowed 1–20 task objects without requiring a reason for multiple tasks. Each object required description, acceptance, verification, priority, dependencies, kind and evidence specification. The planner model authored both task boundaries and dependency indexes. |
| Validation and persistence | `Planner.apply` validates evidence specifications, nonempty task fields, priority bounds and earlier dependency indexes. One transaction inserts the plan, tasks, dependency rows and events. SQLite task status defaults to `pending`. The backend did not manufacture additional task phases. |
| Scheduling | `Scheduler._tick` orders pending tasks by project priority, task priority and creation time. `Workers.readiness` and `Workers.start` check prerequisite completion, project state, effective policy, global capacity and active work in the project. Queuing did not require dependency rows. |
| Assignment | `Workers._run` prepares the existing isolated repository copy, persists process/thread/turn identity, and supplies one task's description, acceptance, verification, kind and evidence specification, plus project/global instructions and policies. The existing controlled capabilities already permit investigation, edits and registered tests in one turn. |
| Worker result | A completed worker turn moves its existing task to `verifying`, retaining its output and execution record. Interrupted turns become paused; unsuccessful turns become failed; uncertain transport outcomes become blocked. Reported success is not verified completion. |
| Acceptance | The existing `Verifier.verify` controller runs registered independent checks, inspects evidence, invokes read-only independent review, and enforces required integration. It updates the same task and records verification results/checkpoints. Neither controller nor reviewer creates development tasks. |
| Recovery | Worker retry/resume and verification retry operate on the original task and retain executions. Existing restricted configuration-blocker recovery remains unchanged. Failed workers are not automatically replaced with recovery tasks. |
| Interface | The Tasks tab renders persisted task status, blockers, prerequisites and readiness. Agents displays execution records, model/effort, workspace and process details. Chat receipts refresh actual task and worker state after dispatch attempts. No frontend task generator was found. |

The decomposition originated in the **model-authored plan enabled by its unconstrained multi-task contract**, not deterministic scheduling, SQLite, worker execution or verification. There was no distinction between one worker's development steps and separate deliverables in the schema. Earlier-index dependency validation checked validity, not whether one task actually needed another's output. The former “small actionable plan” instruction also did not establish one assignment as the default. Stage 1 removed that generic instruction but retained the same open-ended task array.

Repeated requests had a second cause: replay protection only recognized the same tool/request key. A new message normally has a new key, so the planner could recreate an unfinished objective. Supplying existing tasks as context alone did not provide a structured reuse outcome.

## 2. Architectural changes

Only `ai_manager/orchestration.py` changes production behavior:

- The existing manager tool now describes delegation as a complete objective for a worker that investigates, implements, tests and reports.
- The existing planning schema describes one complete assignment as the default. A new `decomposition_reason` records why multiple assignments are needed; `Planner.apply` rejects multiple tasks without a nonempty reason before inserting any records.
- Dependencies describe actual required outputs. Independent assignments use empty dependency lists and queue under existing capacity limits.
- A new `existing_task_id` provides a structured reuse outcome with an empty task list. The backend validates selected-project ownership and unfinished status before recording reuse.
- Exact repeated goals bypass the model call when their existing assignments are unfinished. A transaction-time recheck also prevents overlapping identical single-assignment planning calls from inserting duplicate tasks.
- `task.reused` events connect reuse receipts to existing tasks. `existing_result` reads both original and reuse events, preserving request-key replay protection for single-task reuse receipts.
- The unconditional planner result label `queued` was removed because it was inaccurate for reused failed/blocked work. Scheduler receipts continue returning actual persisted task status and worker state.

No task schema migration, new routing model, framework, provider, agent hierarchy or execution API was added. Existing tasks and plans remain readable; new validation does not retroactively reinterpret stored multi-task plans.

## 3. Planning retained, modified and bypassed

The separate planning call is **retained for new objectives**. It has specific value beyond decomposition: selecting the existing verification strategy, naming investigation/research/documentation deliverables, expressing acceptance, identifying required integration, and resolving Execute's contextual follow-ups into a standalone worker objective. Removing it without another source of that metadata would default investigation work to the code gate or leave required evidence files undefined. That would block legitimate work or require changes to verification outside Stage 2.

The call's output contract was narrowed to complete assignments and explicit exceptions; its developer instruction remains the same trust-boundary sentence. No manager/worker/reviewer personality or lengthy behavior prompt was introduced, and no saved/default model or reasoning setting was changed.

Planning is bypassed for request-key replays and exact repeated unfinished goals. Semantic repeats still use the existing planning model, which can select an existing task instead of creating work. No additional classification call or routing layer was introduced.

## 4. One request, one assignment

For an ordinary request:

1. Auto delegates according to model judgment; Execute delegates directly with relevant history.
2. The planner returns one objective including the necessary evidence/acceptance metadata.
3. One task is persisted before the delegation action returns. The scheduler attempts dispatch under current settings.
4. One coding worker receives that task and performs its normal investigation, implementation and development tests.
5. Worker completion moves the same task to `verifying`; independent acceptance can then complete or block that task.

The real Auto sorting request produced one assignment to inspect the existing ordering, implement newest-first sorting, run relevant checks and report results. It created no preliminary investigation, test or reporting task.

When scheduling is paused, the returned task is `pending` with no worker association. The manager receipt reports the actual task reference and scheduler blocker. Scheduling is not implicitly enabled. A worker execution that is merely queued is not reported by the backend as a confirmed started process/turn.

## 5. Genuine dependencies and concurrency

Multiple assignments remain supported for explicitly separate deliverables, necessary stages or operational constraints. The rationale is stored in the existing plan's JSON, rather than a new table. Dependencies continue referencing earlier zero-based task indexes, which become task IDs transactionally. Forward, self and cyclic indexes remain rejected.

Scheduler gating and capacity checks are unchanged: one active coding assignment per repository, with different repositories able to run concurrently under the existing global limit. `running`, `verifying` and `awaiting_approval` retain the project's active slot. A failed task releases that slot; unrelated pending work with no prerequisite edge can start.

The live explicit independent-deliverables request created two tasks and **zero** dependencies. The explicit contract/client stages created two tasks and **one** real prerequisite edge. Existing isolated-workspace and integration behavior is retained; this stage does not add artifact transfer or workspace coordination between assignments.

## 6. Duplicate tasks, failures and history

- The original unique request key still protects replay, including legacy plan records.
- Exact repeated goals are matched within the same project, ignoring surrounding whitespace. Pending, running, verifying, paused, failed, blocked and approval-waiting assignments are eligible for reuse; completed/cancelled assignments are not reused for a new request key.
- Contextual repeats such as a request to resume failed sorting work can return `existing_task_id`. Unknown IDs, other-project tasks, completed/cancelled tasks and mixed reuse/new-task output are rejected atomically.
- Reuse creates an audit receipt referencing the existing task, not another development task. It does not change task status, lease, blocker, revision, dependencies or worker output/history.
- A planning failure creates no partially applied task chain. An explicit later attempt can apply once under the original key; interrupted planning executions retain their existing blocked/error handling.
- Retrying a worker or independent verification continues using the existing explicit retry/resume mechanisms. A repeated failed objective does not authorize another worker launch.

An overlapping-identical-request regression confirms two planning calls can finish with one task and two audit receipts. The real-model resume check reused the failed task and left it failed, with no recovery worker or additional task.

## 7. Test results

Final full regression run: **122 passed** in 20.32 seconds. The existing FastAPI/Starlette test-client deprecation warning remains. Focused Stage 2, Stage 1, orchestration and execution-reliability run: **49 passed**. Stage 2 adds 20 targeted cases, including parameterized reuse/scope cases.

| Required coverage | Evidence |
| --- | --- |
| Simple implementation/investigation creates one task | New scheduler/delegation tests; real-model Auto implementation and Execute investigation each returned one task |
| Development testing and independent verification add no development task | New host-runtime test invokes worker tests, observes `verifying`, runs controller/review, and confirms one task/worker through completion; real coding fixture does the same |
| Independent requests have no artificial dependency; failure does not block unrelated work | New test queues two requests, observes per-project capacity, fails the first, and dispatches the second |
| Explicit complex work and genuine dependencies | New explicit-independent and contract/client tests; existing atomic/cycle tests; real-model requests returned 2/0 and 2/1 tasks/dependency edges respectively |
| Repeated requests avoid duplicate work | Exact-repeat tests preserve pending/paused/failed/blocked records and execution output; concurrent-repeat test; real-model failed-task reuse |
| “Do it” uses history | New Execute test passes the full antecedent through planning and persists its task receipt; existing Stage 1 Auto/bootstrap tests; live Auto follow-up creates one correct assignment |
| Conversation does not execute | Existing Stage 1 tests reject every mutating action; live Conversation request created zero tasks and zero workers |
| Existing records remain readable | New database-reopen test reads a legacy task/plan without the new fields; existing database/history regressions |
| Scheduler/verification remain functional | Full existing scheduler, worker, approval, integration, host isolation, retry and verification regressions, plus the real coding fixture |

`ruff check ai_manager scripts tests` and `git diff --check` passed. The initial nested-sandbox test run stalled and was interrupted; fixture subprocess/host-runtime checks completed outside that nested sandbox without changing application permissions.

### Real Codex coding fixture

`scripts/smoke_coding.py` now delegates a normal calculator fix through the real planner, checks the persisted pending receipt, repeats the request, and then runs the worker and independent verification. The exact edit approval is simulated only for that disposable fixture.

Observed result:

- **1 persisted task, 1 coding worker**, final task status **completed**.
- Exact approval consumed; worker invoked registered tests; independent verification passed.
- Original calculator repository remained unchanged; the worker edited its existing isolated copy.
- Execution history retained four finished records: planning, coding worker, verification controller and independent review. The repeated request added no planning execution or worker.
- Fixture task ID: `b80a970cc3f24ac8a3d8c99438d122c5`; worker thread `01a127dc-0802-7cf1-bd81-30f26fba3f1e`, turn `01a127dc-0893-7e00-9673-48d859485b05`.

### Real manager/planner cases

[model-delegation.json](stage2/model-delegation.json) contains synthetic, disposable-fixture observations. All six cases completed, with scheduler disabled throughout:

| Case | Persisted tasks | Dependency edges |
| --- | ---: | ---: |
| Ordinary implementation, Auto | 1 | 0 |
| Ordinary investigation, Execute | 1 | 0 |
| Explicit independent deliverables, Execute | 2 | 0 |
| Explicit required stages, Execute | 2 | 1 |
| “Do it” with preceding discussion, Auto | 1 | 0 |
| Implementation request, Conversation | 0 | 0 |

The additional failed-task resume check retained one task in `failed` state. Conversation's model attempted delegation, but the existing backend guard rejected it; its reply reported that execution was unavailable in Conversation mode. This is evidence of backend enforcement, not perfect model routing. Likewise, initial Auto commentary sometimes promises inspection before reporting the actual queued receipt. No conversation template or output rewriting was added to conceal this.

Reproduce without using production data:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check ai_manager scripts tests
.venv/bin/python scripts/smoke_coding.py
.venv/bin/python scripts/check_stage2_delegation.py --output /tmp/stage2-model-results.json
```

Live scripts require the existing Codex ChatGPT login and a host capable of running the unchanged isolated verification runner. They inherit existing model routes/defaults. Real-model results are individual samples, not a statistical guarantee.

## 8. Remaining limitations

- A new objective still uses a separate planning turn for verification metadata and contextual normalization. Stage 2 simplifies its task contract rather than replacing it with another system.
- The backend requires a nonempty multi-assignment rationale; whether it is substantively justified and whether a dependency truly requires output remain model judgments. No semantic classifier or hard-coded task-phase vocabulary was added.
- Deterministic duplicate detection covers exact goals within a project. Paraphrased or contextual reuse depends on model selection and the existing bounded task context. Different phrasings in concurrent requests can still produce equivalent tasks.
- Exact repeat of an entirely unfinished legacy multi-task plan returns that existing plan. Partially completed plans and new/revised requirements still require model judgment. Old artificial dependency chains are not rewritten, deleted or automatically recovered.
- Non-code acceptance still requires the existing deliverable files/evidence. Registered commands and permissions can block work; investigation is not exempted from applicable acceptance checks.
- Genuine dependency scheduling still uses the existing completion gate and integration/workspace behavior. No cross-assignment artifact-transfer mechanism was introduced.
- Native history, context bounds, model preambles and possible inappropriate attempted tool calls retain their Stage 1 limitations. Conversation mutations remain rejected in code.
- No automatic manager wake-up or full recovery workflow was implemented. Existing owner retry/reconciliation and narrowly scoped configuration recovery remain in place.

## 9. Modified files and commit reference

Implementation and test/evidence commit: **`4c012e3c9546fcb9fbbd2aaa0187f9ae264c6700`**, based on `3f6f07a`. This report is added in the following documentation commit; resolve its reference with `git log -1 --format=%H -- docs/SIMPLIFICATION_STAGE_2.md`.

- `ai_manager/orchestration.py`: complete-assignment schema/tool contract, explicit decomposition rationale, validated task reuse, exact/concurrent-repeat protection and reuse receipts.
- `tests/test_manager_stage2.py`: 20 targeted delegation, concurrency, reuse, persistence and verification cases.
- `tests/test_orchestration.py`: existing multi-task fixture now records its explicit separate-deliverable rationale.
- `scripts/smoke_coding.py`: real planning/repeat/worker/verification test with task count and execution-history checks.
- `scripts/check_stage2_delegation.py`: opt-in real-manager/planner fixture checks with scheduling disabled.
- `docs/stage2/model-delegation.json`: observed synthetic real-model results, including Conversation's rejected delegation attempt.
- `docs/SIMPLIFICATION_STAGE_2.md`: this audit and validation report.

No push, restart or deployment was performed. Pre-existing working-tree changes remain uncommitted by Stage 2.

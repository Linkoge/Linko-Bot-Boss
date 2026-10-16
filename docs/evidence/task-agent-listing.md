# Bounded Tasks and Agents listing result

Assignment: `4eaa5d1430d249f0ae86faaa86433c29`.

Root cause: `Tasks.list` selected every task and its full task metadata before
filtering in Python. The manager then sliced that complete list. The portfolio's
Agents query selected 20 finished executions **per agent/project**, followed by a
100-row cap that also applied to unfinished work. The deployed 20-event and
20-execution history pages bounded content within a record, not the number of
cards in the selected portfolio scope.

The shared `bounded_listing` SQL path now selects all unfinished matches and ten
recent terminal cards per UI list, after applying the selected project scope.
All Projects includes every repository and projectless manager executions.
Terminal ordering uses task update/execution finish time plus insertion position
for ties. Explicit older requests extend a timestamp/position boundary by ten;
refresh retains the requested range and admits new finishes. The boundary remains
usable if its task is continued. The UI resets both windows on scope change and
reload, rejects stale scope responses, and protects older requests from background
refreshes. Agents renders immediately when its tab opens. Task headlines use the
derived objective title, with project/state metadata below.

The manager's `list_tasks` uses the same path with the separately requested
five-terminal default, SQL query/status filtering and terminal offset pagination.
Its first page contains all unfinished matches; subsequent pages contain older
terminal summaries. Default task lists select compact fields, never full criteria,
gate logs, workspace/merge payloads or historical prompts. Targeted read actions
and history views still retrieve full objectives, criteria, final reports,
workspace/merge receipts, execution references and compressed content. There is
no deletion, destructive archival, new task state or new retention setting.
Legacy archive settings cannot hide unfinished cards; existing control guards
remain unchanged.

Validation uses permanent repository tests and disposable databases/servers:

- `tests/test_card_listing.py`: 26 terminal tasks/executions in each of two
  projects, older active/held/queued/running/retry work, projectless manager
  executions, exactly ten UI terminal rows, tied sort keys, older paging to
  exhaustion, refresh with new finishes, continued boundary records, cursor scope
  validation, five-terminal manager defaults, compact fields, SQL query/status
  filters, offset paging and complete task/result retrieval after compression.
- `tests/test_card_listing_browser.py`: real Chromium card counts and task
  headlines, all old unfinished work, independent older windows, interrupted
  response timing, refresh retention, rapid project switches, reload reset,
  Hold/Release/Cancel/Continue and full historical report retrieval.
- Existing execution UI assertions now require all 106 unfinished executions
  instead of the removed 100-row cap, and ten finished executions instead of 20.
  Existing retention browser tests still verify compressed messages, events,
  execution references and full output paging.

Commands completed successfully with the repository virtualenv interpreter:

- `python -m pytest -q -m smoke`: **45 passed**.
- `python -m pytest -q tests/test_core.py tests/test_execution_ui.py tests/test_card_listing.py tests/test_token_context.py tests/test_web.py tests/test_retention.py tests/test_card_listing_browser.py tests/test_retention_browser.py`:
  **74 passed**.
- Ruff on all changed Python implementation/test files and `git diff --check`:
  passed.

## Deployment recovery regression and continue follow-up

Follow-up assignment: `ffde2650990e4cfb8cf1ea017cf7578e`, based on `85acc4aaa7`.
The reported deployment failure is deterministic, not a queue/recovery timing
failure. Running only
`tests/test_recovery.py::test_five_queued_tasks_run_in_order_through_a_manager_interruption`
on that base failed in 0.85 seconds with `KeyError: 'origin_conversation'`.
Before that assertion, all five objectives ran in order, all five results were
delivered, and the worker/manager message counts passed. Compact `Tasks.list()`
cards deliberately omit `origin_conversation`; the test still expected a full
task row. The queue claim path reads full tasks directly and does not use the
bounded list, its terminal ordering, or the five-terminal manager window.
The original validation commands recorded above did not include recovery tests.

The test now retrieves each listed task with `Tasks.get()` before asserting its
original conversation. Every existing assertion remains, including exact queue
order, task/result/message counts and the eleven executions through interruption.
The compact-list contract remains covered by `tests/test_card_listing.py`.

The separate continue defect was in `Scheduler.manager_action`: the tool accepted
`details`, but forwarded only `objective` as added instructions to `Core.control`.
A details-only continuation therefore re-queued a terminal task with its original
objective. Queued/held continuations likewise lost those instructions. Continue
now joins distinct, nonblank `objective` and `details` text before calling the
existing append/control path. Original requirements, acceptance criteria, task
limits, queue placement and retained worker threads keep their existing behavior.
The manager prompt, tool field description and README document both input fields.

The permanent parametrized test
`test_manager_continue_instructions_reach_worker_input` covers done, failed,
queued and held tasks with details only, objective only, both distinct fields,
and identical text in both fields (16 cases). It runs workers, compares the
persisted execution input with the prompt actually received, and checks original
requirements, appended acceptance, replay idempotency and resumed native threads.
Before the fix, eight cases failed because details were absent; afterward all
16 pass. The focused recovery/context/listing suites pass (54 tests).

Final validation with the repository virtualenv interpreter, on unchanged
implementation/test files between full runs:

- Three consecutive `timeout 600s python -m pytest -q -p no:cacheprovider`
  invocations: **416 passed** each, in **232.49 s**, **233.84 s**, and **233.25 s**.
- `timeout 180s python -m pytest -q -m smoke`: **45 passed**, 371 deselected,
  in **5.15 s**, within the permanent 20-second smoke budget.
- Ruff on the changed Python files and `git diff --check`: passed.

The application owns merge and deployment; this assignment only changes and
commits its assigned workspace/branch.

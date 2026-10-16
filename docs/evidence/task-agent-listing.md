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

The application owns merge and deployment; this assignment only changes and
commits its assigned workspace/branch.

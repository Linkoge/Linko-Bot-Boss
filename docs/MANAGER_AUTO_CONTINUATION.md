# Automatic manager continuation

The manager now ends its turn immediately after delegating or continuing an assignment. It must not sleep or poll for the worker: keeping the original turn open blocked result delivery in the live acceptance test.

The existing scheduler checks the SQLite execution records once per second. Worker completion atomically saves the actual output, error, terminal status and `delivery='pending'`. After the worker releases its project claim and the originating conversation is idle, `Chat.deliver_results()` submits that result through the normal manager chat path. No model calls are added while the worker runs.

The input contains the original execution objective, current task status/blocker, actual worker output, execution status/error, native thread/turn IDs, workspace, available Git references, model, timing and usage. Output beyond 24,000 characters uses its final 24,000 characters with an explicit truncation flag; full output remains in the execution record. The manager resumes the saved conversation thread (or uses existing saved-history fallback), reasons about the result, and posts a natural completion/failure report. It retains the existing `manager_action` tool to continue genuinely incomplete authorized work. No independent verification or approval step is added.

## Persistence and isolation

- A transaction claims each pending result and creates its manager execution and chat messages together. The worker message uses a stable `result:<execution_id>` ID.
- Successful manager completion marks the result delivered. Repeated worker or manager finish handling cannot reopen it.
- Failed/interrupted manager deliveries return to pending and retry after a persisted 30-second delay. Failed attempts remain in history; the worker message is not duplicated.
- Startup uses the existing native-thread recovery to reconcile unfinished turns. A recovered completed manager report is marked delivered rather than replayed. Pending results are picked up by the scheduler; schema migration 8 also recovers old stranded `delivery='waiting'` records.
- Results resume their exact origin, including global and archived conversations, rather than being redirected to a project's newest chat. A deliberately cleared/deleted origin is discarded rather than inserted into its replacement. Assignments without an origin use their project chat.

Existing chat events and browser history polling expose the new report automatically. No manual user message, refresh, new event bus, agent framework, or supervisor is required.

## Validation

Run the regression suite and the real disposable-project acceptance test from the repository:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check ai_manager tests scripts/smoke_manager_continuation.py
PYTHONPATH=. .venv/bin/python scripts/smoke_manager_continuation.py
```

The acceptance script creates temporary project/data directories, asks the actual manager to create `hello.txt`, and runs the normal background scheduler. With exactly one user message, Codex created `Automatic continuation works.\n` and the manager automatically reported completion in the original conversation. The first successful run took 31.5 seconds. Replaying completion did not create another report. Temporary files are removed afterward; production service/data are untouched.

Regression coverage includes success/failure/interruption evidence, retries after restart, recovery of completed manager turns, repeated/concurrent delivery, busy and archived conversations, separate project/global origins, cleared origins, large output, and continuing an incomplete assignment through the existing tool.

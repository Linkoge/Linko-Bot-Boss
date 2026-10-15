# Permanent five-round policy evidence — 2026-10-15

Assignment: `e01954965b8347b7abbb6d1d9d5a92cf`.
Retained branch: `linko/assignment-e01954965b8347b7abbb6d1d9d5a92cf`.
Assigned initial base: `1e086d4168c2fc25bed7df7a342daa9370b4b6d3` (failed recording candidate).
Final policy-only parent: `f8f4d55`. After discovering whole-branch merge would inherit the failed
recording implementation, committed only policy changes and rebased this assignment's own branch
with `git rebase --onto f8f4d55 1e086d4`. The failed commit and held assignment branch/workspace
remain untouched; no shared checkout or assignment metadata was edited. This avoids publishing the
failed candidate through the policy assignment. This report's base refers to the originally assigned
base, not a claim that runtime ownership metadata has changed.
Status: **PASSED isolated candidate; activation pending normal application-managed merge/deploy.**
This worker did not deploy, push, restart services, modify a shared runtime database/checkout,
resume any held assignment, or create parallel workers/reviewers. The governing injected budget
remained **three**, with **zero verification repair rounds used** (no failed verification checks).
New application code does not authorize this execution to use five.

## Inspected authority and implementation

Read repository spec, delivery decision/state, delivery/autonomy/recovery/security contracts,
retained model-settings report, task controls, orchestration, workers, verification plan,
native thread instruction handling, and existing counter/recovery/prompt regressions.
No AGENTS.md exists in the assignment workspace/parent tree. No external network work was needed.

`ai_manager/assignment_workflow.py:REPAIR_LIMIT` is the enforcement/default source. It now sets five
for continuation charging, active eligibility, transactional wake claims, exhaustion holds and final
wake reporting. No schema migration or state rewrite was introduced. Fifth queued workers may finish;
sixth continuations reject atomically. Counters above five are retained and remain exhausted.
`verification.plan()` and worker remaining-budget generation use that constant. Worker developer
instructions, manager developer instructions, report plan, and current docs now all specify five.
Both instruction sources explicitly preserve evidence/metadata discrepancies and the lower remaining
budget. Retry/reset flags keep their old meaning: backoff/state changes never reset usage.

Focused regressions cover UI and manager continuations, revision/call replay, SQLite reopen,
holds, existing counts 2/3/4/5/6, retained evidence/context/thread, fifth queued success,
fifth wake report deduplication, native network/transport recovery without counter charges,
and changed prompts at usage 1/4/5 with remaining 4/1/0. Passing tests use disposable fixture
runtime data and fake providers; they do not establish live activation or exercise actual model calls.

## Held model/recording evidence

Assignment `4a985c35b51345ada354cd666acbf80e` and execution
`d1e9eec6a6304ffaa0ebf2fc8df415d6` remain held and unchanged by this work.
The supplied context and original `docs/MODEL_SETTINGS_REDESIGN_REPORT.md` in the held assignment establish
**three actual rounds used**, despite supplied machine `repair_rounds=2`. This discrepancy was
preserved, not independently remeasured or overwritten. That held report remains unchanged;
retrieve it with read_document using the held task_id. The policy-only tree retains the earlier
model report from `f8f4d55`, rather than copying or overwriting the held failure evidence.
Failed candidate `1e086d4` and owner-reported live `f8f4d55` are preserved; the worker has not
independently verified live commit/deployment. The originally assigned base was the failed candidate; the final policy branch excludes it.
A policy PASSED result verifies this change only and does not resolve the retained recording assertion
`times['ui-recording'] >= times['recorder-start-event']` or mark the other assignment passed.

The [supported activation/continuation procedure](AUTO_DEPLOY.md#five-round-activation-and-retained-assignments-2026-10-15)
is authoritative. Reconciliation through a real supported continuation is deferred until activation and
explicit owner authorization, never a worker database patch. An unchanged failed task with machine two
charges 2 → 3 on that real continuation, while historical evidence remains three actual used/two remaining.
There is no historical reconciliation endpoint. Paused/waiting resume may not charge at all; the evidence
ceiling must still be carried in appended context and honored. This task has not invoked that lifecycle.
Only a later verified recording candidate can make the failed assignment eligible for normal deployment.
No deployment success receipt was inspected or generated here.

## Full check commands and output

Commands ran sequentially from this isolated workspace. For Python commands below:
`PATH=/home/codex/projects/Linko-Bot-Manager/.venv/bin:$PATH` and
`AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,testserver` select existing tools and fixture hosts.
No dependencies changed; no build step applies to this Python policy/prompt/docs change.
The full backend suite is left to the normal deploy script. Initial smoke ran once; passing checks were
only revisited after new assertions and, subsequently, isolation onto the safe baseline.
The baseline change requires fresh smoke and focused validation of the final deployable tree.
Raw logs also remain at `.data/repair-policy-evidence/` in this retained workspace.

### Smoke (once)

```bash
python -m pytest -q -m smoke
```

Exit 0:

```text
.....................................                                    [100%]
=============================== warnings summary ===============================
../../../../../projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
37 passed, 290 deselected, 1 warning in 3.94s
```

### Focused policy, lifecycle, context and recovery regressions

```bash
python -m pytest -q tests/test_task_rules.py tests/test_assignment_workflow.py tests/test_token_context.py tests/test_recovery.py::test_network_recovery_same_task_thread_history_no_waiting_inference tests/test_recovery.py::test_continue_replayed_with_new_call_id_does_not_restart_finished_assignment
```

Exit 0:

```text
.......................................                                  [100%]
39 passed in 2.18s
```

### Added explicit cumulative retry/prompt assertions; affected tests only

```bash
python -m pytest -q tests/test_recovery.py::test_network_recovery_same_task_thread_history_no_waiting_inference tests/test_token_context.py::test_worker_continuation_keeps_thread_and_only_updates_changed_context
```

Exit 0:

```text
......                                                                   [100%]
6 passed in 1.02s
```

### Initial lint

```bash
ruff check ai_manager/assignment_workflow.py ai_manager/verification.py ai_manager/workers.py ai_manager/codex.py tests/test_task_rules.py tests/test_token_context.py tests/test_recovery.py
```

Exit 0:

```text
All checks passed!
```

### Added conservative evidence-budget instruction assertions; affected test only

```bash
python -m pytest -q tests/test_task_rules.py::test_worker_contract_one_check_pass_five_repairs_and_destructive_confirmation
```

Exit 0:

```text
.                                                                        [100%]
1 passed in 0.28s
```

### Lint after instruction assertions

```bash
ruff check ai_manager/assignment_workflow.py ai_manager/verification.py ai_manager/workers.py ai_manager/codex.py tests/test_task_rules.py tests/test_token_context.py tests/test_recovery.py
```

Exit 0:

```text
All checks passed!
```

`git diff --check`: exit 0, no output. No failed verification check or repair iteration occurred.

## Final validation after isolating policy from the failed recording base

Rebased only this assignment's committed policy delta onto `f8f4d55`; no conflict or repair.
This changes the deployable tree, so smoke and focused checks were freshly run once on that tree.
Final raw outputs (all checks exit 0):

```bash
python -m pytest -q -m smoke
```

```text
.....................................                                    [100%]
=============================== warnings summary ===============================
../../../../../projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
37 passed, 291 deselected, 1 warning in 2.95s
```

```bash
python -m pytest -q tests/test_task_rules.py tests/test_assignment_workflow.py tests/test_token_context.py tests/test_recovery.py::test_network_recovery_same_task_thread_history_no_waiting_inference tests/test_recovery.py::test_continue_replayed_with_new_call_id_does_not_restart_finished_assignment
```

```text
.........................................                                [100%]
41 passed in 2.30s
```

```bash
ruff check ai_manager/assignment_workflow.py ai_manager/verification.py ai_manager/workers.py ai_manager/codex.py tests/test_task_rules.py tests/test_token_context.py tests/test_recovery.py
```

```text
All checks passed!
```

Ancestry assertions (`git merge-base --is-ancestor`, `git rev-parse HEAD^`, and
`git rev-parse linko/assignment-4a985c35b51345ada354cd666acbf80e`):

```text
Failed 1e086d4 ancestor of policy HEAD: false (expected exit 1)
Policy parent: f8f4d55d264dff9554fccb933b6c416aae5ba114
Held assignment branch: 1e086d4168c2fc25bed7df7a342daa9370b4b6d3
```

`git diff --check`: exit 0, no output. Final diff against `f8f4d55` contains only
policy implementation, policy/counter tests and policy/state/evidence docs; no recording/model source changes.

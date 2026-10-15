# Serialized integration update — 2026-10-15

**PASSED combined candidate; activation pending normal application-managed merge/deploy.**
In retained assignment `e01954965b8347b7abbb6d1d9d5a92cf`, merged exact source
`37898cd556db919d24c184bfa9d1707d7cb9d1f8` into policy candidate `a0f9694` using
`git merge --no-ff --no-edit 37898cd556db919d24c184bfa9d1707d7cb9d1f8`.
Merge commit: `24fbe8e`; no conflicts. Both source histories remain intact. The merge brings
`1e086d4` back as historical ancestry, now superseded by the source's current owner-revised
acceptance, rather than treating its old failed status as a passing result. All historical evidence
below and in [the source report](MODEL_SETTINGS_REDESIGN_REPORT.md) remains preserved.

Source inspection: `37898cd` changes only the optional diagnostic and its docs relative to
`e13b3b5`; that earlier commit corrects diagnostic event instrumentation without application changes.
The source report records the owner's explicit removal of the optional startup probe as an acceptance
gate and deletion of the hanging extra mock case. The diagnostic is not registered as a smoke/deploy
test. Integration honors that acceptance scope; it does not rerun the optional diagnostic, claim the
deleted case passed, or remove historical failures. Source records actual **five rounds used** despite
stale machine four, following the earlier **three actual/two machine** discrepancy. Neither value was
invented, reset, migrated or edited by this integration worker. No held source workspace was edited
or resumed. No independent reviewer, parallel worker, shared runtime database/checkout write,
service restart, deployment or push occurred.

The integration prompt reports application limit five, machine usage one/remaining four, while its
preserved objective says this worker must retain the earlier governing three-round ceiling. This
worker conservatively retains that stricter ceiling and the reported one already consumed: at most
two additional repair rounds; no new failed check or repair round occurred in this integration.
No self-extension follows from changed policy code. Application counters count continuations, not
individual test/fix cycles; actual evidence remains authoritative when higher.

Sequential review of the clean merge: policy changes do not overlap model/recording runtime code;
MASTER_SPEC merged both five-round policy and source UX requirements. Checked favorite expected
revision validation and shared intent ordering, synchronous close/focus, recording permission/start
handlers and late-stream release, source acceptance docs and probe deletion. Combined validation
uses local disposable fixture databases/projects/ports, fake model/STT providers and Chromium fake
media input. It does not establish physical device behavior, real model inference or live activation.

Activation remains the application's normal finished-PASSED merge/deploy flow. Only its successful
deployment result can establish activation. No extra source continuation is needed merely to activate
this combined candidate; any future authorized unfinished work must retain the same assignment and
cumulative/evidence budget through supported controls, never direct counter writes or replacement tasks.

## Integration verification evidence

Raw outputs/screenshots/JSON remain in `.data/policy-integration-evidence/` in this retained workspace.
Full commands and stdout/stderr are appended below. Prior policy-only report follows as historical
pre-integration evidence; statements there excluding `1e086d4` describe that earlier isolated tree.

### Combined candidate results (all passed)

Smoke **37 passed, 292 deselected** (once); focused backend **114 passed** (once), lint,
JavaScript syntax, favorite recall browser, recording browser, model/mobile/scroll browser and
chat browser smoke all passed. No repair loop was needed. The full backend suite remains the deploy
script's responsibility. Physical Android/iOS and real provider inference are not claimed.

Commands ran in this retained workspace with
`PATH=/home/codex/projects/Linko-Bot-Manager/.venv/bin:$PATH` and
`AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,testserver`. Each command below exited 0;
stdout/stderr follows verbatim. The only warning is the preexisting Starlette/httpx deprecation;
the two empty-policy rejection logs are intentional passing checks.

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
37 passed, 292 deselected, 1 warning in 2.91s
```

```bash
python -m pytest -q tests/test_task_rules.py tests/test_assignment_workflow.py tests/test_token_context.py tests/test_recovery.py::test_network_recovery_same_task_thread_history_no_waiting_inference tests/test_recovery.py::test_continue_replayed_with_new_call_id_does_not_restart_finished_assignment tests/test_model_settings.py tests/test_claude_provider.py tests/test_audio.py tests/test_chat_queue.py
```

```text
........................................................................ [ 63%]
..........................................                               [100%]
=============================== warnings summary ===============================
../../../../../projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
114 passed, 1 warning in 39.18s
```

```bash
ruff check ai_manager/assignment_workflow.py ai_manager/verification.py ai_manager/workers.py ai_manager/codex.py ai_manager/model_settings.py ai_manager/web.py tests/test_task_rules.py tests/test_token_context.py tests/test_recovery.py tests/test_model_settings.py scripts/browser_audio.py scripts/browser_model_recall.py scripts/browser_model_scroll.py scripts/browser_smoke.py scripts/probe_recording_start.py
```

```text
All checks passed!
```

```bash
LINKO_RECALL_EVIDENCE="$PWD/.data/policy-integration-evidence/recall" python scripts/browser_model_recall.py
```

```text
/home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
Immediate recall close/focus, duplicate suppression, fast Send ordering, reopen races, stale/unavailable/network reconciliation and draft preservation passed
```

```bash
LINKO_AUDIO_EVIDENCE="$PWD/.data/policy-integration-evidence/audio" python scripts/browser_audio.py
```

```text
{"result": "passed", "real_capture": true, "provider": "fixture only", "uploads": 28, "chat_submissions": 6, "viewports": [1440, 393, 320]}
```

```bash
LINKO_MODEL_EVIDENCE="$PWD/.data/policy-integration-evidence/models" python scripts/browser_model_scroll.py
```

```text
/home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
action.rejected path=/api/model-settings reason=Allow at least one supported model and level combination
action.rejected path=/api/model-settings reason=Allow at least one supported model and level combination
Settings persistence/policy/mobile/keyboard and manual scroll during sends/streams/polling passed
```

```bash
LINKO_CHAT_EVIDENCE="$PWD/.data/policy-integration-evidence/chat" python scripts/browser_smoke.py
```

```text
/home/codex/projects/Linko-Bot-Manager/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
Browser passed: login, chat, selection across refresh/streaming, copy all 425 messages, copy five, clipboard fallback, scroll preservation, archive/search, rename, model roles, recovery reasons/timing, desktop/mobile/narrow controls without JS errors or overflow.
```

JavaScript syntax commands each exited 0, no output:

```bash
node --check ai_manager/static/app.js
node --check ai_manager/static/audio.js
node --check ai_manager/static/model-settings.js
```

Exact ancestry/tree preservation checks (expected exit 0, no diff):

```text
$ git merge-base --is-ancestor a0f9694 HEAD
exit 0

$ git merge-base --is-ancestor 37898cd556db919d24c184bfa9d1707d7cb9d1f8 HEAD
exit 0

$ git diff --exit-code a0f9694 HEAD -- ai_manager/assignment_workflow.py ai_manager/verification.py ai_manager/workers.py ai_manager/codex.py tests/test_task_rules.py tests/test_recovery.py tests/test_token_context.py
exit 0

$ git diff --exit-code 37898cd556db919d24c184bfa9d1707d7cb9d1f8 HEAD -- ai_manager/model_settings.py ai_manager/web.py ai_manager/static/app.js ai_manager/static/audio.js ai_manager/static/model-settings.js docs/AUDIO.md docs/MODEL_SETTINGS.md docs/MODEL_SETTINGS_REDESIGN_REPORT.md scripts/browser_audio.py scripts/browser_model_recall.py scripts/browser_model_scroll.py scripts/browser_smoke.py scripts/probe_recording_start.py tests/test_model_settings.py
exit 0

$ git rev-parse linko/assignment-4a985c35b51345ada354cd666acbf80e
37898cd556db919d24c184bfa9d1707d7cb9d1f8
```

`git diff --check`: exit 0, no output. Integration changes after the clean merge are documentation
only. The exact pinned model/recording implementation and its evidence report remain unchanged;
five-round policy implementation/tests remain identical to `a0f9694`.
Screenshots and detailed browser results remain under the retained `audio/`, `models/`, `recall/`
and `chat/` evidence subdirectories. Test databases/servers were disposable and cleaned up by fixtures.


---

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

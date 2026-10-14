# Risk-tiered worker verification (Option B)

Owner-approved 2026-10-14. Updated with the owner-approved execution reductions; Risky retains relevant acceptance and sensitive review.
Code: `ai_manager/verification.py`, delegate/continue in `ai_manager/orchestration.py`, batching in
`ai_manager/tasks.py`, worker prompt and post-checks in `ai_manager/workers.py`.

## Tiers (approved reductions, 2026-10-14)

| Tier | Worker checks | Release preparation |
|---|---|---|
| Small | Smoke, changed-behavior targeted tests, relevant build/lint | Smoke, changed test files, relevant lint/syntax and selected existing behavior checks; no mandatory full backend or full browser suite |
| Normal | Smoke, targeted tests while iterating, relevant build/lint; no end-of-task full backend | Full backend once plus smoke, relevant build/lint and existing browser checks for changed behavior |
| Risky | Normal worker checks plus relevant acceptance; independent review only for sensitive actual changes | Normal release checks plus existing relevant migration/dependency rehearsal |

Correct substantive findings and failures, then rerun affected checks after fixes. Do not repeat successful unrelated
suites. No additional validation stage, periodic checks, AI planning/routing call, reviewer layer or approval step.
Worker replies remain at most 12 short lines; full native commands/outputs and `task.verification` stay in the execution
record. Completed work follows the existing standing Auto-deploy setting and normal manager handoff.

## Deterministic classification and combined releases

The existing `select(change_paths, objective, acceptance, requested, integration, deployment, previous)` classifier
and its broad risky path/text floors are unchanged. DB/auth/deploy/worker/manager/entry-point/configuration code,
dependency/infra manifests, risky request text, integration and deployment-intent assignments stay Risky. Docs/tests
are not risky by path. Small requires 1–3 concrete UI/text/test files, at most one script/test; other ordinary scopes
are Normal. Manager requests and continuations can raise the floor and never lower it. Model routing is unchanged.

Post-completion classifies actual changes against `workspace_base`, including untracked files, and records assigned,
actual/effective tiers, escalation and undeclared paths. Escalations require same-assignment follow-up. Preparation
classifies the complete live-base-to-candidate diff and takes the highest tier across every included assignment
policy and actual diff. Unknown/pre-tier included assignments conservatively retain a Risky floor. Integration's
existing Risky floor remains. Declared Small work cannot conceal an actual risky file.

## Sensitive independent review

Tier and review eligibility are separate. Review reasons come from actual runtime paths and changed diff lines,
plus sensitive requested behavior: auth/security, database migrations/schema, deployment/rollback/backup and worker
concurrency/recovery. Ordinary manager/backend/provider/chat filenames alone no longer purchase a review. For
DB/worker/task/scheduler/orchestration/assignment paths, changed migration/concurrency/recovery indicators select
review rather than the broad filename alone. Docs/tests alone do not select a path review. The existing cheapest
permitted read-only process, bounded input/time and interruption behavior remain. Findings remain substantive;
unavailability never changes worker completion, but required unavailable/failed review prevents automatic deployment.
The event records `review_required` and reasons. Old events without that field retain their prior Risky review rule.

## Selected release checks and exact evidence

`scripts/prepare_deployment.py` uses `release_policy` from `ai_manager/verification.py`, within the existing preparation
gate. Smoke runs for every release. Normal/Risky run the full backend exactly once there; Small-only omits it.
Changed Small test files (including root test files) run directly; changed test helpers select importing tests instead of attempting to collect the helper; runtime UI behavior uses existing selected browser acceptance. Python lint
and JS syntax checks cover changed surviving files, resolved against the candidate rather than the manager checkout.

`BROWSER_FEATURES` maps feature modules to existing consumers: audio/attachments → composer/audio;
registry/projects → project UI/stash; memory, models, usage, markdown and To-Do → their checks; deployment/publication
→ deployment/auto-deploy; verification → worker delivery, controls, deployment/auto-deploy; tasks/workers/assignment/
orchestration/native transport → delivery, controls, parallel/To-Do/model/auto-deploy consumers as applicable.
Retained `.data` reports/audits are not runtime browser coverage or lint inputs. Changed browser scripts select themselves. Docs/tests alone select no browser. Shared HTTP/auth/DB/config or unknown
runtime modules conservatively select all consumers, with a per-script reason. Small shared UI changes use existing
representative composer, project UI and smoke acceptance instead of a mandatory full browser suite, alongside worker
changed-behavior checks. Tests remain available; no useful coverage is removed.

The manifest and hashed validation log record tier, actual paths, selected browsers/coverage and skipped-check reasons.
The new automatic attestation says `release_checks: passed`, replacing mandatory `full_suite: passed`. Dispatch and
the independent helper require the exact new attestation and policy version; immutable historical full-suite
attestations remain supported. Commit/scope/base/assets/bundle/log/index binding, failed-check rejection, idle/drain,
backups, post-activation health, rollback and ON/OFF/holds remain intact.

## Batching

Batch related Small requests into one objective. Alternatively, `delegate(batch=true)` appends to the newest queued,
never-started Small assignment from the same conversation. It applies only without parallel permission/dependencies.
It also applies only if the combined paths/text still classify Small and fit assignment limits. The target keeps its
route, thread and workspace identity. A `task.batched` event and `task.reused` request key make replay idempotent.
Otherwise a separate assignment is created.

## Existing work

Assignments created before this feature have no tier record. Their prompts, execution policy, continuations and
completion are unchanged: no tier block, no post-check and no review. `WORKER_INSTRUCTIONS` are unchanged.

## Smoke suite

`python -m pytest -q -m smoke` runs 28 core-path tests (44 cases): auth/CSRF, chat receipts/queue/clear, delegation →
native worker → delivery, modes, restart/recovery, controls, parallel admission, deployment activation/rollback/idle,
deployment intent, model routing, worker context, follow-up, To-Do handoff, provider routing and tier selection.
`tests/conftest.py` registers the marker. Changing `pyproject.toml` would need dependency-level deployment review.
It fails a smoke run that exceeds 20 s. Measured 2026-10-14 on commit e04548b: 3.0–3.6 s, versus 64.6 s for the
355-test backend suite. On that commit all 19 browser scripts (247 s, `PYTHONPATH=.`), Ruff and `node --check` also passed.
`tests/test_verification.py` checks size (20–30) and core-area coverage.

## Rollback on post-deploy health failure

The deployment helper already rolls back automatically when post-activation health fails. See
[deployment helper](DEPLOYMENT_HELPER.md). `tests/test_rollback_rehearsal.py` rehearses this in a throwaway copy. It uses
a real Git checkout, a pinned bundle/manifest, snapshot backups, HTTP release/scheduler/auth/asset probes and SQLite
preservation checks. `systemctl` is simulated by a local server process, so the live service is never touched. An
unhealthy candidate ends `rolled_back`: detached base checkout, forward `main` preserved, base assets served, database
untouched, one report. A healthy control ends `succeeded`. `LINKO_REAL_HEALTH_CLOCK=1` keeps the real 45 s deadline.
It passed in 45.6 s on 2026-10-14.

## Historical evidence before the approved reductions

Dated sections below retain their original checks; the current policy above supersedes mandatory full-suite/review wording.

## Documentation follow-up evidence — 2026-10-14

Implementation remains `e04548b53ea56a75b64481bcb06d42ac396c0771`; its final validation was recorded in
`642511a0878e9df7a6937bb6af4dfdd65adacd80`. README, DECISIONS and STATE were brought into agreement
with this contract in the same retained assignment branch. The fresh command
`.venv/bin/python -m pytest -q -m smoke` passed all 44 cases (311 deselected) in 3.08 s, with one upstream
Starlette/httpx deprecation warning. Full backend/browser and real-clock rollback results above are retained
implementation evidence; documentation edits do not repeat those expensive suites. No application code changed
in this follow-up. Nothing was prepared, deployed, pushed or restarted; no production data was accessed.

## Serialized release candidate — 2026-10-14

Integration uses only the pinned source `52ee9e7a9e8f686b8ab0cf63cf4e49c01149bdbc` in its original
assignment workspace/branch. That source is already HEAD; explicit `git merge --ff-only` reports up to date.
Live commit `c36b163216cd6c43ae75f40d8dfa1a7e736bedcb` is an ancestor, confirmed by live health and
succeeded receipt `02220171270ebe62d05f81f8fb71c0da.json`. Relative to that base there are no changes to
dependencies, database schema, deployment helper or served static assets. Existing live functionality remains
in the candidate; changes are confined to Option B worker/manager verification/batching, tests and docs.

The owner corrected backend scope to `ai_manager/` and `scripts/` on this same assignment.
The attachment regression is fixed: selected context is deduplicated by equality, allowing dictionaries
without hashing or changing metadata. First-seen order is retained across old/new attachment references;
existing constraints deduplicate and replay remains idempotent. The retained regression, tier/batching
and attachment checks pass together (15 cases in 2.99 s). No partial auto-deploy work is included.

Release ID: `option-b-scoped-v3-20261014`. The final committed candidate is validated once through the
existing fixed preparation CLI with `--task-id c3aebb4c76db45de81ccd8567c2ac259`, workspace-local runtime
and evidence. The scoped evidence database contains only this assignment's task/project/context rows
copied read-only from runtime, providing the authoritative assignment hash. The actual manager integration
reservation serializes this turn; the preparation lock/reservation affects only local evidence storage.
Full backend and all 19 browser scripts run once; `LINKO_REAL_HEALTH_CLOCK=1` exercises the real health
failure/rollback deadline. Browser screenshot destinations are redirected into workspace evidence storage;
application assertions and the fixed CLI validation checks are unchanged. Smoke runs separately.

Durable report, exact candidate, command outputs, timings, hashes and manager delivery facts:
`.data/option-b-integration/REPORT.md` and `manager-summary.json` in the retained workspace.
Immutable release path:
`.data/option-b-integration/runtime/deployment-helper/releases/option-b-scoped-v3-20261014/`.
Only a complete manifest with matching bundle/validation hashes establishes preparation. Manager/operator
publication of those exact bytes into the shared release store is a separate step after integration ends.
No production data writes, shared checkout edits, service restarts, push or deployment are performed.

**Latest owner resume supersedes the earlier revocation.** Recorded intent
`c92b72ed0955ee84482c37422ef677eb` binds this assignment for manager-controlled activation. The worker
prepares only; no deployment or push. Manager review requires current-base verification, immutable
assignment-bound evidence and subsequent actual succeeded helper receipt. Preparation alone is not deployment.

Preparation attempt v1 was retained without a manifest after its full backend check timed out at 180 s.
A focused reproduction also failed with the long workspace TMPDIR (Claude host transport closed); the same
test passed with a short private per-assignment temporary directory. The interrupted full run cannot
establish complete-suite success, so one corrected full attempt is necessary. Completed targeted and smoke
checks are reused; failed v1 evidence is retained. Candidate application code is unchanged by this correction.

Attempt v2 passed all 356 backend cases, Ruff, six JS syntax checks and the model-settings browser script.
Its screenshot wrapper then missed the scripts import path, causing composer import failure before assertions.
The wrapper is corrected outside repository source. Fresh v3 preparation reuses those succeeded checks with
source/log hashes and reruns only the remaining browser checks. Relative to the v2 tested candidate, only
STATE/verification documentation changes; all application, test, build and browser-source bytes match.
The failed v2 release log remains immutable; full backend validation is not repeated.

## Close lifecycle correction

The owner authorized deploy/manager source correction after v3 publication/binding blockers. The supported
import command and V2 binding strategy are documented in DEPLOYMENT_HELPER.md. No v3 evidence or old intent
is rewritten. New corrected release target: option-b-lifecycle-v5-20261014. Its combined evidence reuses only
unchanged baseline checks and adds affected-path regressions, smoke, relevant browser checks and one scoped
read-only diff review. Current exact results/commit/hashes and post-cleanup publication command are in the
retained report; no blind full-suite repetition or worker activation.

## Optional automatic deployment

With per-project Auto-deploy verified work ON, the worker post-check also records its clean
commit. The deterministic pipeline requires the current tier and, for sensitive changes, the completed
nonblocking cheap review before release checks. Missing, failed or escalated evidence does not
auto-deploy. The tier-selected preparation gate binds the exact
candidate before preparation. Explicit holds/OFF and changed scope are retained; unrelated chat
needs no renewed approval. See [AUTO_DEPLOY.md](AUTO_DEPLOY.md).

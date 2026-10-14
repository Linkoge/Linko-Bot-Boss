# Task and deploy verification — owner-approved simplification, 2026-10-15

This policy supersedes Option B's Small/Normal/Risky check tiers and independent AI review.
The approved owner discussion increased repair rounds to three, made deployment automatic under the
standing setting, allowed secrets in authorized prompts and retained destructive-action confirmation.
No new reviewer, validation stage, planning/routing call or recurring AI check is added.

## Two check levels

| Level | Checks |
|---|---|
| Normal task | Changed-behavior targeted tests, fast smoke including startup and `/health`, relevant build/lint, one self-check in the worker turn |
| Deploy | Full backend once against the exact combined committed candidate at existing release preparation, smoke, relevant build/lint and existing browser checks for changed behavior; existing schema/dependency rehearsal when needed |

Every retained, pre-tier and new assignment receives the Normal task prompt. Sensitive/backend filenames,
request text and integration do not purchase extra worker checks, independent reviews or escalation retries.
Legacy classifier/review utilities and their test coverage remain available but do not schedule a review;
the classifier is used only to bound compatible small-request batching. Legacy tier arguments are accepted
without raising task checks. Model routing uses the same existing setting/catalog machinery, with no extra call.
Small documentation/UI releases now also run backend once at preparation, replacing the earlier exemption.

## Bounded repair and sequential execution

A failed task may continue in the same workspace/thread up to **three repair executions total**. The existing
assignment workflow setting stores `repair_rounds`; manager and UI continuations charge it atomically after
replay/revision guards. State changes, explicit retry flags, owner holds and process restarts do not reset it.
Paused/transport resume is the same round and does not charge another repair. At exhaustion further repair
continuations are rejected, failed work becomes owner-wait, and remaining failures must be reported. Successful
third-round completion still proceeds to handoff. Workers receive used/remaining budget and rerun affected checks
after fixes. Native transport recovery keeps its existing outcome-reconciliation behavior; this changes no
recovery feature or usage-blocking implementation. The backend counts repair executions, not shell commands.

Successful worker completion closes the workflow for normal automatic handoff; ordinary result delivery still
reports established facts. This implicit completion permits a bounded correction on the same assignment; an
explicit completion/owner hold remains closed. Unfinished/failed results cannot delegate a replacement task to reset the budget. Healthy completed steps may
continue an already owner-authorized sequential chain through the existing manager path; holds remain authoritative. Failed work has bounded actionable follow-up on its existing assignment; at most three
idle manager wakes are counted separately in durable `followup_rounds`, preventing response-only loops. Old retry
backoff counts are not interpreted as new repair usage. No independent reviewer or escalation loop runs.
One global coding worker is admitted even with historical opt-in permissions or higher capacity settings.
Workspace/process ownership and recovery admission enforce this. Existing isolated sources, branches and serialized
integration reservations remain intact for supported reconciliation; no new parallel work or automatic merge.

## Integrity, automatic deployment and owner holds

Actual changed files, authorized scope, clean committed source and assignment evidence remain bound in
`task.verification`. Out-of-scope edits, diff errors, missing/changed commit or semantic assignment hash still
prevent automatic deployment. Tier/review findings from old records do not create a new review obligation.
The existing automatic pipeline fetches the exact source into a disposable clone and runs the existing preparation
gate once. Its hashed manifest/log records policy version 3, `tier: deploy`, `full_backend: true` and relevant browser
selection/coverage/skips. Shared runtime primitives retain justified existing consumers; useful tests are not deleted.
`release_checks: passed` attestation requires the exact execution/commit plus v3 Deploy/full-backend policy;
immutable v2 scoped and historical full-suite attestations remain supported. Failed checks create no ready manifest.

Current live ancestry, source quiescence, immutable package/index/asset/log hashes, idle/drain, database/history
backups, post-activation health, automatic rollback and ON/OFF/explicit holds are preserved. Preparation/publication
remain internal steps in the existing automatic pipeline, without per-release owner consent or manual AI review.
Workers never deploy, push, restart services or write shared runtime. Only the controlled manager/helper activation
with a durable succeeded receipt proves deployment. Ordinary edits need no approval; destructive project/data
deletion, force-push or catastrophic destruction requires owner confirmation. Authorized prompts may contain secrets.
No public hosting, authentication, session, CSRF or private-access behavior is relaxed.

Keep detailed execution/report evidence and update relevant authoritative docs. Brief/delivery-memory writes are
needed when meaningful established facts change, not as a mandatory stage for every task. Related queued small
requests may still batch while their combined scope is compatible and their workspace/thread identity is preserved.

## Retained assignment and delivery facts

Assignment `e684e6b33ca14b778ef7769dfc92a0fc` retains its earlier exact source/check evidence. The delayed multiline
review-selector finding is superseded by removal of automatic independent review; no obsolete keyword/context
repair is required or claimed. The previous source and positive/negative coverage remain in Git and the report.
The five-delivery backlog was stale: later durable succeeded receipts cover composer cleanup, controls,
microphone/file uploads, parallel/composer layout, and verification/handoff releases. This assignment's new
simplification is implemented separately and is **not deployed**. Full receipt IDs, hashes and historical commit
coverage are in the retained `.data/verification-reductions/REPORT.md` and evidence inventory.

Latest owner resume explicitly grants serialized reconciliation of external historical main
`d629701957337357b4915e6ca8607d053cdb799a` with retained `113507d` in this assignment workspace.
The completed source provenance remains retained; dynamic read-only checks found shared HEAD clean at d629701,
unchanged completed source thread and no active source writer. The candidate must include actual d629701 ancestry,
not a fabricated succeeded receipt for it. Latest controlled succeeded receipt still names addfcd09.
All branches remain; conflicts retain both simplified policy and usage-restriction removal. Normal preparation
uses actual live base and `--task-id e684e6b33ca14b778ef7769dfc92a0fc`, with immutable validation/package/index
hashes. Prepared/deployed status and exact candidate/checks are established only by the retained report and real
artifacts. Existing post-completion auto evidence and helper bootstrap guards still apply; worker never activates.
Cancelled `703f113e1b134a9d8a008307f8c4a3aa` remains cancelled; unrelated recovery implementation is deferred.

## Historical evidence before the approved reductions

Dated sections below retain their original checks; the current policy above supersedes incompatible tier/review wording.

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

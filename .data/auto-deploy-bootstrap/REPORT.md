# Serialized readiness audit — 2026-10-14

Outcome: exact registered release integrity and current-base compatibility VERIFIED; missing
risky diff review COMPLETED with findings; helper v3 NOT INSTALLED due to external-cgroup
requirement. Application activation is BLOCKED pending manager assessment and scope-review
compatibility. No application deployment was requested or performed. This report is evidence,
not an approval or succeeded activation receipt.

Assignment: 27b4bbb092704f739eb7b30b2bb4d308, retained branch
linko/assignment-27b4bbb092704f739eb7b30b2bb4d308. Source assignment:
44097fd1a52c4f9096fcd901bd04d344. Source execution:
8a6e0afe948d4f04922419121954070c. Existing recorded intent:
ba8ce92a3d406a98cdfc9fa81851c251. All production SQLite connections used mode=ro;
no Database initializer, migrations, settings/event writes or lifecycle edits were performed.
No source code edits, push, live checkout edits, service operations, new release or owner intent.
Only audit evidence and the authoritative documentation pointer were added in this retained branch.

## Read instructions and source evidence

Read docs/AUTO_DEPLOY.md, docs/DEPLOYMENT_HELPER.md, docs/VERIFICATION_TIERS.md,
the source workspace .data/auto-deploy-stage2/REPORT.md, scripts/install_deployment_helper_v3.py
and its v2 implementation. Read relevant native task/execution/policy and publication evidence,
live manager intent-review code and the exact registered manifest/index. Repository search
found no AGENTS.md in these assignment workspaces. Runtime skill guidance was inspected;
no attached-cloud environment_status tool was available and no network operation was needed.

## Immutable release integrity

Release: auto-deploy-standing-policy-v1-20261014.
Registered directory:
/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/auto-deploy-standing-policy-v1-20261014
Registration event: seq 201759, kind release.published, phase registered, exact candidate/hash pins.
Candidate: 0223c3493a6edf9b54b280fa5993fb6404e2d8ca.
Live/base: bee8b088fa0eb2c20d963b95f109cbf3a87caaeb.
Manifest SHA256: 5c31263dea6bfd2c19d3a1b9151c20db76a5ed33169467d7ad64324825fc39cd.
Index SHA256: 8a8c313e9db83c2817297556bd6b73b3d6a45073a805e63172dd3929e57898db.
Bundle SHA256: c4ca81d6053b2f501f55999d462776299ca48e380329068bf3ac8d85860b6ab0.
Validation SHA256: 9bc425700a40ca9a30a91217b85aeda95b81524f41ba2dae140372a0481de80a.
V3 helper SHA256: 30dc382080d59f6d316e681bf9dc06fced36dc2b6226e24e0190e5c2e0e0741e.

The production inventory validator verified all 82 files (81 indexed plus index), rejecting
symlinks/special files, invalid paths, omissions, extra files and checksum mismatches. The source
prepared directory and registered directory have identical inventories. Bundle HEAD names the
exact candidate. Every manifest asset hash matches Git at the bound candidate/base; live base
assets also match disk. Packaged helper bytes equal candidate ai_manager/deployment.py bytes.
Actual live HEAD equals base, base is an ancestor of candidate, live checkout and retained source
are clean. No db.py/pyproject.toml/uv.lock changes occur in the candidate/base diff.
Source V2 scope 44051737214630f1211289b4f6faf879eb67ca1b96bde180c9d994814ed5afae
matches BOTH manifest assignments and recorded intent assignment_hashes. The actual original
owner message still matches its recorded SHA256. Durable succeeded live-base receipt:
/home/codex/.local/share/linko-bot-manager/deployment-helper/results/a41cc8ad77cb345f895c00b373dd3804.json.
This is the predecessor's receipt, not evidence of this candidate's activation.
Executable read-only assertions and full results: audit.py, audit.json, audit-output.log.

## Risky review: assessed and completed

Native execution is completed, delivered, finished_at set; worker.finished event 201125 exists.
No task.verification event exists for that execution. Its retained legacy assignment policy
contains no verification field; the worker hook returns immediately for that policy. Thus no
native tier/commit/review record was generated. Source lifecycle is completed and lease is NULL
(the legacy status column is pending; lifecycle, not that old column, is used by the intent guard).
Do not infer native review from the simulated test reviewer or source test success.

One separate real non-native provider review reused Workers.review, owner-permitted cheapest
model selection, production REVIEW_INSTRUCTIONS and tool-disabled AppServer. Model gpt-6.1-sol,
effort low, status completed. The reviewer received the entire exact base→candidate diff:
20 changed files, 132946 characters, no truncation. Diff SHA256:
8849e65d4a543c3b4ad324f5dce98b16be59e36c0073632f2b8158b9c4f4d0a6.
Review input/output and usage are retained in review.py, review.diff and review.json. This is
external evidence for the manager, NOT a forged native task.verification record. No second
review or feature reimplementation was performed.

Raw review findings:
1. ai_manager/orchestration.py:322 — Auto intent recording is skipped when delegation has a manual
   deployment intent, so ON owner requests that include deployment still require the manual path.
2. ai_manager/auto_deploy.py:232 — A diverging candidate is marked unverified rather than reconciled.

Assessment: finding 1 is a concrete uncovered ON path. Lines 289–307 discover/create a manual
intent (including owner_deployment_quote); lines 321–322 skip auto owner_request whenever that
intent exists. AutoDeploy processes only kind=auto intents, leaving that owner-requested work
on manual source-refresh/review behavior. The owner requirement says ON removes repeated
approval/fragile source-refresh churn. Existing OFF/manual tests do not establish that ON plus
explicit deployment intent satisfies this requirement. Manager must assess this retained finding;
the review is completed WITH findings, not a clean nonblocking review. No source changes authorized
in this audit and no alternate candidate created.

Finding 2 is qualified: the reviewed contract explicitly requires ancestry and reports real
divergence for serialized reconciliation (AUTO_DEPLOY.md:26–28); silently merging would invalidate
the exact verified candidate. This actual candidate includes live and is unaffected. Ordinary
stacked ancestry is supported. Actual divergence remains a reported operational dependency;
this is not evidence that the exact release's base check fails. No reconciliation is attempted here.

## Additional live manager activation blocker

Recorded intent is status=recorded, task_ids=[source], assignment_hashes={source:scope}, with
review_task_ids=[this audit]. Immutable manifest assignments include only source, correctly.
Live ai_manager/deployment_intent.py:195–196 tasks() returns source PLUS review tasks.
Its review():220 constructs expected hashes for ALL returned tasks, then compares that mapping
with the SOURCE-ONLY intent assignment_hashes and manifest assignments. The audit currently
must finish to satisfy the lifecycle guard. Even after completion, expected has two keys while
the fixed mappings have one, producing 'Assignment scope has not been sealed'. Read-only
calculation in audit.json proves both comparisons false without invoking a mutating manager action.
Candidate code retains the same comparison. Completion cannot fix this deterministic mismatch.

This lies in ai_manager/deployment_intent.py, outside this audit's change_paths and prohibited
source-edit scope. Manager needs sequential review of this existing supported-interface dependency.
Do not edit runtime SQL/protocol, remove review_task_ids, reseal immutable evidence, manufacture
new owner authorization or bypass the exact scope guard. The current report does not establish
that review_deployment_intent can succeed under the recorded intent. No request was submitted.

## Helper v3 supported bootstrap and external blocker

Observed cgroup:
0::/user.slice/user-1000.slice/user@1000.service/app.slice/linko-bot-manager.service
Therefore installer execution here violates the documented outside-manager/helper prerequisite.
No supported external execution capability is available in this worker. Shell execution remains
inside that cgroup. No cgroup escape or guard alteration was attempted.

Installed helper remains v2, SHA256
e6d3c0c7200b749e32814f95ac5a8fc15552091b72bfb030f6c0345e084e60a3;
helper-v2-upgrade.json is succeeded and pins those installed bytes. Unit references v2 exactly
once; unit SHA256 0a6d8412eac9c665bbf022d3d0f0558fc0f51a54d43dc822c5ddd96f531396e4.
No helper-v3.py or helper-v3-upgrade.json exists in the runtime. Handoff is currently ungated;
active.json describes the succeeded predecessor. Installation was NOT attempted; no v3 install
receipt or new-byte identity can be claimed. Installer v3 delegates to v2 and itself has no
cgroup guard; do not interpret that omission as permission to invoke it here.

EXTERNAL_HANDOFF.md contains the exact supported installer command, cgroup precheck, inherited
environment requirement, serialization/pin/lock/unit guards and post-install receipt/byte identity
requirements. Authorized external operator may upgrade ONLY the helper using that command.
This requires no new owner approval, but it requires an actual external authorized context.
Only durable succeeded v3 receipt + installed/registered helper byte identity establish success.
The installer must not be used for manager restart or application activation. On partial failure,
retain receipt/state and inspect externally rather than deleting or blindly retrying.

## Verification coverage and limits

Reused unchanged source gate (registered validation.log is hash-verified): 404 backend tests,
0 failures, 1 upstream warning, 90.19 s; Ruff passed; all six JS syntax checks passed; all 20
browser scripts passed, including Settings owner/CSRF, ON seed and per-project desktop/mobile
persistence. Source required-checks.json identifies the commands. No source code changed after
that fixed gate. Full/backend/browser/build gates were not repeated for this audit.
Source smoke: 44 passed. Source targeted: 35 passed. Source real-health failure/data rollback:
passed in 46.99 s. Indexed rehearsal evidence covers succeeded migration, migration/code-only
health-failure DB restore, dependency DB+venv restore, publication/replay and negative guards.
Those receipts are disposable fixture receipts. Provider review/systemd and dependency resolution
are simulated in source tests; the new read-only provider review here is actual provider output.

Audit fresh smoke once: 44 passed, 360 deselected, 0 failures, 1 upstream warning, 16.28 s
(smoke.log). Test runtime and temporary files were workspace-local; no live service was used.
Audit integrity assertions pass; installed v2 pin and predecessor receipt pass. Diff whitespace
check excluding the byte-exact raw review.diff evidence passes and is retained in diff-check.log.
Raw patch context lines trigger whitespace warnings when treated as newly added text; they are
preserved unchanged for review hash identity. No new browser behavior/build source was touched.
The separate review has TWO reported findings; the scope-review and external-context blockers
are established. Coverage must not be represented as activation-ready or deployed.

## Manager brief/recent-delivery update

Implemented: existing exact auto-deploy policy candidate remains unchanged.
Verified: original fixed 404/backend + 20/browser + Ruff/JS/rehearsal gate reused; all 82 registered
artifact hashes, candidate/live ancestry/assets/scope, and fresh 44-case smoke verified.
Reviewed: missing separate risky diff review completed with findings, raw output retained.
Registered: release.published event 201759; exact manifest/index remain immutable.
Installed: helper v2 only. V3 bootstrap pending authorized external operator using handoff.
Deployed: this candidate NO; live remains bee8b088, succeeded predecessor receipt above.
Pending: finish this audit/result cleanup; manager assess review finding 1 and qualified finding 2;
sequentially resolve existing intent review-task scope incompatibility; supported external v3
installation with durable succeeded receipt and byte identity; only then manager exact intent
review/activation under recorded ba8ce92a3d406a98cdfc9fa81851c251 if guards permit.
Do not rerun the valid fixed gate simply because bootstrap/review waits. Do not claim success
from a requested restart, implementation, preparation, registration or isolated receipts.

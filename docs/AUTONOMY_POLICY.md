# Global autonomous development policy

Implemented 2026-10-11. This supersedes the routine permission and verification-registration restrictions described in the Stage 1/Stage 2 reports. It does not implement Stage 3 manager wake-up.

## What changed

The actual path remains manager delegation → persistent task → scheduler → isolated worker → verification controller → independent file-inspecting review → required integration → completion. No new planner, permission framework, scheduler or agent hierarchy was introduced.

`Policies.effective` now returns ALLOW for routine development globally, regardless of existing profiles or project/global overrides. Existing settings, approval receipts and history remain stored. Every future registered project inherits the same policy immediately; there is no activation or migration step. Scheduler enablement, scope, budget and concurrency are unchanged.

The worker's existing `manager_workspace` tool now accepts `command`, `test` and `install` argv with an optional project-relative working directory. The worker handler actually dispatches all three operations, including the previously omitted installation capability. Native App Server shell/patch/connectors remain disabled; commands use the audited broker and existing OS runner. Worker instructions were shortened rather than expanded.

`VerificationCommands.selected` inspects current workspace manifests, test files and documented checks, and includes worker-selected tests. It does not require owner registration or renewed approval after configuration edits. Known manifest checks take precedence over alternative commands in documentation. Legacy command-management APIs remain optional compatibility interfaces. Replacing/revoking a custom command removes it from future selection; changing checks during an active review requires a fresh independent run, not permission registration.

Code/infrastructure tasks retain applicable independent command checks. When no suite is discoverable, file-only code tasks receive a diff check, available Python syntax checks, and mandatory independent acceptance review. Documentation/research/investigation tasks retain their deliverable/evidence gates and independent review; commands run when explicitly required by their verification specification. A file or documentation task is not blocked because no test commands were configured. A diff/syntax check alone is not proof of functional correctness: the independent reviewer must substantiate the actual acceptance criteria.

Tests run in disposable build copies, with reviewed input hashes checked after execution. System Python has a `python` alias for `python3`; a project `.venv/bin` is automatically on the isolated PATH. Required verified commits are autonomous; a required merge uses the source's current branch when no target was configured. Existing clean-source, content-identity, fast-forward and uncertain-integration safeguards remain.

## Autonomous operations

- Investigation, file reads/listing, edits, creation and ordinary file deletion.
- Arbitrary project argv/shell commands, code generation, builds and tests.
- Project-local dependency installation, including creating a virtual environment and installing packages. Installation has networking for downloads, without inherited host credentials.
- Worker launch/resume under the existing task/scheduler eligibility checks.
- Local branches, commits and merges; independently verified required source integration needs no per-project approval configuration.
- Automatically chosen independent verification commands and read-only acceptance review.

Commands run in recoverable isolated clones. Original repositories, unrelated host data, manager storage, credentials, Unix service sockets and infrastructure authentication are not mounted. Test execution has no network. Remote authenticated push, production deployment and host infrastructure administration are not newly enabled by this change.

## Approval boundary and recovery

`destructive_operations` always requires exact confirmation through the existing approval lifecycle; profiles and overrides cannot turn it into ALLOW. Explicit repository/root removal, database/schema destruction, infrastructure destroy/termination and filesystem formatting requests trigger this category. The worker can also mark a command `catastrophic`. The same check applies to worker tests, installations and independent verification commands. Rejection, expiration and policy-epoch invalidation prevent execution. Ordinary edits, dependency installation, tests and Git work do not create approval rows.

This command classifier is a confirmation trigger, not a universal static analysis of arbitrary scripts. Mount isolation prevents arbitrary programs from directly deleting original repositories or unrelated host files. Dependency installation shares host networking: an unauthenticated network service is not protected by mount isolation, and no claim of universal remote destructive-operation detection is made. Network services must retain authentication; credential-backed host/infrastructure destruction has no supported capability, even after a confirmation.

Before each command, the broker retains a full workspace recovery copy under `verification-workspaces/<operation-key>`. File write/delete preimages are retained under `recovery-files/<operation-key>`. Command receipts identify their recovery copy. Git metadata that would redirect subsequent controller access is restored automatically from the checkpoint; the operation reports failure so the worker can correct its request without owner intervention. These backups are retained rather than silently garbage-collected. Existing storage reserves and command resource/output/time limits remain.

Operations still use the durable started/completed ledger. Unknown process/transport outcomes are not blindly replayed. This preserves task persistence and execution reliability instead of weakening them for autonomy.

## Existing task records

No production database was read or rewritten for this implementation. Task `6aa6574b` is not replaced: the existing scheduler recovery path can retry confirmed completed worker output on that same task once the configuration blocker is resolved by automatic selection. It still requires scheduler eligibility and available capacity. Task `6d1fca78` remains intentionally paused; the recovery query only considers blocked tasks. No task history, project data or execution records were reset or deleted.

## Validation

Disposable repository regression tests cover:

- `hello.txt` containing `Hello World` completes through worker execution and independent review with no registrations or approvals, both as code and documentation.
- An existing project with a conservative profile/explicit DENY and a newly registered project inherit identical autonomous permissions.
- A coding worker modifies a calculator, creates a Git branch, executes tests, passes a separate independent review and performs a required verified merge without configuring integration permissions.
- A virtual environment bootstraps and installs a disposable Python wheel without approval; installation exposes no host home/service sockets.
- Repository deletion, root deletion, database destruction and infrastructure destruction wait for exact confirmation before any operation starts; rejection prevents execution. Approval consumption, expiration and stale-policy invalidation remain tested.
- Git metadata redirection recovers automatically; recovery copies and operation receipts persist.
- A legacy missing-verification blocker recovers on its original task without replaying its worker, creating replacement tasks or unpausing an intentionally paused task.

**127 regression tests passed** in 38.79 seconds in a clean disposable checkout containing only the staged autonomy patch. The full working tree, including pre-existing changes, also passed **136 tests** in 40.16 seconds. `ruff check ai_manager scripts tests` and `git diff --check` passed. The existing FastAPI/Starlette test-client deprecation warning remains.

Real installed Codex smoke test (`scripts/smoke_autonomy.py`): **passed**. A new Hello World project and subsequently registered calculator project each completed with **one task, one worker, independent verification, zero approval rows and zero configured verification commands**. The first coding attempt exposed the missing `python` alias; after the deterministic runtime fix, the complete live smoke passed. These are individual real-model observations, complemented by deterministic regression coverage.

Reproduce without production data:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check ai_manager scripts tests
git diff --check
.venv/bin/python scripts/smoke_autonomy.py
```

The OS-runner tests require user systemd/bubblewrap access outside a nested sandbox; the live smoke also needs the existing Codex ChatGPT login. They use disposable databases and repositories, not the production service.

## Remaining blockers and limits

There is no remaining owner-registration or routine approval gate on the development path. Actual test failures, missing runtimes/dependencies, unavailable OS isolation, insufficient storage, source divergence/local work and genuinely uncertain execution outcomes can still block verified completion. The reviewer remains responsible for acceptance evidence when a repository has no test suite; automatic selection cannot invent functional coverage. Recovery copies increase disk usage and retain existing storage reserves.

Production activation completed on 2026-10-11 after the owner explicitly requested deployment of everything and a service restart. The full tested working tree was checkpointed as `0e6ee27`, and the dedicated service restarted successfully with private HTTPS and loopback health checks passing. All 42 existing tasks and original history rows were retained, with unchanged scheduler settings. See [the deployment record](DEPLOYMENT_2026-10-11.md). No Stage 3 wake-up or replacement-task recovery was added.

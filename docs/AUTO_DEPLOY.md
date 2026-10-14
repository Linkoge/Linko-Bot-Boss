# Auto-deploy verified work

The owner requested this policy on 2026-10-14 for Linko-Bot-Boss. It removes the separate
repeat deployment authorization stage; it does not change application login, sessions or CSRF.

Settings → Deployment shows **Auto-deploy verified work** for each project. The value is stored
in `settings[auto_deploy:<project ID>]`. The configured manager project is seeded ON once, using
its helper configuration's repository identity rather than its editable display name. Other
projects default OFF. Explicit saved toggles survive restarts and are never reset by seeding.
The existing controlled helper can deploy its configured repository only; Settings explains
when another project has no configured deployment helper.

## Standing intent and exact release

With ON, a real owner Auto/Execute `delegate` or `continue` records a durable automatic intent
for that task. Its original owner message/execution IDs and digest remain audit provenance.
There is no repeated owner quote, candidate approval screen or per-message consent refresh.
Unrelated chat, chat cleanup and ordinary result delivery do not revoke that recorded intent.
Internal follow-ups may finish the original assignment but cannot invent an owner request.

The scheduler waits for worker cleanup, delivered results and completed job follow-up. Its
checks require the current Option B tier record, no tier escalation/error, and a clean commit
matching `task.verification.commit`. Risky work also requires a completed cheap diff review
with no blocking findings. Older records lacking exact commit evidence are not eligible.
`assignment_source` and the actual workspace HEAD/cleanliness must agree. A candidate must
include the current live commit; stacked sequential workspaces are supported without changing
another assignment's branch. A diverging candidate needs serialized source reconciliation and
is reported; it is never silently merged or activated under a different commit.

Preparation fetches that exact source commit into a disposable clone. The running manager's
fixed preparation script runs the smoke gate for an automatic assignment and the full
backend/browser, Ruff and JS checks once per release. Full-suite coverage also includes all
required targeted/backend/browser checks. No manifest exists after a failed check. The
manifest binds the execution, commit, stable Option B v2 assignment scope, live base, asset
hashes, bundle and validation log. An evidence index inventories every release artifact.

Preparation and dispatch are separate scheduler steps so a quiet-queue wait or the external
helper bootstrap does not rerun validation. Scope/candidate changes withdraw the old prepared
release; the updated assignment needs its own completed, verified attempt. The pipeline alone
consumes automatic intents; manager manual review/dispatch actions cannot substitute a release.
Dispatch rechecks verification, scope, clean source and current live base, then pins the immutable
manifest/index identity in the helper origin. The standalone helper checks exact manifest,
setting, project/task holds, scope, delivered execution and consumed intent at acknowledgement
and again immediately before cutover. The existing persistent-idle/admission fence remains.

Explicit assignment stop/pause/cancel/wait, owner-wait/deferred follow-up and project pause
are honored. OFF revokes unused automatic intents and cancels an automatic `waiting_idle`
request. Once acknowledged, UI mutations are fenced; an interrupted cutover requires external
inspection. OFF retains the previous manual owner-intent → exact evidence review → dispatch
flow, including its existing source-review behavior. ON is not a grant to arbitrary worker
prose, unverified work, another project or another release.

## Explicit intents and audit dependencies (readiness correction)

ON also applies when new work is delegated with `owner_deployment_quote`, an explicit
new-work `intent_id`, or a matching intent discovered from the same owner turn. The backend
links that original manual provenance to a task-specific automatic intent and marks the unused
manual stage superseded. It returns the automatic intent ID. No extra manual evidence-review
or owner-message refresh stage is needed. Cancelling the original intent also cancels its linked
automatic intent. A different new-work deployment request does not revoke another assignment's
standing automatic intent. OFF retains the existing manual flow.

A delegation explicitly auditing a fixed existing release or source assignment remains a
readiness dependency of that fixed request. It cannot authorize deployment of the audit's own
branch instead. Source tasks come only from fixed `task_ids` or the source `task_request`;
`review_task_ids` require completed, unheld lifecycle and completed execution evidence, but
are excluded from the expected immutable release-source mapping. Versions 1 and 2 retain
their original hash algorithms and strict scope checks; no legacy record or artifact is resealed.
Real divergence still requires serialized source reconciliation and exact renewed verification.

## Migration, backup and rollback

New schema/dependency changes use helper v3's rehearsed contract. A candidate's `Database`
initializer runs against a private SQLite online backup, never the live database. Preparation
checks integrity, foreign keys, preservation of old tables/columns/rows, monotonic schema
versions and records schema fingerprints. Destructive/failed migrations do not prepare a
release. Dependency changes first install the candidate's frozen lock into a disposable venv;
that environment runs rehearsal and all release checks. It is then removed.

The helper refuses a database whose schema differs from the rehearsal. It backs up the
manager/native histories, retained workspaces/source refs and private configuration, stops
the service, checks idleness again, and takes a final database snapshot before activation.
Dependency releases also snapshot the service venv before running `uv sync --frozen`.
Post-start probes check both configured HTTP origins, release/scheduler, authentication,
assets and preserved data/schema. Health failure restores the previous code and the final
SQLite snapshot for all automatic releases (including code-only releases), and the old venv
when dependencies changed. Manual rehearsed schema/dependency releases also restore data.
Older manual code-only rollback continues to preserve its database as before.

Results and restoration hashes are durable in `deployment-helper/results/<nonce>.json`.
The owner receives one result message identifying success, failure or automatic rollback,
including whether the database/dependencies were restored. Failures/interrupted preparations
and failed deployments are not retried automatically. A helper crash while mutating leaves
admission fenced, rather than claiming success or retrying. An explicit later owner request
can start a new verified attempt; it cannot reuse a failed release receipt.

## External bootstrap and this second release

This worker does not activate production. The second release packages `helper-v3.py` and its
pinned hash even for a code-only application diff. The currently installed helper is v2.
After worker cleanup and exact artifact publication, the authorized external manager/operator
must install v3 using the candidate's `scripts/install_deployment_helper_v3.py` with the registered
release ID and runtime `--data` path. The guarded installer refuses manager/helper cgroups, pins the prepared helper, preserves
the old unit/hash in its receipt, waits for no pending handoff, and upgrades only the independent
helper service. It does not restart/deploy the application. This is a one-time bootstrap operation,
not an additional owner deployment approval. It must run outside the helper/manager cgroup.

Automatic dispatch waits for a succeeded v3 upgrade receipt and an installed helper whose hash
matches the prepared artifact. A waiting release remains immutable and is not revalidated on
each tick. Future changes to helper implementation require an explicit safe helper installation;
a hash mismatch never downgrades the activation guard. See [deployment helper](DEPLOYMENT_HELPER.md)
for external operations and [verification tiers](VERIFICATION_TIERS.md) for Option B.

## Evidence

`tests/test_auto_deploy.py` exercises owner queue/delegation, real isolated worktrees, backend
commit verification, real smoke/full fixture checks, SQLite rehearsal/backups, pinned bundles,
actual HTTP servers and helper health/rollback. The provider review and systemd commands are
simulated; dependency resolution is a deterministic frozen-lock adapter so tests need no remote
packages. The application release gate separately runs the full repository suites with its real
dependencies. Negative cases include missing/wrong tier/commit/review evidence, dirty source,
failed full checks/rehearsal, destructive migration, schema drift, explicit holds/OFF, changed
scope/manifest, interrupted preparation, restart/chat cleanup and no automatic retry.
`scripts/browser_auto_deploy.py` checks authenticated/CSRF-protected desktop/mobile toggle
persistence, one-time ON seed, project independence and layout. Final validation/artifact hashes
and the publication-ready manager handoff are retained in this assignment's detailed report.

The completed readiness audit `27b4bbb092704f739eb7b30b2bb4d308` covers the original registered
82-file package. Close source corrections require a new release ID and current source-task scope;
old artifacts and intent `ba8ce92a3d406a98cdfc9fa81851c251` remain unchanged. Continuing this
source task changes its scope fingerprint, so the old sealed fingerprint no longer authorizes the
new manifest. The live old manager additionally still combines source and audit tasks in review.
Publication is possible after cleanup with current source binding; activation is not established
by publication or helper installation. The current old manager has no scope refresh in
`revise_deployment_intent(preserve)` (it refreshes message provenance only). An authorized manager
must resolve this supported-interface compatibility dependency serially; do not modify SQLite
intent mappings, drop review dependencies, fabricate a new owner request or request activation
through shell. This is a concrete bootstrap limitation, not a new owner deployment approval.
Corrected exact artifacts, review and external commands are in the retained assignment's
`.data/auto-deploy-corrections/REPORT.md` and `EXTERNAL_HANDOFF.md`.

The corrective real reviewer also reproduced concatenated progress commentary before its final
`No blocking findings.` verdict. The review collector now uses the completed final agent message
for eligibility and retains the raw transcript as evidence. A progress message declaring no
findings cannot mask a blocking final message. Providers without completed-message events keep
the existing stream fallback. This avoids another approval/review cycle caused by commentary.

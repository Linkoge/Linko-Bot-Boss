# Delivery pipeline: checks, merge, deploy

Owner-approved replacement (2026-10-15) for the earlier verification tiers, immutable release bundles, deployment
intents/approvals, exact-commit evidence binding and the hash-pinned deployment helper service. All of that is
removed; its history is in Git. Older dated sections elsewhere that mention manifests, receipts, helper versions or
`deploy_release` are historical.

## 1. Worker checks (once per task)

Every task, every size: run the smoke suite (`python -m pytest -q -m smoke`) and the targeted tests for the changed
behavior (plus build/lint when relevant) **once**. Fix failures for at most **five repair rounds** (durable counter in
`assignment_workflow`, surviving retries/restarts). Then stop and report the concrete failure. The final reply ends
with `RESULT: PASSED` or `RESULT: FAILED - <check>`. There is no independent AI review, no risk tier and no escalation.
One coding worker runs at a time globally. The full backend suite is not run by workers.

## Five-round activation and retained assignments (2026-10-15)

The owner permanently raised the application limit from three to five total repair rounds. Both
`repair_rounds` (committed manager/UI repair continuations) and the separate `followup_rounds`
(manager wakes) retain their cumulative values; transport retries and paused resumes do not charge
repairs. No migration, reset or automatic release of owner holds accompanies this change. The fifth
queued repair may finish successfully; another repair is rejected atomically at five or above.
Worker prompts report the saved usage and `max(0, 5 - usage)` remaining. Workers must also retain
actual verification usage from durable evidence when it exceeds machine metadata; counters do not
measure individual test/fix cycles inside a worker turn.

Activation uses the normal PASSED-candidate merge/deploy pipeline below. Only a successful deployment
status establishes activation; editing policy code does not extend the already-running worker's
injected budget. This policy implementation worker remains governed by three rounds.

Held model/recording assignment `4a985c35b51345ada354cd666acbf80e` remains separate. Its retained
`docs/MODEL_SETTINGS_REDESIGN_REPORT.md` (read with that task_id) records **three actually used**; supplied machine metadata
is **repair_rounds=2**. Keep both facts, failed candidate `1e086d4`, and owner-reported live `f8f4d55`.
This policy-only branch is based on `f8f4d55`, excluding failed `1e086d4` from its ancestry.
It neither resumes nor deploys that failed candidate, edits its workspace, nor
changes shared counters. The lifecycle has no dedicated historical-counter reconciliation operation.
After policy activation and explicit owner continuation authorization, the manager should read_task,
read_result for execution `d1e9eec6a6304ffaa0ebf2fc8df415d6`, and read_document(task_id, report path),
then continue the **same** inactive assignment with the discrepancy and a conservative **two remaining**
in appended constraints/context. Re-read current revision, hold, lease and counters first. For an unchanged
failed lifecycle/count of two, this real continuation transaction charges **2 → 3**; its next prompt shows
three used/two remaining. That is a continuation charge, not a retroactive correction or proof of historical
usage. Paused/waiting resumes do not charge it: carry the evidence-based ceiling explicitly even if machine
remaining is higher. Do not manufacture a continuation solely to adjust a number or rewrite old evidence.
The worker must stop after the two actual remaining rounds if still failing; the manager must preserve
that exhaustion/hold even if stale counters would permit more. Subsequent real continuations remain
cumulative and replay-safe. Only a later verified PASSED result may enter automatic merge/deployment.

## 2. Merge to main (sequential)

Per-project setting **Auto-merge and deploy finished work** (Settings → Deployment; the manager's own project is seeded
ON once). When ON, `ai_manager/pipeline.py` merges each finished assignment branch into the project's `main`, oldest
first, one at a time, once the worker has completed, its result is delivered, the follow-up state is `complete` and the
branch is committed and clean. It never forces anything:

- `RESULT: FAILED`, an owner hold (`owner_wait`/`deferred`/pause/cancel), OFF, or a paused project: nothing merges.
- Dirty checkout or merge conflict: the merge is aborted, the reason is posted in the originating chat and the
  assignment stays for repair. The same commit is not retried automatically.
- OFF means no merge and no deploy; work stays on its assignment branch. The manager `deploy` action (owner asks to
  deploy now, optional `task_id` to merge first) works regardless.

## Checkout fence

One lock file (`DATA/deploy/checkout.lock`, `ai_manager/fence.py`) protects the live checkout. Merges (automatic and the
manager `deploy` action) take it exclusively and re-check for a pending deploy inside it; task claims and chat sends take
it briefly in shared mode. The deploy script holds it exclusively from its final idle check until the service is healthy
or rolled back, so no merge or new work can start in that window. Both sides resolve the main branch the same way
(`main`, else `master`).

## 3. Deploy (one script)

After a successful merge into the manager's own repository the pipeline writes `DATA/deploy/request.json`. systemd
(`linko-bot-manager-deploy.path`) starts `linko-bot-manager-deploy.service`, which runs `scripts/deploy.py`:

1. Check out `main` in the live checkout (must have no uncommitted changes) and run the full backend suite **once**.
2. Wait until nothing is active (no unfinished execution, worker lease, streaming message or running delivery).
   While a request is pending the scheduler starts no new coding and merges nothing. Owner OFF, a paused project, or
   cancelling the request (Settings → Cancel) stops the deploy here, before anything changes. Waiting is bounded (6 h).
3. Take the fence, re-check idle, back up the SQLite database (online, integrity-checked), stop the service, take a second consistent copy.
4. Update code to `main` (`uv sync --locked` only if `pyproject.toml`/`uv.lock` changed), start the service.
5. Health check (60 s): `/api/auth-status` answers 200 with `scheduler_running` and the new commit, an unauthenticated
   `/api/portfolio` is 401, and a temporary session works (login path) and is removed.
6. Any failure after the stop rolls back automatically: `main` is reset to the previously running commit (the failed
   commits stay reachable under tag `linko-failed-<id>`), the database is restored from the stopped copy, dependencies
   are re-synced if they changed, and the old version must pass the same health check. The service is always started
   again, even if a rollback step fails (then the result is `rollback_failed` with every failed step named).

The outcome is one short result in `DATA/deploy/last-deploy.txt` (also `result.json`, `results/<id>.json`): `success`,
`failed` (nothing changed), `rolled_back` (with the reason), `rollback_failed` (needs attention) or `cancelled`. The
pipeline posts it once to the originating chat and shows it in Settings and `deployment_status`. A failed or rolled-back
deploy is never retried automatically; repair is a normal task. `success` is the only deployed claim.

Backups live in `DATA/deploy/backups/<id>/` (last 10 kept). Deploys do not touch attachments, workspaces or other data.

## 4. One-time bootstrap (operator)

The new script cannot install itself through the old helper. After the manager has merged this work into main of
`/home/codex/projects/Linko-Bot-Manager`, run once on the server:

```bash
cd /home/codex/projects/Linko-Bot-Manager && python3 scripts/install_deploy.py
```

It needs no sudo. It writes `DATA/deploy/installed.json`, installs and enables the user units
`linko-bot-manager-deploy.path/.service`, retires the old `linko-deployment-helper.service` if idle, and queues the first
deploy so the running service picks up the new code (progress in `DATA/deploy/last-deploy.txt`). Safe to re-run.
After that everything is automatic. Manual deploy at any time: `python3 scripts/deploy.py --now`.

## Tests

`tests/test_pipeline.py` (merge rules), `tests/test_deploy_script.py` (private disposable runtime: success, unhealthy
release rollback of code and database, service that never starts, idle wait, holds/cancellation, failing tests,
dirty checkout, installer) and `tests/test_task_rules.py` (repair budget, one worker).

# Risk-tiered worker verification (Option B)

Owner-approved 2026-10-14. Small tasks use less time and fewer tokens. Risky work keeps its checks.
Code: `ai_manager/verification.py`, delegate/continue in `ai_manager/orchestration.py`, batching in
`ai_manager/tasks.py`, worker prompt and post-checks in `ai_manager/workers.py`.

## Tiers

| Tier | Typical scope / route | Worker checks | Skipped |
|---|---|---|---|
| Small | UI/text, one script/test file; cheapest family or workhorse at Low | smoke suite, tests covering touched files, build/lint of touched files | full suite (release preparation runs it once), reviewer |
| Normal | ordinary code; workhorse at Medium | smoke, targeted tests while iterating, build/lint, full backend suite **once** at the end | repeated full runs, reviewer |
| Risky | DB/schema, auth/session, deploy/release/backup, worker/manager/recovery logic, integration, deployment preparation; workhorse High or strongest | Normal checks + browser/acceptance scripts for touched behavior, then **one** cheap read-only diff review (automatic) | worker self-review passes |

The full backend+browser suite runs exactly once per release in `scripts/prepare_deployment.py`. Small tasks rely on
that gate. Every tier runs the smoke suite and gets the same short report rule. The worker's final reply is at most 12
lines: outcome, files, commit, checks with pass/fail counts, limitations. The execution record is the detailed
evidence. It keeps every native command/output event and the `task.verification` record, alongside updated docs.

## Deterministic classification

`select(change_paths, objective, acceptance, requested, integration, deployment, previous)` has no AI call:

- **Risky** if any declared path is a dependency/infra manifest (`pyproject.toml`, `uv.lock`, `package.json`, `.env`, …),
  has a DB/unit suffix (`.sql`, `.sqlite`, `.service`, `.timer`, …), is a code file under a risky directory
  (`deploy/`, `migrations/`, `auth/`, `db/`, `security/`, `.github/`, …), or is a code file whose name contains a risky word
  (db, schema, migration, auth, session, csrf, secret, permission, deploy, release, rollback, backup, restore, worker, manager,
  orchestration, scheduler, recovery, lease, assignment, parallel, provider, codex, claude, tasks, server, service, api).
  Backend code is also risky when its name contains an entry-point/config word (app, main, web, cli, config, settings,
  chat, routes, …). UI scripts such as `app.js` are not risky for those words. Also risky: objective/acceptance text that names
  databases/schemas/migrations, auth/login/sessions/CSRF/passwords/credentials/secrets, encryption, security, permissions,
  rollback, backup/restore, systemd or leases. Integration assignments and deployment-intent delegations are risky too.
  Docs (`.md`, `.txt`, `.rst`) and test files are never risky by path.
- **Small** only with 1–3 concrete files, all docs/styles/assets/UI scripts/tests, at most one script/test file.
- **Normal** otherwise, including empty, directory or unknown scope and any backend/configuration code.

The manager's optional `verification_tier` can raise the floor and never lower it. A requested lower tier is recorded
as `requested <tier> not allowed`. `continue` reclassifies the appended objective/acceptance with the previous tier as
a floor (`tier never lowers`). For integration it is risky. Repeated identical inputs give identical records
(`{version, tier, requested, reasons}`) in `assignment_policy:<task>.verification`.

In Automatic model mode, a Risky delegate is rejected if its pair is weaker than workhorse High or strongest family while
such a pair is permitted. Manual mode always uses the owner's saved pair (see [model settings](MODEL_SETTINGS.md)).

## Post-completion guard and diff review

Mis-declared paths cannot keep risky work Small. When a tiered worker turn completes, the backend lists the assignment
branch's actual changes against `workspace_base`, including untracked files. It classifies those paths with the same
rules. The `task.verification` event records: assigned/actual/effective tier, `escalated`, changed files, undeclared files.
If the effective tier is Risky and files changed, it runs one review turn. This turn uses a separate non-native,
read-only provider process on the cheapest pair the owner policy permits. The input is the objective, the acceptance
criteria and a diff bounded to 60 000 characters. There is a 300 s bound. Findings are recorded. Review failure records
`unavailable` and never blocks completion. A pause or cancel during review interrupts it like the worker.

The manager's `worker_result.verification` is compact (tiers, escalation, up to 5 undeclared paths, review status and
600 characters of findings). An escalation or review findings mean `continue` on the same assignment before the job
is complete. If recovery finishes a turn after a restart, the notice says `post_check: not recorded for this attempt`.

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

## Documentation follow-up evidence — 2026-10-14

Implementation remains `e04548b53ea56a75b64481bcb06d42ac396c0771`; its final validation was recorded in
`642511a0878e9df7a6937bb6af4dfdd65adacd80`. README, DECISIONS and STATE were brought into agreement
with this contract in the same retained assignment branch. The fresh command
`.venv/bin/python -m pytest -q -m smoke` passed all 44 cases (311 deselected) in 3.08 s, with one upstream
Starlette/httpx deprecation warning. Full backend/browser and real-clock rollback results above are retained
implementation evidence; documentation edits do not repeat those expensive suites. No application code changed
in this follow-up. Nothing was prepared, deployed, pushed or restarted; no production data was accessed.

# Combined policy/model/recording integration candidate — 2026-10-15

Serialized integration in retained assignment `e01954965b8347b7abbb6d1d9d5a92cf` merged exact
source `37898cd556db919d24c184bfa9d1707d7cb9d1f8` into policy candidate `a0f9694`, without
conflicts. This source supersedes its earlier failed status under the owner's revised acceptance;
the optional recording-start diagnostic is not a delivery gate. Historical failures and actual
five-used source budget remain in [the source report](docs/MODEL_SETTINGS_REDESIGN_REPORT.md).
Five-round policy/cumulative-counter enforcement remains intact; no runtime counters were changed.
Combined validation passed: 37 smoke tests, 114 focused backend tests, lint, JS syntax, and
favorite-recall/recording/model-settings/chat Chromium checks. No new repair round was needed.
Candidate activation is pending application-managed merge/deploy; no live deployment claim.
Integration evidence: [policy report](docs/REPAIR_POLICY_REPORT.md). Earlier snapshots below are historical.

# Five-round policy candidate — 2026-10-15

Prepared in assignment `e01954965b8347b7abbb6d1d9d5a92cf`; activation is pending normal
application-managed merge/deploy. The policy-only branch is based on `f8f4d55`, excluding
failed `1e086d4` from its ancestry. No deployment success is claimed. Permanent repair/follow-up
policy is five total rounds, preserving saved usage/holds; this worker still has its injected three-round
limit. Held model/recording assignment `4a985c35b51345ada354cd666acbf80e` is untouched:
actual three used versus machine two remains documented, failed candidate `1e086d4` retained,
owner-reported live `f8f4d55` unchanged by this work. See [activation and continuation](docs/AUTO_DEPLOY.md#five-round-activation-and-retained-assignments-2026-10-15)
and [policy evidence](docs/REPAIR_POLICY_REPORT.md). Earlier state below is historical.

# Current state — minimal pipeline, 2026-10-15

**Status: implemented and verified on branch `linko/assignment-a683bcca8afd4f12a4af581e5706a0ac`; not merged, not
installed, not deployed.** No live runtime, service, shared checkout or database was touched. The manager must merge the
branch into main; the owner then runs the single bootstrap command below once.

## What changed

- Task rules (kept): one global coding worker, smoke + targeted tests once per task, at most 3 repair rounds then a
  concrete failure report (`RESULT: PASSED|FAILED` marker), no independent AI review/escalation, no usage-limit
  account gate, multi-step delegation-chain compatibility.
- Removed: hash-pinned deployment helper service and its upgrade-receipt bootstrap; immutable release bundles,
  manifests, evidence indexes and publication; deployment intents/approvals; exact-commit evidence binding and
  release-preparation/ancestry gates; risk tiers and automatic AI security-review selection; the long manager
  deploy-workflow instructions. Old tests for those were deleted with them.
- Added: `ai_manager/pipeline.py` (merge finished work to main one at a time, write the deploy request, report the
  result), `scripts/deploy.py` (the one deploy script), `scripts/install_deploy.py` (one-time installer) and
  `deploy/linko-bot-manager-deploy.{service,path}.in`. Contract: [docs/AUTO_DEPLOY.md](docs/AUTO_DEPLOY.md).
- Auto-deploy ON = merge + deploy; OFF = neither; explicit owner holds/pauses are honored. Manager actions:
  `deploy` (owner asks to deploy now) and `deployment_status`.

## Bootstrap (owner, once, after main contains this work)

```bash
cd /home/codex/projects/Linko-Bot-Manager && python3 scripts/install_deploy.py
```

No sudo. Installs and enables the user units, retires the idle old helper service and queues the first deploy. Result:
`~/.local/share/linko-bot-manager/deploy/last-deploy.txt`. Until it is run, merged work is not deployed and requests
wait with "waiting for the one-time install command" in Settings.

## Verification (this branch)

Full backend suite once at the end; deploy/rollback/health paths run against a private disposable copy with its own Git
repo, SQLite database and port (`tests/test_deploy_script.py`). Final run: 315 passed (full backend, once), ruff clean, `browser_smoke.py` and `browser_auto_deploy.py` passed. Not verified: the
real systemd path unit and real `systemctl` stop/start (the installer test uses a fake `systemctl`); the first live
deploy will be the first real systemd run.

## Known limits

- Deploy waits for `uv`/network only if dependencies changed; a failed rollback re-sync reports `rollback_failed`.
- A rolled-back release resets `main` to the previous commit (failed commits kept under tag `linko-failed-<id>`);
  its assignment branch remains and the work needs a normal repair task.
- A worker that omits `RESULT:` is treated as passed; only an explicit `RESULT: FAILED` blocks the merge.
- Older sections in feature docs about manifests, receipts and helper versions are historical.

Feature facts (audio, composer, PWA, usage, models, memory, parallel workspaces, recovery) are unchanged; their
contracts are in `docs/`. Earlier state and handoff history is in Git.

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
repo, SQLite database and port (`tests/test_deploy_script.py`). Final run: 308 passed (full backend, once), ruff clean, `browser_smoke.py` and `browser_auto_deploy.py` passed. Not verified: the
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

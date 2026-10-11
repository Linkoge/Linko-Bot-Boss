# Stage 2 acceptance preparation — 2026-10-11

Owner authorized the dedicated backend restart, global continuous scheduling, protection of old test work, and preparation of a disposable acceptance repository. No new manager assignment was requested or executed. Stage 3 was not implemented.

## Live state

- Service `linko-bot-manager.service` restarted at 09:38:05 Asia/Tbilisi, PID 1521798. Startup completed at 09:38:06. The service uses this checkout and the unchanged live database path. Its newly loaded orchestration implementation is the committed Stage 2 implementation from `4c012e3`.
- `scheduler_enabled=true`, `scheduler_scope=null` (Python `None`, all projects), `scheduler_batch={"limit": null, "task_ids": []}`. Removing the prior finite completion budget prevents its completion counter from eventually pausing continuous scheduling. Original batch tracking remains in the backup and historical action events.
- Global worker limit remains 2. The existing one-active-coding-assignment-per-project limit, dependency gates, policies, isolated workspaces, and independent acceptance mechanisms remain unchanged.
- New databases persist these scheduler defaults with `INSERT OR IGNORE`; reopening preserves explicitly saved owner settings. Later project registration requires no scheduler configuration or project allowlist. Explicit task/project pauses and permission gates still apply.

## Existing queue

Inspected all 15 pending tasks before enabling unrestricted scheduling. Task `6d1fca78ec2f4f39a7a32e76dd92ec50` in linko-gym-coach was the old hello.txt test and had no prerequisites. It is now **paused**, with a recorded task transition and explanation; its ID, plan, description and history remain intact. Explicit resume is available later.

The other 14 pending tasks belong to Linko-Bot-Manager and retain their unfinished prerequisite chains. Their failed/blocked prerequisites were not retried or altered. No existing task became automatically dispatchable, and no worker was launched.

## Disposable acceptance project

- Repository: `/home/codex/projects/linko-stage2-acceptance`, branch `main`, no remote.
- Registered project ID: `cb1ffeb2ab074accac78768855c686b6`.
- Committed passing calculator baseline and two unittest cases; no external dependencies or downloads.
- Owner-registered verification command: `python3 -m unittest discover -v`.
- Inherits the existing global High Autonomy profile. Read/edit/create files, tests, worker launch/resume, branch creation and commits all resolve to ALLOW; no policy changes or new approvals were introduced.
- Baseline command passed using a disposable verification copy through `run_checked` and the existing Bubblewrap sandbox, with project input hashes unchanged. This preflight created no task, plan, execution row or model assignment.
- No project merge target or remote/deployment operations were added. A later assignment may use normal independent verification and isolated local commits.

## Preservation and checks

Private integrity-checked pre-change backup:
`/home/codex/.local/share/linko-bot-manager/backups/manager-before-stage2-global-20261011T053742Z.sqlite3`.

Live database integrity check passed. Comparison against the backup confirmed all original record identities remain, all original events/messages/plans/executions/checkpoints/approval and operation records are unchanged, and task/plan/execution counts remain 41/10/67. Only the hello.txt task changed state. Scheduler scope and batch changed as requested; enabled was already true. Adding the fixture added a project, its memory, its verification command, and audit events. Three existing exported memory rows reflect the paused Gym Coach task.

Manager health endpoint returned HTTP 200 after restart; no scheduler errors or active executions were observed. All 11 unrelated active user services retained their original PIDs.

Validation: 124 regression tests passed; Ruff and `git diff --check` passed. Fixture-only tests/scripts that previously relied on disabled scheduling now explicitly pause their isolated databases before inspecting delegation or launching workers manually. Existing unrelated working-tree changes were preserved.

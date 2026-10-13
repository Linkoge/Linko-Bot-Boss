# Explicit To-Do handoff — bundled candidate, 2026-10-13

Release `todo-handoff-chat-trash-recording-agents-v1-20261013`; full evidence: `/home/codex/.local/share/linko-bot-manager/deployments/todo-chat-20261013/REPORT.md`.
This candidate preserves startup recency and the existing active backlog table; no migration is added.

Add/Edit/Delete remain backlog-only. The explicit Start button submits an owner Auto message to the
existing project conversation and FIFO manager queue, preserving the composer draft/attachments.
Acceptance of that message alone does not consume a To-Do. The Start request binds exact IDs/revisions
to its durable manager execution in `execution_todo_sources:<execution>`, atomically with message acceptance;
its eventual delegate/continue inherits those sources even if the model omits the explicit tool field.
Invalid/stale/wrong-project sources reject before queuing; request replay after successful handoff returns
the original receipt. Natural scheduling also supports explicitly supplied tool selections. An ambiguous request retains its exact
request ID/payload for retry in per-tab session storage; a later deliberate Start after accepted manager
failure has a fresh ID. No background action starts entries. Natural owner scheduling (including bundled
"do todo list") uses the same manager delegate/continue actions with `todos:[{id,revision}]`.
Conversation mode still cannot dispatch; internal results/recovery cannot consume To-Dos.

Task creation/reuse or bundled continuation and consumption occur in one SQLite write transaction.
Stale/wrong-project/previously consumed selections reject and roll back the whole dispatch;
model/setup failure leaves the entries active. Same-task/source-revision replay is idempotent.
On success `archived_at` removes the entries from the active list. Original title/details/source_task_id,
full preimage, accepted task ID, reason/time are retained in `todo_handoff:<id>` settings and
`todo.consumed` task-linked events. Original source_task_id is never repurposed (one original recording
assignment can differ from execution assignment). `list_todos(todo_id=...)` retrieves archived source
and its handoff link for recovery/reuse, with the same project boundary. No physical row is deleted.

Current authorized data cleanup is already applied, separately from candidate activation: three scheduled
entries link existing assignment `46690e845e75461cbfe9309f5c734662`; old Pause/Cancel entry is archived
on owner confirmation with reference `a01b6c008c19464f883d5709937fc906`, without new investigation.
Full preimages and the accepting assignment are in the private `todo-owner-handoff.json` receipt.
No other old entries are scheduled or investigated. Earlier deferred wording below is historical.

# Startup / To-Do release preparation — 2026-10-12

Status: implemented and reviewed in the isolated checkout; **activation pending outside the manager service**.
Internal evidence: `/home/codex/.local/share/linko-bot-manager/deployments/startup-todo-20261012/REPORT.md`.
This receipt supersedes stale “mobile patch pending” text: actual live source, startup API and served assets
are `9e5c5181cb85ad3d0484af8b7e92e305ed8f190b`, schema 10. The deployed combined reliability/global-recency/mobile
changes are the candidate's base, preserved rather than replaced. The live dirty TASKS.md deferred note is
preserved privately, migrated to one linked SQLite entry on activation, and replaced by an authoritative-backlog
reference in the candidate. No other dirty source was found; no remote push was performed.

## Behavior and decisions

Startup uses the same durable last_used_at/global_last_used_at ordering and name/ID tie breaker as the navigation.
All Projects participates; the request explicitly says context/project and global ordering, so no concrete-project
priority exception is inferred. The selected context is recalculated at login, fresh browser session and reload.
No projects selects null/global. Ties follow existing navigation, without fabricated recency. Opening, polling,
view switches, renaming and To-Do CRUD do not change meaningful-use timestamps. Initial loading is protected
from background refresh and later user selections. Tasks retains its existing queued/running/waiting filter and
selected-project/portfolio scope; its badge is entirely hidden at zero, including initial markup.

To-Do immediately follows Agents. One project-owned SQLite table supplies a portfolio list in All Projects and
project isolation otherwise. Add/Edit and checkbox selection/Delete selected use the existing authenticated,
CSRF-protected API, optimistic revisions, refresh and editor patterns. Deletion archives only selected entries;
a stale or wrong-project batch rolls back in full. No task or execution deletion route is reused. Archived rows,
source links, update preimages and audit events remain durable. Text selection and focus survive unchanged polls;
switches immediately clear old entries/selection and reject stale responses or editor saves. Controls remain
usable at desktop, mobile and narrow reduced-height viewports.

The existing manager_action capability adds list_todos/save_todo, allowed for deferred recording in every chat
mode. Manager instructions require future requests to reuse/update To-Do without delegate/continue/wait or
scheduling. Updated dynamic-tool schema causes existing native conversations to start a compatible thread using
the existing historical/context handoff; no native history is deleted. New projects and fresh conversations use
the same instruction/capability. To-Do records have no queue lifecycle, execution ownership or automatic promotion.
Explicit later owner scheduling is a separate execution assignment. The memory redesign is still a future proposal.

Schema 11 links assignment `2b15765038784009b8f6ad8e0e7008ea` once. The original deferred Markdown details and exact
recording objective are in that entry; source assignment and all original evidence stay unchanged. Reinitialization
and archival cannot duplicate/resurrect it. Original TASKS.md preimage is retained in the private evidence directory.
No competing backlog table/setting was present at inspection. TASKS.md becomes an index to the authoritative To-Do
store, not a second editable future-work list.

## Review and verification

Implementing-agent self-review covers the complete patch, SQL transactions/migration, archive scope/revisions,
manager-mode dispatch boundary, startup ordering/loading races, project-switch/text-selection guards and live
preservation. No independent reviewer is claimed and no sub-agent was used.

109 backend tests pass. Ruff, Node syntax and Git whitespace pass. Chromium smoke, chat reliability faults,
project UI/viewport, global recency/restart and dedicated startup/To-Do suites pass. Focused checks cover SQLite
reopen, project isolation, stale revision/batch rollback, attempted task-ID deletion rejection, audit preimages,
manager recording in Conversation/Auto/Execute with no tasks/executions/claim, migration idempotency, reload and
fresh-session selection, recent global/no projects, delayed startup, selected-context zero/nonzero badges,
editor/context changes, delayed old responses, polling selection/focus and responsive 1440/393/320 widths.
Physical Android/iOS keyboard behavior and live inference acceptance were not performed. Existing Starlette/httpx
deprecation warning remains; dependencies are unchanged.

Online production snapshot rehearsal adds only one linked To-Do, its migration event and schema11 marker;
all original rows across 25 tables remain unchanged (128340 original rows at snapshot). Integrity/foreign keys,
fresh-destination restore and repeat initialization pass. Complete application lifespan/HTTP/auth/assets were
rehearsed on a copy with scheduler/repository discovery disabled so active production claims/native turns could
not be reconciled or dispatched. These are migration/startup checks, not a production recovery acceptance claim.
Verified manager/native-history backups retain all 78 referenced native rollouts, with zero missing. Active files
are verified prefixes, not a synchronized stopped snapshot. Paths/hashes are in migration/native-backup receipts.

## External activation and rollback

The releasing worker's native PID 132017 is a direct child of manager PID 123727 inside
`linko-bot-manager.service`, whose KillMode is control-group. Only this unfinished execution/claim was found. Final inspection also found one queued assignment, which was
preserved and adds an idle gate; zero pending questions/deliveries were present. Recheck all queued work,
questions/deliveries and recovery eligibility immediately before cutover.
No supported external operator/dispatch-quiesce tool is available to this session. Stop/restart here would kill
this worker. No service escape, detached restart, unit change, live asset edit, task-state manipulation or production
schema initialization was attempted. Deployment authorization is already present; no new permission is needed.

Use an operator session **outside** that cgroup after this worker and manager result-reporting turn finish.
Follow docs/operations.md plus the combined reliability receipt-aware rollback restrictions in docs/chat-history.md.
The exact candidate head, patch hashes, base/dirty-file checksums and fresh idleness findings are in release.json.

1. Verify `/proc/$$/cgroup` is external; check candidate/source hashes and Git status. Candidate is the standalone
   `/home/codex/checkouts/linko-startup-todo-20261012`. If live HEAD or unrelated source changed, recompose and review
   the patch against it; never overwrite other releases. Arrange a quiet submission window and preserve tab drafts,
   request IDs and ambiguous legacy submissions. Check every unfinished manager/worker execution, task lease,
   queued task, pending question, pending delivery and recovery-eligible record; wait for idle.
2. Take fresh verified manager and native SQLite/rollout/config backups. Repeat schema11 migration/startup rehearsal
   on copies. Preserve authentication, global settings, conversations, source files and native references. The
   preparation backup is evidence, not a final cutover snapshot.
3. Stop only the existing dedicated user service. Recheck persisted idleness and take a final manager snapshot.
   If new work appeared, start the original service and defer. Preserve the dirty deferred TASKS.md note by copying
   it privately and comparing it with the recorded preimage. Only when byte-identical and already preserved/migrated
   in the candidate, restore that single tracked file to the base version to permit the local fast-forward; never
   reset other files. If it changed, integrate the new note into the migration/link and retest first.
4. Fetch the candidate locally, fast-forward live main to its tested receipt commit and start the same user service
   with its existing .venv/environment/unit/listeners. Do not use git push or install new dependencies.
5. Verify service PID/start time, scheduler, loopback/private HTTPS, protected-route 401 boundary, exact served
   assets, schema11/integrity/foreign keys, all original rows/session/native references and the one linked note.
   Poll for queue errors. Verify disposable authenticated browser behavior rather than create production work.
   Link actual deployment evidence to this assignment and update the truthful activation status.

Rollback should prefer a focused code fix. Schema11 is additive; base code can read it, but hides To-Do and lacks
future-recording instructions. Preserve the new table/edits/archives/audit and request receipts; never drop/downgrade
or restore an old backup over new work. Do not run receipt-blind schema9 code. An idle rollback to the exact schema10
base needs the same external cutover/backup procedure and an explicit pending-To-Do operational status. Existing
native instructions may persist until a new compatible manager thread, so prevent new submissions during rollback
and arrange forward repair before normal future-work use. Copy restore was rehearsed only to a fresh destination.

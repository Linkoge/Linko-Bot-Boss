# Per-request parallel coding — owner-approved contract, 2026-10-14

This supersedes unconditional one-worker-per-project requirements. Architecture remains
User → Manager → native Codex workers → Manager → User. No planner/router, DAG,
independent reviewer, integration controller, recurring AI review or additional routing
inference is introduced. The existing manager assesses the request during its normal turn.

Ordinary requests execute sequentially per project with FIFO admission. An explicit owner
instruction in the current chat request or expanded composer **⇈ Send** grants permission
for that request only. It never changes a persistent setting or silently upgrades an existing
assignment. Refinements pause/continue the existing task, preserving its objective, acceptance,
worktree, branch and native thread. Parallel permission may queue rather than overlap.

`POST /api/chat` accepts `parallel: boolean` (default false). Accepted UI consent is retained
as `execution_parallel:<manager_execution_id>` with source/project/execution identity, in the
same transaction as messages, queue and request receipt. The true flag participates in the
receipt hash; ambiguous browser retries retain the exact flag and request ID in sessionStorage.
Ordinary Send after acceptance defaults false. Internal result/recovery turns and Conversation
mode cannot create permission. Attachments and historical owner text never authorize it.
The normal manager prompt exposes the current request's permission, including an empty value
to clear any previous-turn inference. Explicit chat consent uses `delegate.owner_parallel_quote`,
validated against the current execution's actual owner message, project and conversation;
negative/conditional language is rejected conservatively. Unsupported phrasing can use ⇈ Send.

Manager `delegate` supplies `change_paths` (concrete relative file/directory scopes, no globs),
`depends_on` (existing same-project task IDs), `parallel_safe` and `parallel_reason`.
Backend transactional admission enforces active project status, recovery/account gates,
global capacity, archived/owner-held task states, existing leases and integration/preparation
reservations. Simultaneous assignments need real isolated workspaces, both manager assessments
safe, known disjoint scopes and completed/integrated dependencies. Unknown or overlapping scopes,
shared checkout ownership, unsafe shared operations and incomplete dependencies queue with a
retained reason visible in Tasks and manager read_task/list_tasks. Ordinary FIFO is retained;
explicit independent opt-ins may pass queued ordinary work. Model selection remains the existing
manager/manual policy; no new inference selects the execution mode.

New assignments on a clean committed Git repository get deterministic private worktrees under
`AI_MANAGER_DATA/assignment-workspaces/<task_id>`, branches `linko/assignment-<task_id>` and a
fsynced workspace receipt. SQLite tasks retain `workspace`, `workspace_branch`, `workspace_base`;
each execution copies worktree/branch ownership. Worktree creation interrupted before SQLite
commit reuses the receipt and existing branch without resetting anything. Existing native threads,
legacy assignments, non-Git and dirty ordinary projects keep their original sequential cwd.
Dirty/non-Git/historical opt-ins cannot create unsafe parallel writers. Missing or changed retained
workspaces/branches block recovery/dispatch; there is no fallback to the shared checkout.
Schema12 adds those three nullable columns without rewriting old records. `project_worker` stays
unique for shared running assignments; `workspace_worker` uniquely protects leased isolated paths.
Application admission also protects waiting/requested-cleanup leases transactionally.

Pause/cancel/restart/recovery retain the exact assignment, workspace, branch and native thread.
Existing receipt-proven process cleanup, launch/turn deduplication, account backoff, unanswered
questions, native outcome inspection and global capacity continue. Cancellation retains files and
branches and remains terminal. Resume does not acquire new parallel permission or new source scope.
Owner/project holds never turn into authorization through internal results. Backups must retain the
private worktree directories/receipts plus registered repositories, Git refs, SQLite and native history.

Workers edit/commit only their retained workspace/branch. Their instructions require disposable
runtime data/ports/output, scope discipline, no shared runtime DB/service writes, shared checkout
editing, branch deletion, deployment or push. Same-user native workers still have broad access;
worktree/admission restrictions prevent application-created competing writers, not malicious shell
access. They cannot guarantee arbitrary detached effects or infer semantic dependencies missed by
the manager. Unexpected actual conflicts are retained and reviewed sequentially.

After clean committed sequential completion, the backend retains an actual Git candidate for the
next ordinary isolated task; the shared checkout stays intact. Concurrent/uncommitted source results
stay pending and cannot disappear from later work. `integrate_assignment(task_id, task_ids=[...])`
appends integration/combined-validation instructions and resumes ONE existing isolated assignment
in its original worktree/thread. All sources must be same-project, completed, committed and stopped.
A global durable `assignment_integration` reservation serializes integration; all project writers
must be cleaned up before reservation. Same-project new dispatch waits while it is reserved;
other projects can code within global capacity. Pause/cancel release only after confirmed cleanup;
explicit resume reacquires the reservation with the original pinned source commits and partial merge.
A missing reservation fails closed. Source branches are never automatically deleted or reset.
The existing native worker merges/reviews and validates the complete combined candidate; no new
merge AI/controller is introduced. Actual clean Git state and ancestry of pinned sources are checked
before publishing `project_candidate:<project_id>` and marking sources integrated. Native completion
alone does not prove a merge, combined validation, preparation or deployment.

Release preparation holds a global file lock and a receipt-bound durable `release_preparation`
reservation, mutually exclusive with integration reservations. An exited preparer's stale record is
removed only after process-start identity inspection; failed immutable artifacts remain available.
The deployment helper retains its complete global idle/drain/backup/auth/data/asset verification gates.
The schema12 bundle requires the reviewed, hash-pinned helper v2 artifact; installed v1 refuses it.
See [deployment contract](DEPLOYMENT_HELPER.md). Neither helper upgrade nor app deployment is performed
by this implementation task. Exact candidate/readiness/status/evidence: [STATE.md](../STATE.md).

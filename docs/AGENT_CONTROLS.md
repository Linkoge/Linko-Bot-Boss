# Integrated activation status — 2026-10-13

Current integrated activation target: `integrated-recovery-append-popup-v1-20261013`; see [STATE.md](../STATE.md).
The owner has authorized combined deployment. Manager review and controlled `deploy_release` remain;
the worker does not deploy or restart. Exact manifest establishes preparation; only a matching later
succeeded receipt establishes activation. Original source reports and candidate IDs below retain their
historical scope and evidence; their prior requirement for a new owner request has been satisfied.
Full integration report: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md`.

# Agent Pause / Cancel and bundled updates — prepared, 2026-10-12

Implemented and verified in the isolated checkout
`/home/codex/checkouts/linko-agent-controls-20261012`. Exact local release:
`agent-controls-v1-20261012`. Activation is not requested or performed.

## Owner controls

Tasks places **Pause** and **Cancel** beside the working assignment's creation time and History.
Agents exposes the same controls on its current coding execution. Manager responses retain
**Stop reply**, which only interrupts that response. Paused Tasks expose **Resume**; cancelled
assignments retain History and execution output without a resume button. Controls bind to the
selected project snapshot and use assignment revisions to reject stale changes.

A working pause transitions `running/waiting → pause_requested → paused`; cancellation uses
`cancel_requested → cancelled`. The request is durable before any signal or native RPC. A paused
or cancelled state is confirmed only after the owned App Server and its dedicated process group
close. The project lease prevents resume or another coding worker during that cleanup. Requested
states remain visible while cleanup is pending, including after disconnect/restart. Agents counts
work while it is still running, and stops counting when the native checkpoint or forced cleanup
begins. Queued/unleased work can pause/cancel immediately because no worker needs termination.
Resume queues the same task ID with a new attempt and its retained native thread.

Pause requests native `turn/interrupt` immediately in a background control job. The RPC has a
one-second deadline, followed by at most one second waiting for the runner's checkpoint. It starts
no additional inference or substantial work to prepare a checkpoint. If native interruption stalls,
it cancels the runner, closes the owned group using SIGTERM and a five-second SIGKILL fallback,
and records **Paused after forced interruption**. A native checkpoint and a forced interruption
have separate durable events. This checkpoint means a retained native turn boundary and stopped
owned writer, not an atomic checkpoint of arbitrary shell commands or external side effects.

Cancel sends SIGKILL to the receipt-matched owned process group directly in the control request,
before any cooperative RPC or task-completion wait. Async cleanup then confirms termination.
Cancel can supersede Pause, including while cleanup is already underway; completion cannot
overwrite an accepted control. Cancel is terminal and does not delete files or execution records.

Measured real installed-Codex acceptance, interrupting an active 60-second disposable command:
Pause confirmed in **0.046 seconds**, Cancel in **0.055 seconds** (request handling 0.002/0.010 seconds).
Both stopped the command and retained its partial file. Native resume completed added instructions
in the same assignment/thread without rerunning that command. An intentionally unresponsive native
RPC fixture confirmed pause in **2.02 seconds**, cancel in **0.006 seconds**. These are observations,
not an SLA: scheduler/network/OS delay and the five-second process-cleanup grace can add latency.
An unprovable process identity retains exclusivity and requested state rather than claiming success.

## Partial work and recovery

Files, Git changes/commits, retained workspaces, native history, task acceptance/context, streamed
output, usage and execution events remain available. No reset, stash, rollback, deletion or cleanup
commit occurs. An interrupted tool may leave incomplete edits, builds, writes or external effects;
review partial files and recorded operations before resuming. Buffers not yet written to disk/native
history cannot be recovered. Detached commands outside the owned process group remain a documented
native-worker limitation; neither button promises transactional undo or universal descendant control.

The existing integer `executions.stop_requested` is extended without a schema migration:
`0` normal, `1` legacy Stop (waiting), `2` Pause, `3` Cancel. Existing deliberate waiting/cancelled
states, historical rows and native recovery behavior remain intact. Requested stops bypass retry
backoff. Restart never replays them; it first reaps receipt-proven owned processes, then confirms
the appropriate disposition. SIGTERM-resistant orphan groups escalate on the next pass; Cancel
uses SIGKILL immediately. Uncertain ownership stays leased. Pending questions expire on request
and no late answer can grant execution after a stop request.

## Manager brainstorming and instruction bundling

Pause/append is reserved for direct corrections or close refinements of the running assignment;
other work gets a separate assignment, queued when edits conflict. Initial requests may contain
multiple requirements. The authoritative rule is `MANAGER_INSTRUCTIONS` in `ai_manager/codex.py`;
`Chat._run` loads it as native developer instructions and hashes it with the manager tool schema.
After deployment/restart, a changed hash starts a compatible native thread on the next manager turn,
including existing conversations; fresh conversations load it directly. Source edits alone do not
update the running service. This change preserves the pause/cleanup/resume safeguards below.

Conversation mode stays read-only. In Auto/Execute the manager can call `pause(task_id)` while
brainstorming, report the requested state, and retrieve the assignment until `paused` with no
lease. Owner-authorized added instructions then use `continue(task_id, objective, acceptance,
constraints, dependencies, selected_context)` on that assignment. Here objective/acceptance are
**additions**: prior objective and acceptance remain, and selected context/attachments merge with
stable deduplication. Overflow rejects the complete update atomically without dropping requirements.
The queued turn is one bundled assignment. The worker inspects partial work before repeating actions.

A repeated manager continuation within the same response is idempotent even with a regenerated
call ID. Active/requested/queued/cancelled tasks reject continuation; stale revisions reject changes.
Exact repeated delegation also reuses deliberately paused unfinished work, and delegation carrying
an existing task ID directs the manager to continue instead. Semantic relatedness is a manager
judgment, not a new policy engine. A deliberate owner pause/stopped result does not authorize automatic
continuation; explicit resume or an owner-authorized bundled update is required. Project pause retains
its separate meaning: prevent future project dispatch, without cancelling current work.

## Validation and release evidence

Focused tests use real disposable process groups plus native-history fixtures, covering requested
versus confirmed state, bounded unresponsive pause, immediate cancellation, native/thread/progress
preservation, cleanup and completion races, restart/orphan/child cleanup, no duplicate workers,
idempotent bundling, atomic overflow, revision/mode/project guards and pending-question controls.
Real subscription acceptance and Chromium at 1440/393/320 px cover active commands, resume, Tasks
and Agents controls, retained history and responsive layouts. Existing backend/security/browser
checks remain part of fixed local preparation. No production acceptance task or signal is used.

Full report, logs, native receipts, screenshots, manager brief/recent-delivery facts and immutable
prepared manifest/bundle references:
`/home/codex/.local/share/linko-bot-manager/deployments/agent-controls-20261012/REPORT.md`.
Original To-Do `5bdfa1dc8b7f42a6a766ab9f99574935`, source conversation
`f305c9fa998441fe89a3b369e4779777`, assignment `a01b6c008c19464f883d5709937fc906`,
execution `2c8c18e89e4d4f2882823f171be7d034`. The owner's explicit scheduling supersedes
the To-Do's earlier deferral; its original record/source are preserved.

The succeeded baseline receipt is
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/a9ef45440d33ae7a692f825249291e41.json`
for `chat-ui-mobile-composer-v4-20261012`, commit `00fb20f456c924bc2b787e6dbce7a6cdec729c75`.
It does not establish this candidate's deployment. No GitHub push, deploy request, service restart,
production data migration or activation is authorized by this development assignment.

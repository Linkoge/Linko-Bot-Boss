# Compact project memory — prepared, 2026-10-12

Activation is pending. This delivery implements the owner's explicitly scheduled memory design;
it does not authorize deployment or a full review of any registered project.

The manager uses one derived, persistent brief: purpose, goals, constraints, established state,
active decisions with reasons, unresolved work, retrieval references and three recent deliveries.
Existing authoritative repository documents, saved owner notes, transcripts, assignments and
full execution evidence remain authoritative/retrievable. The brief is a current navigation
representation, never a competing specification. Global owner communication instructions stay
in the existing shared setting and apply to fresh manager conversations.

## Bound and initialization

The target is 1500 tokens and the complete default project context has a hard 2000-token ceiling,
excluding global instructions and conversation content. With no runtime tokenizer dependency,
compact UTF-8 byte length supplies a conservative byte-level BPE token upper bound. A brief has
at most 1600 bytes; project identity, its active reference and compact deployment status must fit
inside the 2000-byte envelope. This deliberately underuses the usual English token allowance;
these are upper bounds, not actual token estimates. Actual native review usage appears only when
reported. Model/harness instructions and existing current-chat history are outside this memory
budget; separate conversation compaction is future scope.

Overflow rejects atomically and leaves active memory/evidence intact. Condense wording with
explicit retained-item mappings and existing scoped source references. Never silently remove
requirements. Completed pending work needs an explicit resolution, reason and durable source.
Full prior text and these mappings remain in immutable versions. If this conservative bound is
insufficient, report the gap rather than truncate requirements or run an automatic review.

First access cheaply seeds from the established project description/objective/success criteria/
instructions; it explicitly marks capabilities as unreconciled. Oversized fields remain complete
in the initial version, with mandatory retrieval notices in the brief. No inference or repository
inventory starts automatically. Authoritative docs and stored notes are retrieved when planning
requires them. Project field changes are not an automatic reconciliation trigger.

## Routine delivery

Assignments carry the objective, acceptance criteria, relevant constraints/dependencies and selected
context. Workers inspect relevant code/docs and retain repository/security instructions; they receive
neither the whole brief, bulk owner notes nor the manager-only global communication preference.
Worker completion instructions require durable detailed evidence, relevant authoritative-document
updates, established facts/effects, pending work, verification and deployment receipts. The manager
uses `read_memory`, `update_brief` and `record_delivery` for small updates; `list_memory`, paginated
`read_document`, `read_task`, `list_tasks`, `list_history` and explicit `read_history(conversation_id)`
retrieve the underlying sources. Archived chats never enter fresh-chat defaults.

Every recent entry has a stable delivery ID, date, change, effect, verification, deployment status and
full-report reference. Related fixes reuse the delivery ID and retain every evidence revision.
The fourth delivery moves the oldest evidence reference into the version's archive navigation;
its lasting established effects and unrelated pending work remain current. All old versions and
complete reports remain in private SQLite `memory`; settings hold only the project control index.
No schema, dependency or authoritative-document copy is introduced.

Deployment states are `not_requested`, `pending`, `failed`, `deployed`. Pending/failed activation
adds durable pending work. For this manager, `deployed` requires an exact succeeded independent-helper
receipt scoped to its configured repository; requests or worker prose do not satisfy the gate.
Receipts must match the exact delivery/release ID; an older successful release cannot establish a
new deployment. Other projects can use a retained scoped JSON memory receipt (`memory:<reference>`)
with project ID, succeeded status, exact release ID, completion time and verification evidence.
This is retained worker evidence, not an independent verification controller; unrelated projects
cannot substitute the manager's receipt.

## Owner-controlled review

In a selected project's Memory view, **Review project memory** explains scoped source/evidence review,
unknown cost and potentially significant time/allowance use. Confirming starts one explicit review;
there is no timer, recurring rebuild, completion-triggered full review or automatic restart of a failed
review. Progress, inspected references and actual reported usage (or unavailable) are visible.

The read-only App Server has native shell/editing/apps/plugins/subagents/web disabled. Its sole
review tool retrieves eligible code/docs inside the registered root, scoped project/task state and
selective historical evidence. Hidden/secret/credential files, symlinks, dependency/vendor/build/data
folders and unrelated project rows are excluded. Inventory is bounded at depth eight/5000 files and
reads at 100 KB with pagination; excluded/uninspected material can remain an unresolved coverage gap.
No database dump, global settings, account credentials or unrelated conversations enter review context.

The active version remains unchanged while the candidate, old/new diff, references and mismatches are
stored. Removed/changed goals, constraints, established descriptive facts, decisions and unresolved work are automatically surfaced
as conflicts even if the reviewer omits them. The owner supplies a reason and chooses Keep existing
requirement or Accept proposed change. Other ambiguities need an explicit owner resolution before
Apply. Missing code is never automatic permission to erase an intended goal. Apply checks the current
revision/base, preventing a stale candidate from overwriting a newer delivery. Keeping facts can still
exceed the budget, in which case Apply rejects and the old version remains active.

Prior versions are inspectable and rollback creates another retained version. Failed/cancelled/interrupted
reviews preserve active memory and partial evidence/usage; restart marks interrupted reviews failed
without spending allowance again. Deployment acknowledgement waits for active review jobs, and review
cancellation remains admitted during a deployment drain. UI selection guards reject stale cross-project
responses/actions. Full review does not mutate authoritative source documents; owner-approved facts become
the derived representation, with source changes handled as separate ordinary work.

## Validation and release

Meaningful tests cover fresh goals/state and shared preference, archived exclusion/explicit retrieval,
fourth-entry archive/effects/full evidence, related fixes, pending/failed/succeeded deployment truth,
30 deliveries with bounded defaults, atomic overflow, scoped condensation/resolution provenance,
focused worker acceptance/context, candidate/diff/apply/keep/rollback/staleness, failure/cancellation/
restart, source path/privacy controls and HTTP authentication/project scope. Chromium checks explicit
start, usage/diff/mismatch resolution, Apply/reload/rollback, selection races/global context and desktop/
393 px/320 px layouts. The preparation CLI includes this workflow and JavaScript syntax validation.

Detailed evidence and exact prepared release references:
`/home/codex/.local/share/linko-bot-manager/deployments/project-memory-20261012/REPORT.md`.
Existing deferred To-Do and original source task remain linked, preserving the original recording and
all subsequent conversation refinements. No remote push, service restart or release request is part of
this delivery. The live checkout was restored after an initial editing-location mistake; the isolated
candidate and preservation receipts are documented in the private report.

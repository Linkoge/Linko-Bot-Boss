# Whole-message Copy and Delete — retained assignment report

Assignment: `978211a9c69a48dfa9347b3620b4db1b`  
Branch: `linko/assignment-978211a9c69a48dfa9347b3620b4db1b`  
Base: `1406e396b001052e1ad5f6a9cec1dbad30adedd4`  
Source: To-Do `fc506b0047394f5fb0d1c64a90b957ad`, observed revision 1.

## Scope and preserved source

The owner's latest explicit scheduling supersedes execution of the earlier bundle. Only whole-message
Copy/Delete is implemented. Quota recovery, hybrid picker, usage/reset display and Tasks selection are
unscheduled in this run. Their original descriptions/handoffs/source links, including screenshot
`attachments/ed907fc52a7a4801a8ffe18ecd833db9.jpg`, remain historical sources, unchanged.
Initial inspection found this assignment clean at the base, with no partial source edits to replay.
No worktree switching, source merging or shared runtime mutation occurred.

Original selected source title: **Whole-message Copy and Delete controls**.
The source details below are preserved verbatim as read from the existing To-Do:

> Subtle small low-opacity lower-right expandable arrow on whole owner/assistant messages reveals Copy and Delete underneath. Copy/delete entire message and complete assistant response rather than fragments. No Edit; maintain deletion confirmation.
> 
> Restored at owner's request from held assignment 978211a9c69a48dfa9347b3620b4db1b (read_task description, four linked To-Dos). Original handoff/source records remain authoritative and must be preserved. Backlog only; do not execute or resume the held assignment. When explicitly scheduled, inspect and reuse that existing assignment to avoid duplicate implementation.

The source already had `archived_at` set when inspected. This worker neither archived it nor claims
successful acceptance/activation from that pre-existing field. Its record, revision, original task/native
history and other bundled records remain unchanged; the application owns successful acceptance disposition.
The scoped source snapshot and full current assignment description/acceptance are retained in
`.data/message-actions/source-records.json`, read using SQLite `mode=ro` only.

## Implemented behavior

One native details/summary disclosure after all owner/assistant text and attachments, in both current
Chat and the saved/archive reader. The arrow is small and opacity .32 when idle; hover/focus/open restores
visibility. The native disclosure supports keyboard operation. Copy/Delete appear underneath. No Edit.
Copy uses the complete stored source text (all received text while streaming) with existing clipboard
fallback, independent of selected/rendered Markdown fragments. Delete uses the existing modal confirmation
with Cancel initially focused. Queued/live/recovering messages and their corresponding owner messages
must finish or be stopped before deletion; UI and backend both enforce this.

The strict confirmed owner/CSRF endpoint checks exact project/conversation/message membership. Atomic
SQLite settings tombstones and audit events remove whole display messages from Chat, saved/archive
readers, search counts/results and Copy all/Copy five exports. Retry is idempotent; restart retains deletion.
Existing task/execution/native/attachment evidence and provider prompt context are preserved. No schema,
provider routing, queue/recovery, Send, deployment-policy or security-policy changes. Polling removes deleted
IDs without treating the rolling 200-message tail as a deletion list. Clear/archive/trash tail checks use
the remaining visible tail. The authoritative behavior contract is updated in `docs/chat-history.md`.

## Verification and durable evidence

- Targeted: 13 passed (four new message-action cases plus nine existing history cases).
- Fixed smoke: 46 passed, 517 deselected at that run, within its 20-second budget.
- Chromium `scripts/browser_markdown.py`: full streamed multi-block Markdown with one control, raw-source
  owner/assistant Copy, no Edit, Cancel makes zero deletion requests, confirmed whole-response removal,
  saved-reader removal, reload persistence, owner deletion, keyboard and 320/393/1440 widths. Existing
  1552-prefix security/selection/stream/saved-reader checks remain.
- Ruff, JavaScript syntax and whitespace checks are retained in `.data/message-actions/`.
- Final backend run/result and exact committed candidate are recorded in
  `.data/message-actions/manager-summary.json`; full output is `full-backend.log`. This final run is
  executed once after source/test/document edits; no release-preparation or deployment success is inferred.
- An upstream Starlette/httpx deprecation warning is non-fatal and unchanged.

Execution logs include the initial test's incorrect HTTP-400 expectation; the application correctly returns
409 for rejected mutations, and the test now asserts its established contract. Initial fixture-import lint
warnings were corrected by fixture registration. Browser checks passed before and after the final UI polish.

## Handoff and lasting effects

Implemented and verified in this retained branch. Exact candidate and evidence hashes live in the adjacent
private manager-summary; source, tests and authoritative docs are committed. The application must merge and
perform its existing automatic exact-candidate preparation/activation after normal completed-job handoff.
No shared checkout/database write, push, service restart, publication, deployment or receipt fabrication.
No activation receipt exists for this change from this worker; deployment remains pending. Internal evidence
retention means Delete is not provider-side erasure. Physical mobile touch/clipboard behavior is not claimed;
Chromium responsive and keyboard behavior is verified. There is no overnight/quota bootstrap claim because
recovery work was explicitly excluded by the owner's latest scope.

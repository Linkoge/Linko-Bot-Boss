# Manager communication and internal delivery — 2026-10-12

Deployment handoff candidate (2026-10-14): persistent tool/instruction contracts now record actual
owner intent before asynchronous work and expose compact unused-intent references to the returning
manager. Internal results may review/consume that fixed prior intent only. Later queued owner
corrections hold consumption; no old owner message is backfilled. Existing transport filtering,
read_result evidence retrieval, lean ordinary prompts and compatibility-based future thread rotation
remain. [Exact contract, pending activation and bootstrap limits](DEPLOYMENT_HELPER.md).

The global owner preference in SQLite `settings.global_instructions` remains the shared,
editable communication authority for all projects and fresh conversations. Routine owner
completion/status replies must be extremely brief and natural; normal explanations and
requested detail remain supported. Full technical evidence stays in internal durable records.
Blockers, incomplete verification and pending activation must never be hidden.

## Established cause

`Chat.deliver_results` previously constructed `Codex result:\n` plus JSON containing the
original worker objective, up to 24,000 trailing output characters, task/execution status,
blocker and error. `Chat.submit(delivery=...)` inserted it as a `worker` message with stable
ID `result:<worker-execution-id>`, alongside a manager assistant response. The manager prompt
received the same raw text. `app.js` rendered every message, labelled the worker row
“Codex result”, and rendered its content verbatim. Current history, saved history and Copy
all/five thus exposed the transport independently of the manager's natural-language reply.
A fresh native thread also replayed those rows in its latest-16-message history, retaining
8,000 trailing characters per non-user row. Resumed native threads retained previous inputs.

Read-only production evidence identifies the owner's example as worker execution
`93760722a8c845729ffe2ccd5fe435ba`, task `2b32890ed14e42b6aea43091c195b3a7`,
stored worker message `result:93760722a8c845729ffe2ccd5fe435ba` (3,776 characters), linked
to manager delivery `826a1acb12cc412e89947777a4b7569d`. This was application-created
transport, not an owner-authored request or a manager-generated report. An owner message
quoting the issue also exists and is preserved. Result-shaped incoming events are not
permission grants and must not be attributed to the owner without provenance.

The earlier private `MANAGER_COMMUNICATION_REPORT.md` accurately documented persistent
preference loading but did not address transport rendering or bulk result injection.
Its runtime receipts are historical, not current deployment evidence. `MANAGER_INSTRUCTIONS`
required reporting actual result/status and durable evidence; worker instructions required
reports, authoritative updates, verification and deployment receipts. None required publishing
raw payloads. The saved preference already prohibited routine technical dumps. Project-specific
instructions for this manager were empty. Therefore persistence of the preference was not the
missing piece: application delivery and history projection bypassed it.

## Persistent remedy (candidate code)

Automatic delivery stores a compact internal notice with exact task/project/execution IDs,
actual attempt status and current task status, bounded blocker/error with explicit truncation
flags, output length and an evidence retrieval action. It omits objective and output. The full
worker input, output, errors, native references, task evidence and old messages/events remain
unchanged in SQLite. No schema/dependency migration or production transcript rewrite occurs.

`manager_action.read_result(execution_id, project_id, offset)` retrieves the exact attempt's
retained input/error and output in 12,000-character pages, with a continuation offset and
execution evidence references. Project scope is enforced, including All Projects selection;
read access is permitted in Conversation mode. `read_task`, `read_memory`, explicit
`read_history(message_id)` and existing deployment status remain available. Managers retrieve
relevant evidence before declaring acceptance, continuing work or recording a delivery.
Status `completed` alone is never proof of acceptance or activation. Full technical evidence
is not automatically copied into routine defaults.

Owner history/current/saved-history/search/counts and fresh native thread history omit only
proven application transport: role `worker`, ID `result:<id>`, and an existing task-linked
source execution. Text and JSON shape are never classification criteria. Genuine user JSON,
code, quoted reports, real assistant replies and unproven historical rows remain. Historical
raw rows stay in audit storage and can be explicitly retrieved internally. The UI's existing
copy and archive views consume this projection, so raw payloads do not enter normal exports.

Manager thread compatibility now hashes persistent manager instructions together with the
tool schema. On activation, the next newly assembled turn rotates older contaminated native
threads and rehydrates actual conversation content without transport. Original native threads
remain retained for audit. Interrupted turns are inspected/reconciled by existing recovery;
new delivery prompts are reconstructed from the source execution even when an old manager
execution input contains raw text. Already running native turns are not rewritten or killed.
Full reconciliation remains an explicit owner action; brief, recent-delivery, source and
provenance constraints remain intact. This is not a deterministic filter of model-written
assistant prose; persistent instructions govern natural summaries and requested detail.

## Compatibility, verification and activation

Both linked audio/composer assignments are completed; their candidate is prepared, not live.
This isolated candidate descends from `audio-composer-v3-20261012` commit
`80ef8c0cddc77508006961ccb805544c9c35175e`, preserving the requested 20-minute recording,
longer upload/transcript support, removed composer chrome and elapsed `00:00` timer.
Their checkout, prepared artifacts and succeeded older audio/key-load receipts remain intact.
No competing composer implementation or active worker interruption is introduced.

Prepared combined release ID: `worker-delivery-audio-v1-20261012`.
Checkout: `/home/codex/checkouts/linko-worker-delivery-20261012`.
Sanitized detailed evidence and preparation receipt:
`/home/codex/.local/share/linko-bot-manager/deployments/worker-delivery-20261012/REPORT.md`.
Release manifest and validation log are retained under the same exact ID in
`deployment-helper/releases/`. The helper enforces the live base and preserves data/settings.
A changed live base requires reviewed re-preparation, never bypassing that check.

Meaningful verification covers historical projection without data mutation, literal owner
JSON, archived exclusion, database/Chat reopening, compatibility-driven native-thread rotation,
compact failed notices with truncation flags, exact scoped paginated retrieval, concurrent
delivery/idempotency, manager outage/retry, same-task continuation and five ordered assignments.
Existing memory/deployment tests verify exact succeeded-receipt gates. Chromium checks current
chat, reload and saved-history projection with real authentication and disposable storage.
Release preparation also runs the full Python suite, Ruff, JS syntax and existing audio,
memory, fetch, smoke, deployment and usage browsers. No paid inference/provider call is needed.

The shared preference is strengthened immediately through the existing setting; code/UI and
context changes require authorized deployment. No deployment request, restart, cutover or new
succeeded receipt is created by this task. Keep implemented, verified and deployed distinct.

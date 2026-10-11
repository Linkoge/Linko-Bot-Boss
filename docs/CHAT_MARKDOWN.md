# Chat Markdown preparation — 2026-10-12

Assistant and worker replies now use one Markdown token-to-DOM renderer in live
streams, saved history, reloads and archive/search details. User messages remain
literal text. Supported syntax includes paragraphs, emphasis, nested/ordered lists,
links/autolinks, inline/fenced/indented code, headings, block quotes, separators,
strikethrough and tables. Images appear as alt text without fetching remote assets.

The pinned local MIT markdown-it 15.0.2 browser asset parses with HTML disabled.
The small application renderer creates only allowlisted elements and attributes;
it never inserts message HTML. Links must resolve to HTTP, HTTPS or mailto.
Code and raw HTML are text nodes. No highlighting plugin, CDN, runtime package
installation, backend/schema change or Markdown storage transformation is needed.
Parser provenance/license are retained beside the vendored asset.

Message source strings remain authoritative for SSE reconciliation, persisted
history and whole-chat copying. Existing cursor/context/request guards and scroll
follow decisions remain unchanged. Unchanged polls preserve DOM nodes. A selected
message defers replacement and applies the latest pending source when selection
clears; a reset back to displayed source discards stale pending replacement.
Code blocks and overflowing tables support keyboard focus and horizontal scroll;
mobile message columns stay constrained. Incomplete Markdown is rendered according
to parser rules on each accepted delta and converges to the same final DOM.

This patch is based on the complete prepared startup/badge/To-Do candidate for
assignment fdcc3ac0e34444149d7f1fffaf017d3f, preserving that implementation and its
release instructions. The original live checkout and original To-Do checkout are
untouched. It is a combined candidate, not a competing deployment. No remote push.

Implementing-agent self-review covers the complete focused diff, parser provenance,
DOM/URL allowlists, source cache/selection lifecycle, copy/scroll/SSE guards, saved
reader parity, keyboard and responsive styling. No independent agent review is
claimed. All 109 backend tests pass; dedicated Chromium checks cover all 1552
character prefixes, actual partial SSE and final reload/saved-reader equivalence,
XSS/unsafe schemes, escaped copyable code, selection/reset/unchanged polling,
no unsolicited network requests and desktop/mobile/reduced/narrow viewports.
Existing reliability, smoke/copy/scroll/archive, mobile project UI and startup/To-Do
Chromium suites pass. Ruff, Node syntax and Git whitespace checks pass. The smoke
selection fixture now finds its text node through a DOM walker so it tests both
plain and formatted messages. Physical device keyboard behavior is not claimed.
The existing Starlette/httpx deprecation warning remains.

The existing shared communication setting was minimally refined: only routine
completion/status reports must be extremely brief; explanations/brainstorming
remain natural and sufficiently detailed. All other instructions are preserved.
The supported setting setter was used without application initialization against
production, and a separate database read verified persistence. No restart required.

Full durable review, backups, migration/startup rehearsal, test output, screenshots,
communication preimage/diff and current live/idle-gate receipts:
`/home/codex/.local/share/linko-bot-manager/deployments/markdown-20261012/REPORT.md`.
Task/execution events link this evidence and the composed candidate.

**Activation pending:** this worker is inside the dedicated service's control group;
stop/restart would terminate the releasing turn. Live still serves the original
assets. No detached restart, service override, live asset edits or lifecycle changes
were performed. The owner's deployment authorization is retained; no repeat approval
is needed. Use the existing startup/To-Do external cutover instructions with this
combined candidate after workers and automatic manager reporting settle. Recheck
live and both candidate heads/dirty files, all executions/leases, eligible/queued
work, deliveries and questions; take fresh manager/native backups and repeat additive
migration/startup rehearsal; immediately recheck idle state before external stop,
final stopped snapshot, local fast-forward and same-service start. If live changed,
recompose/review/test rather than overwrite it. Preserve the recorded dirty TASKS.md
preimage under that release's exact byte-match gate.

Post-cutover verify PID/start time, scheduler, schema11/integrity/foreign keys,
preserved sessions/settings/history/native references/To-Do link, exact served assets
including both new scripts, private HTTPS/auth boundary and disposable Markdown,
reliability and To-Do browser fixtures. Record actual activation on both assignments.
Until then deployment is pending. For rollback retain accepted chat receipts and
To-Do rows/audit; prefer a focused renderer revert/fix under the same external idle
procedure. Never restore an old preparation backup over newly accepted work.
Memory architecture/archival changes remain deferred.

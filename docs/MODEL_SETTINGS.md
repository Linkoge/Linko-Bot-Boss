# Manager and bot choices — 2026-10-13

Current deployed release: `model-settings-manual-scroll-v2-20261013`, commit
`0745c1a6af91f09d62f414c0c63326d2afe82260`, proven by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/c9a21304d22c36785e25b66f914ec90b.json`.
The prepared navigation/audio successor moves the gear to the upper row beside the project
dropdown, with matching chat-utilities/usage columns below; see [audio](AUDIO.md).
That successor is not deployed. All settings behavior below is preserved.

The fourth 44px navigation utility opens one responsive modal. Manager has two exclusive
choice rows and no policy/mode controls. Bot has Manual/Automatic and three vertical model
sections. Each section has manual model/level choices followed by independently selectable
checkmark/X policy states for that model and each level. Manual selection remains shown,
retained and disabled in Automatic; switching back restores it. Policy stays shown in Manual
and becomes editable in Automatic. Controls save immediately, with nonvisible accessible
names/status, keyboard arrows, native Enter/Space, contained Tab/Shift+Tab, Escape, outside
dismissal and focus restoration. Conflicts and rejected empty policies leave saved state intact.
No redundant visible Model/Thinking labels. All controls fit the verified 320×568 phone,
393×851 phone and desktop viewports; shorter viewports permit ordinary modal scrolling.

## Provider and execution contract

Exact IDs are Luna `gpt-6-luna`, Sol `gpt-6.1-sol`, Astra `gpt-6-astra`. Sol denotes the
current workhorse already used by existing defaults; it does not resolve to an invented alias
or the older `gpt-6-sol`. Authenticated installed codex-cli 0.162.1 `model/list` confirms all
three support `low`, `medium`, `high`. Missing models/levels are disabled, and backend validation
uses the actual catalog. No fallback or automatic substitution. Official references:
[Models](https://learn.chatgpt.com/docs/models) and
[App Server model discovery and turns](https://learn.chatgpt.com/docs/app-server).
The complete observed catalog is retained in the private evidence directory below.

Existing manager route/default is preserved; observed production default is catalog-default
Sol/Low, with explicitly saved worker Sol/Medium. First use retains existing manual bot execution;
the new automatic policy allows supported Luna/Sol levels and denies Astra by default. Owner can
allow Astra and independently restrict levels, including Luna High only and Astra Low only.
At least one currently supported allowed pair is required in either mode.

Global `model_settings` in existing SQLite settings is canonical once saved: revision, manager
pair, bot mode, retained manual pair and policy. Revision checks, all fields and compatibility
`model_routes` writes commit atomically. Existing authenticated/CSRF/origin/CSP/deployment gates
apply to GET/PUT `/api/model-settings`; old role PUT uses the same validation and persistence.
No dependency, schema, new AI role, service or routing framework is introduced.

Before delegation the existing manager receives the current mode and allowed pairs. It supplies
`worker_route` in its existing tool call: Luna for simple well-defined work, Sol for ordinary
coding/debugging, Astra for difficult reasoning only if allowed; choose Low/Medium/High within
policy. Backend checks the exact pair, rejecting absent, unsupported or denied automatic choices.
Manual mode always uses its saved pair, regardless of a suggested tool pair. Difficulty is the
manager's judgment, not a new deterministic classifier or extra inference call.

`task.model_selected` durably records pair, mode and settings revision in the same transaction
as new assignment creation. Native thread start/resume and turn start use that pair; executions
also retain model/effort. Continue, pause and recovery retain the assignment pair/thread; changing
global manual choices affects new assignments. Every new worker turn checks catalog support and,
for an automatic assignment, current policy even after switching modes. A denied/unavailable saved
pair waits with an explicit blocker, retaining its selection and thread. Enable the pair before
explicitly continuing; no silent reroute, replay or competing worker. A running turn is not preempted
by settings edits. Recovered completed native outcomes are reconciled before any new turn as before.
Pre-feature assignments with execution records retain their recorded original manual pair; a new
assignment without a selected pair cannot dispatch under Automatic until selected by the manager.

## Verification and evidence

Backend tests cover persistence/revision conflicts, defaults, all nine model/level pairs,
unsupported catalogs, empty policies, explicit manual override, manager runtime parameters,
manager delegation, worker native start/turn parameters and denied resume without inference.
Real provider acceptance proves Sol/Low manager → policy restricted to Luna/High → manager-selected
Luna/High worker → completed result → manager delivery. Its SQLite database, native thread/turn
references and selection event are durable. This is disposable acceptance, not production activation.
Chromium verifies saves/reload, mode retention, both restrictive policies, model deny, rejected last
allow removal, viewport fit, keyboard focus and sends/streams/polling without scrolling jumps.

Detailed report, native acceptance, complete provider catalog, browser receipts and screenshots:
`/home/codex/.local/share/linko-bot-manager/deployments/model-scroll-20261013/`.
Exact release commit/base/asset hashes, validation hash and Git bundle:
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/model-settings-manual-scroll-v2-20261013/`.
A complete manifest establishes preparation; only a later matching succeeded helper receipt could
establish deployment. Physical Android/iOS behavior is not claimed by Chromium viewport simulation.
Manual scrolling behavior is authoritative in [chat history](chat-history.md).

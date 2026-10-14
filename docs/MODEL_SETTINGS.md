# Codex / Claude provider switch — succeeded live, 2026-10-14

Live receipt `deployment-helper/results/5ad868944ca0bfef14d430c5eb04566d.json` establishes
provider activation at `b0f0c8d`; the current integrated parallel/composer candidate preserves
these settings and behavior. [Current preparation/status](../STATE.md).

The settings dialog has a Codex | Claude toggle on top; the gear button shows the active provider's
logo (OpenAI or Anthropic). The active provider (`model_provider` setting) applies to **new** manager
turns and new assignments. Claude mirrors the Codex panel with Haiku/Sonnet/Opus
(`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`) and Low/Medium/High effort. Its settings are stored
separately (`model_settings:claude`). Defaults: Manager Sonnet/Low, Bot Automatic with every pair allowed.
Codex settings, including `model_routes`, are unchanged by Claude edits.

`ProviderServer` (ai_manager/providers.py) routes by exact model and thread, so pinned assignments
keep their original provider after a switch. A manager conversation started on one provider starts a
fresh native thread on the other. Claude runs through `claude_host.py`, a local process that speaks the
App Server subset this app uses (threads = Claude session IDs, turns = `claude -p` stream-json runs, history
in `<data>/claude-threads/` for `thread/read` recovery). The manager's `manager_action` tool reaches Claude through
`claude_mcp.py`, a stdio MCP bridge over a private Unix socket. The Claude manager has only Read plus that tool.
Claude workers run with `bypassPermissions`, which matches the Codex worker's full access. Usage badges follow the active
provider (Claude 5h/weekly windows come from its rate-limit events). Memory review stays on Codex.
The Claude binary is `AI_MANAGER_CLAUDE`, else `claude` on PATH, else `~/.local/bin/claude`.

# Integrated activation status — 2026-10-13

Current integrated activation target: `integrated-recovery-append-popup-v1-20261013`; see [STATE.md](../STATE.md).
The owner has authorized combined deployment. Manager review and controlled `deploy_release` remain;
the worker does not deploy or restart. Exact manifest establishes preparation; only a matching later
succeeded receipt establishes activation. Original source reports and candidate IDs below retain their
historical scope and evidence; their prior requirement for a new owner request has been satisfied.
Full integration report: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md`.

# Catalog-backed family resolution candidate — 2026-10-13

Exact prepared candidate `unfinished-recovery-family-ui-v2-20261013`; **not deployed**.
Current deployed baseline is `nav-audio-upload-v2-20261013`, commit
`08f0ba712f194ea0f985740ef408c9e033511b98`, succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json`.
Older pending/live assertions below are superseded by this observation.
Full report/catalog/native runtime evidence:
`/home/codex/.local/share/linko-bot-manager/deployments/unfinished-recovery-20261013/`.

## Deployed answer and new behavior

The deployed mapping is fixed: Luna `gpt-6-luna`, Sol `gpt-6.1-sol`, Astra `gpt-6-astra`.
It does **not** dynamically advance Sol when a newer Sol appears. At inspection, production settings
revision 13 choose Manager Sol/Low, Bot Automatic with retained manual Sol/Medium. Automatic policy
allows Luna/Medium only, all three Sol levels, and denies Astra. Recent actual manager/native settings
receipts confirm `gpt-6.1-sol`/low; the bundled worker runtime is `gpt-6.1-sol`/medium.
A fresh authenticated ChatGPT `model/list` snapshot from codex-cli 0.162.1 includes `gpt-6.1-sol`,
`gpt-6-sol`, `gpt-5.6-sol`, `gpt-6-astra`, `gpt-6-luna`, `gpt-5.6-luna`, and `gpt-5.6-terra`.
The provider cache records fetched-at `2026-10-13T08:30:12.641864813Z`; this is observed metadata freshness,
not a guarantee of immediate discovery of every future public model. All selected families support Low,
Medium and High. The official [Sol page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
and [App Server model discovery](https://learn.chatgpt.com/docs/app-server) were fetched using OpenAI Docs;
account-authenticated catalog capability data is authoritative for this app's available choices.

The candidate resolves **new** work within the explicitly chosen Luna/Sol/Astra family from the provider
catalog, without a hardcoded current version. Recognized exact IDs match `gpt-<numeric version>-<family>`;
compare numeric version components, not text/catalog ordering/default or cross-family upgrade metadata.
Only visible text-capable catalog entries with an app-supported effort participate; unknown naming,
preview/snapshot suffixes, other families and hidden entries are excluded. If a future naming convention
changes, it needs an explicit resolver update; never guess an alias or switch family. Unrecognized legacy
custom routes remain exact. Existing legacy IDs are compatibility constants for callers/fixtures, not
latest-version dispatch defaults. Unavailable families remain visibly chosen and disabled.

Retained global IDs denote family preference for new work. Reads project settings and automatic policies
to current family versions; no background settings rewrite or revision bump. Denies migrate by family;
conflicting legacy keys intersect allow states, never erase a deny. New manual work honors the retained
family/effort regardless of automatic suggestions. New automatic delegation validates the resolved pair
against current family/level policy, without an extra router/model call. If the newest supported family
model cannot use the chosen effort, reject/wait rather than downgrade effort, pick an older model or cross
families. Missing families/catalog access similarly block; explicit owner choices stay intact.

An assignment's existing `task.model_selected` exact model/effort stays pinned through continue and
recovery. Each resumed automatic assignment checks current family policy and its **exact** catalog support,
even if mode changed or a newer family version appeared. Denied or removed exact pairs wait with their
original thread/route; no silent reroute. New manager executions resolve the family before initial native
launch and record the exact model/effort in executions; interrupted manager executions with native identity
retain the exact ID. Native thread/settings/turn events retain runtime evidence too.

`model/list` is metadata, not inference. App Server startup loads a complete paginated snapshot; a live
manager refreshes metadata on use at most hourly, with an atomic snapshot replacement. Refresh failure
retains the last complete snapshot for up to 24 hours, retries no faster than five minutes and exposes
catalog timestamp/error through `/api/models`. Empty/partial/too-stale cache blocks instead of inventing
models. Restart requires an authenticated fresh catalog; no fabricated persistent fallback. Existing
provider/client cache behavior can delay discovery beyond application refresh; no zero-delay latest claim.

Family selection buttons have a subtle dark-red gradient consistently in both role settings; effort
buttons remain plain. Existing aria-pressed, focus, disabled/selected states, minimal labels, full visible
choices and keyboard interaction remain. Chromium checks computed background treatments, persistence,
policy/deny behavior and layout at 1200/393/320px; screenshots retain the actual rendering. Backend tests
cover numeric 6.9/6.10 resolution, hidden/unknown IDs, missing families/efforts, deny preservation, pinned
assignments and manager recovery, metadata pagination/atomic failure/TTL/backoff/stale/empty cases.

---

## Historical model-settings delivery

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
`worker_route` in its existing tool call according to the deterministic
[verification tier](VERIFICATION_TIERS.md). Small uses Luna/Haiku or Sol/Sonnet Low, Normal uses
Sol/Sonnet Medium, and Risky uses Sol/Sonnet High or Astra/Opus, always within policy. The backend
checks the exact pair, rejecting absent, unsupported or denied automatic choices. In Automatic it
also rejects a Risky pair weaker than workhorse High/strongest while such a pair is permitted.
Manual mode always uses its saved pair, regardless of a suggested tool pair or tier. No extra
inference call routes work. The automatic Risky diff review uses the cheapest policy-permitted pair
of the worker's provider in a separate read-only process.

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

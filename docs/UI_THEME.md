# Tasks wrapping and family button candidate — 2026-10-13

`unfinished-recovery-family-ui-v1-20261013` is implemented/validated/prepared only; deployment awaits a new owner request.
Tasks paragraphs previously allowed unbroken filenames/paths to widen the page. At 320px the reproduced
baseline overflow was 15,511px. Tasks panels and their children now constrain intrinsic widths and wrap
unbroken text with overflow-wrap:anywhere, preserving full names. Task History fields remain contained;
intentional code scrolling stays inside its preformatted block. No screenshot was attached by the owner:
this is a synthetic long-filename reproduction, with zero page/panel overflow at 320/393/1440px.
The existing History editor Cancel button lacks a handler; Escape closes it. That unrelated pre-existing
behavior is not changed in this layout patch and is recorded as a follow-up, not claimed fixed.

Manager and Bot family buttons share a quiet red gradient; effort buttons remain plain. Existing selected,
disabled/focus states and semantics remain. Full browser receipts/screenshots/report:
`/home/codex/.local/share/linko-bot-manager/deployments/unfinished-recovery-20261013/`. Current live remains the succeeded navigation/audio release, not this candidate.

---

# Rich red chat accent — 2026-10-12

Owner requested a cohesive rich red in place of the green selection theme. This focused
candidate changes only `ai_manager/static/style.css` at runtime. Dark navy surfaces remain;
selected project/menu surfaces use burgundy `#3b202d`, bright highlights use `#ff8296`,
primary actions use crimson `#c52f4c` with `#cd3350` hover and near-white `#fff4f6` text.
Hover surfaces use `#4b2635`; focus indicators use `#ff647c` with outward button/link rings
and a visible composer border. Native form accents and text selection share the palette.
Selected project tabs, project menu, chat modes, workspace tabs, primary actions, Markdown
links/quotes, authentication mark and shared focus/hover states follow these tokens.

Semantic completed badges and successful action notices retain green. Warning, outage,
failure and destructive-action colors remain distinct. Existing layout, markup, JavaScript,
backend, dependencies, schema, composer behavior and agent controls are preserved.

## Review and verification

Disposable authenticated Chromium checked 1440×1000, 393×851 and 320×420 viewports with
actual computed normal/hover text contrast, project/menu/mode selection, workspace active
state, keyboard focus, composer focus, real Send/stream completion, retained draft, 44px
composer controls, viewport bounds, no horizontal overflow and no page errors. Accent text
contrast ranges from 4.66:1 (primary hover) to 7.79:1 (active tab). Composer/control focus
contrast exceeds 3:1. Before/after, menu and authentication screenshots were visually reviewed.
Physical Android/iOS browser and keyboard behavior is not claimed by Chromium viewport checks.

Exact candidate release ID: `chat-ui-rich-red-v1-20261012`.
Isolated checkout: `/home/codex/checkouts/linko-rich-red-20261012`.
Detailed report, replayable theme browser check, computed receipts, before/after screenshots
and manager routine-delivery facts:
`/home/codex/.local/share/linko-bot-manager/deployments/rich-red-theme-20261012/REPORT.md`.
Required preparation validation, exact commit/assets, checksums and Git bundle are retained in
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/chat-ui-rich-red-v1-20261012/`.
The manifest is the readiness gate; no prepared status is established until it exists.

## Activation truth

Rich-red release `chat-ui-rich-red-v1-20261012` is **deployed**, proven by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/4dc90c51e97f8497303717091cf126a7.json`.
Its observed service commit is `361a18f43f7d2fca2e211ce5b2673f808003ea20`. Earlier preparation-only
wording described the original handoff before owner activation. The combined [model settings](MODEL_SETTINGS.md)
and manual scroll candidate preserves this palette; it is not deployed and owner said not to deploy yet.

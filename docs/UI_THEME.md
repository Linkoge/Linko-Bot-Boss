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

Implemented and UI-verified; preparation is local only. **Not deployed.** No owner deployment
request is included in this objective. No restart, production mutation or remote push occurred.
Current established deployed baseline is `agent-controls-v1-20261012`, commit
`0d2d80f2162c932d7beb945ce43b2aeb9c4bab0a`, proven by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/01e3989b0f1b9e05c3c726d49248dc01.json`.
Older prepared-only statements about that release are historical. On a later explicit owner
request, use the controlled manager `deploy_release` with the exact red-theme release ID,
requiring its own durable succeeded result before reporting activation.

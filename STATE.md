# Current state — 2026-10-13

Current live is `nav-audio-upload-v2-20261013`, commit
`08f0ba712f194ea0f985740ef408c9e033511b98`, established by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json`.
Read-only integration checks confirm unchanged service PID/start, active scheduler and all matching served assets.

Integrated release: `integrated-recovery-append-popup-v1-20261013` in
`/home/codex/checkouts/linko-integrated-deliveries-20261013`. This combines the three completed
independent deliveries without reimplementing them: unfinished-recovery-family-ui-v2, manager-append-rule-v1
and popup-cleanup-v1, all dated 20261013. Owner explicitly authorized deployment after their reports;
that authorization persists. The manager reviews and calls `deploy_release` with this integrated ID.
The integration worker does not request deployment, restart the service or alter production state.
Prepared status requires the exact manifest at `/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/integrated-recovery-append-popup-v1-20261013/manifest.json`;
only this release's own later succeeded result receipt establishes deployed status.

Lasting candidate effects: durable assignment follow-up and idle recovery respecting owner holds;
latest supported catalog version within the chosen family for new work, exact retained routes for
existing assignments/recovery; separate recording Stop removed while Record toggles stop/transcribe;
long Tasks names wrap; model-family buttons have distinct subtle red backgrounds; concise manager
pause/append scope; lean 5 hour/Weekly usage with last successful refresh time; project-menu layering
and input corrected. Existing 2×2 navigation, recording Upload/cancellation/replacement, drafts,
attachments, manual chat scrolling, rich-red theme, lifecycle controls and live base remain.
No schema/dependency change, separate router or new supervision service is introduced.

Owner pauses, waits, cancellations, deferred To-Do and unrelated unfinished work remain respected.
The History editor Cancel handler remains an unrelated pre-existing follow-up (Escape works).
Historical interruption was caused immediately by an authenticated interrupt request; triggering gesture
and STT-provider 502 cause remain unknown. Physical-device/native picker and paid STT acceptance are
not claimed. Original model/catalog observations are historical snapshots, not current settings claims.

Authoritative contracts: [recovery](docs/AUTOMATIC_RECOVERY.md), [models](docs/MODEL_SETTINGS.md),
[audio](docs/AUDIO.md), [controls](docs/AGENT_CONTROLS.md), [UI](docs/UI_THEME.md),
[usage/project popup](docs/CODEX_USAGE.md), [memory](docs/PROJECT_MEMORY.md),
[deployment](docs/DEPLOYMENT_HELPER.md). Full report, source mapping, verification and manager routine
brief/recent-delivery facts: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md` and sibling `manager-summary.json`.
Earlier state remains in Git and original private reports/receipts. No full memory reconciliation.

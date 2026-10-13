# Focused popup cleanup — implemented/verified; do not deploy, 2026-10-13

Candidate `popup-cleanup-v1-20261013` simplifies usage to 5 hour/Weekly percentage and reset,
retains a truthful last-successful-refresh footer and fixes project-menu painting/input layering.
Contract/evidence: [Codex usage and project popup](docs/CODEX_USAGE.md). Isolated checkout:
`/home/codex/checkouts/linko-popup-cleanup-20261013`. Exact manifest is the preparation gate.
Full private report and routine manager facts: `deployments/popup-cleanup-20261013/REPORT.md`
and `manager-summary.json` under `/home/codex/.local/share/linko-bot-manager`.
Current live baseline nav/audio v2 succeeded with nonce `0b09a6f825a34572dd8bcf5773f149b1`,
commit `08f0ba712f194ea0f985740ef408c9e033511b98`; older pending/live statements below are historical.
Activation is pending. Keep the independent recovery/UI/model and queued instruction assignments
untouched; a newer live baseline requires coordinated integration and a new exact preparation.

# Navigation/audio successor — verified candidate, 2026-10-13

Exact candidate `nav-audio-upload-v2-20261013` aligns navigation squares in two columns and adds recording
Upload with selection-only replacement and stale-session protection. Contract, validation limits,
exact preparation gate and full report: [audio](docs/AUDIO.md). Activation remains pending owner
instruction; no deployment/restart requested or performed for this delivery.

# Current deployed baseline — 2026-10-13

`model-settings-manual-scroll-v2-20261013`, commit `0745c1a6af91f09d62f414c0c63326d2afe82260`,
succeeded with nonce `c9a21304d22c36785e25b66f914ec90b`. Receipt is referenced in [model settings](docs/MODEL_SETTINGS.md).
The earlier pending/live statements below are historical.

# Model settings and manual scrolling — verified candidate, 2026-10-13

Exact release `model-settings-manual-scroll-v2-20261013` implements the owner-approved panel,
persisted native runtime choices, constrained manager-selected Automatic bot pairs and fully manual
chat scrolling. Contract/evidence: [model settings](docs/MODEL_SETTINGS.md) and [chat history](docs/chat-history.md).
No deployment authorized or performed; release manifest is the preparation readiness gate.

# Current deployed baseline — rich red, 2026-10-13

`chat-ui-rich-red-v1-20261012`, commit `361a18f43f7d2fca2e211ce5b2673f808003ea20`, succeeded with
nonce `4dc90c51e97f8497303717091cf126a7`. Receipt references: [UI theme](docs/UI_THEME.md).
Existing composer/audio and agent controls are preserved. Older pending statements below are historical.

# Compact project-memory candidate — activation pending, 2026-10-12

Versioned compact manager brief, focused worker context, recent-delivery archive and explicit owner review/diff/apply/rollback are implemented for testing/preparation. No deployment is requested. Current known behavior and detailed evidence: [project memory](docs/PROJECT_MEMORY.md). Existing production acceptance and historical evidence remain unchanged.

# Current state

The service uses direct manager delegation, native autonomous Codex workers, a global project queue and durable automatic manager continuation. Task lifecycle: queued → running → completed/failed, with waiting for recovery/questions. The prepared agent-controls candidate adds pause_requested → paused and cancel_requested → cancelled after owned-process cleanup; resume retains the same task/thread. See docs/AGENT_CONTROLS.md; activation requires an owner request and exact succeeded receipt. Conversation, Auto and Execute use the same manager path.

Stage 3 adds deterministic automatic recovery for workers and manager conversations, persisted backoff and shared account usage gating (schema 9). Current validation and deployment evidence are in [docs/AUTOMATIC_RECOVERY.md](docs/AUTOMATIC_RECOVERY.md). Runtime records live in SQLite outside Git. Earlier milestone observations remain in Git history.

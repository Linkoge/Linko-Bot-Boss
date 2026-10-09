# Session handoff

Read MASTER_SPEC.md, IMPLEMENTATION_PLAN.md, STATE.md and git status/log. Resume the first unfinished stage; do not ask the owner to resend instructions. Work inside this repository only.

Respect unrelated changes and infrastructure. Test milestones, record evidence and create local Git checkpoints. Never mark untested worker claims or host deployment as verified.

Stages 1, 2 and 3 passed. Next task: Stage 4 planning, scheduling and independent verification. Run `uv run --offline pytest -q` with host execution (sandbox thread portal stalls); CLI is `uv run python -m ai_manager.cli`. Real chat/resume smoke test: scripts/smoke_codex.py. Browser test: scripts/browser_smoke.py. Runtime inventory is in STATE.md and docs/inventory.md. Existing AI-Manager is a separate Chatwoot deployment; preserve its private Serve listeners 443/3000. Approved host execution provides user systemd and Tailscale access. Owner setup token is private in the runtime data directory; no owner account has been created in the real DB.

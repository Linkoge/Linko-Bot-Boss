# Session handoff

Read MASTER_SPEC.md, IMPLEMENTATION_PLAN.md, STATE.md and git status/log. Resume the first unfinished stage; do not ask the owner to resend instructions. Work inside this repository only.

Respect unrelated changes and infrastructure. Test milestones, record evidence and create local Git checkpoints. Never mark untested worker claims or host deployment as verified.

Stage 1 passed four tests and real ten-repository inventory. Next task: Stage 2 authentication and streaming dashboard. Run `uv run --offline pytest -q`; CLI is `uv run python -m ai_manager.cli`. Runtime inventory is in STATE.md and docs/inventory.md. Existing AI-Manager is a separate Chatwoot deployment.

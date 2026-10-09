# Linko Bot Manager / AI Manager

## Purpose and long-term vision

Give one human owner a persistent conversational interface for managing multiple software and business projects. The manager remembers context and decisions, converts delegated goals into tasks, supervises Codex workers, verifies results, and asks for consequential approvals. The owner should not have to carry prompts or reconstruct state between sessions.

Permanent requirements: MASTER_SPEC.md. Stage gates: IMPLEMENTATION_PLAN.md. Verified implementation: STATE.md. Development priorities: TASKS.md. Fresh-session instructions: HANDOFF.md. Keep these existing files authoritative; do not create competing copies.

## Objectives

- Deliver a responsive Android/desktop chat and portfolio overview with independent project contexts.
- Keep structured tasks, conversations, execution ownership, approvals and evidence durable across disconnects and restarts.
- Automate useful work with configurable autonomy and enforceable capabilities; never equate an agent's claim with verified completion.
- Preserve unrelated projects, uncommitted changes, sessions and remote access.
- Expand controlled dependency/integration/deployment capabilities only after their own security and acceptance checks.
- Complete the six-stage plan, including actual reboot recovery, before claiming daily-use production acceptance.

## Architecture and major components

Python 3.12 + FastAPI runs a single persistent asynchronous service. SQLite WAL/full synchronous transactions are authoritative. Browser assets are plain HTML/CSS/JavaScript with durable server-sent event replay. The installed ChatGPT-authenticated Codex App Server supplies inference; no separate API provider is introduced.

| Component | Source and responsibility |
| --- | --- |
| HTTP/UI | ai_manager/web.py, static/: authenticated APIs, dashboard, per-project chat, approvals and agent monitor |
| Authentication | auth.py: Argon2id passwords, local one-use setup, hashed/expiring/revocable secure sessions, CSRF and throttling |
| Persistence | db.py: schema migrations, WAL transactions, events, integrity-checked backup/restore |
| Registry/memory | registry.py, memory.py: bounded discovery, reconciliation, revision checks and locked atomic Markdown projections |
| Conversation/transport | chat.py, codex.py: JSONRPC stdio, thread/turn IDs, streaming, cancellation, account/catalog checks and model routes |
| Work execution | tasks.py, workers.py, workspace.py: explicit states/dependencies/leases, supervised processes and isolated local Git clones |
| Planning/verification | orchestration.py: delegated structured plans, scoped scheduler, trusted tests and independent file-inspecting review |
| Permission boundary | policies.py, capabilities.py: ALLOW/ASK/DENY, exact-operation grants and brokered file/test/isolated-commit operations |
| Operations | deploy/, scripts/: dedicated user service, daily local backups, installation and disposable acceptance scripts |

Runtime data lives outside Git at /home/codex/.local/share/linko-bot-manager. Existing project documentation is adapted; generated per-project PROJECT/STATE/TASKS/DECISIONS/HANDOFF documents are projections of the database, not competing source authorities. Workers use committed source baselines and preserve original checkouts; verified patches can be downloaded for deliberate integration.

## Current delivery boundary

Stages 1–5 are verified for supported capabilities. Stage 6 is partially verified. Dependency installation, merge/push/deployment and other sensitive capabilities remain unavailable. The trusted Python/App Server run as the host owner; scoped model tools and bubblewrap test isolation do not protect against compromise of that trusted backend. See docs/security.md.

The unrelated /home/codex/projects/AI-Manager is a Chatwoot customer-service deployment. Its source, services and Tailscale listeners must remain intact. Full requirements and existing infrastructure take precedence over convenient shortcuts.

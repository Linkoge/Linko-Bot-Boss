# Verified state

## 2026-10-09 — Inventory

- Empty target repository found with expected origin and unborn `main` branch.
- Existing `../AI-Manager` is a Chatwoot deployment for customer email/Messenger, not a software-project manager. Reviewed README/upstream docs read-only; no reusable Codex orchestration backend. Its unrelated uncommitted changes were preserved.
- Python 3.14.4, uv 0.12.23, Git 2.53.0 and Codex CLI 0.161.0 available.
- Installed App Server supports stdio and local JSON schema generation. Schema generated into temporary directory, not vendored.
- Host systemd/tmux sockets inaccessible; Tailscale daemon inaccessible from this executor. No remote access configuration changed.
- Specification and stage plan persisted. Stage 1 implementation next.

## Blockers and limits

Host service installation, private Tailscale exposure and reboot verification require access to the actual VM runtime. ChatGPT account model access must be tested through App Server, not assumed from the catalog or subscription description.

Updated observation: approved execution provides host runtime access. Read-only checks confirmed user systemd running, existing tmux sessions intact, Tailscale Running/online (`codex-server.tail5ebb9d.ts.net`), port 8787 free. Existing private Serve ports 443 and 3000 route to Chatwoot and must be preserved. A minimal bubblewrap OS namespace probe succeeded. No network listener changed yet.

## Stage 1 verified checkpoint

- Four foundation tests passed (Python 3.12.15): discovery/source preservation, stale revisions/path boundaries, competing durable task claims/dependencies/verification, restart and backup restore integrity.
- Actual CLI inventory found ten repositories; overview works. Database WAL confirmed in tests.
- Private generated memory adapts existing documentation without modifying project source. SQLite remains authoritative; revision-checked memory edits and locked atomic Markdown exports prevent stale overwrite.
- Structured tasks, dependencies, execution metadata, verification checkpoints and event log persisted. Expired leases block for manual/process reconciliation.
- Stage 1 acceptance passed. Next: Stage 2 authentication, dashboard and verified App Server chat.

## Stage 2 verified checkpoint

- Nine backend tests passed, Ruff checks passed, JavaScript syntax check passed. Web tests require approved host execution because the sandbox stalls the test client's thread portal; two hung test runs were identified and stopped, leaving unrelated sessions intact.
- Argon2id owner passwords; one-use local setup token; hashed expiring/revocable session tokens; HttpOnly/SameSite cookies; CSRF and origin/host checks; durable login throttling; restrictive CSP. No default password or public signup.
- Responsive dashboard, project selector, scoped persistent history/search, durable SSE, settings for five validated model roles, registry and memory editing, task creation and execution monitor.
- Real App Server initialized against Codex 0.161.0 using existing ChatGPT login. Catalog reported GPT-6.1 Sol and other installed models. Verified GPT-6.1 Sol/low through an actual completed streamed `MANAGER_READY` turn (~4.3s), then a new process resumed the same thread and completed `RESUME_READY`. Thread/turn IDs, output, latency and token usage persisted in isolated smoke-test DB.
- Other catalog models/efforts are selectable but not claimed as inference-verified. No separate API key/provider introduced. Chat tools, connectors, plugins and automatic subagent effort disabled; unknown capability requests fail closed.
- Context is bounded to selected summary/tasks/memory snippets; Codex native persistent compaction supported and lifecycle events recorded.
- Playwright verified one-time owner setup/login, ten-project dashboard, project switching and memory. Desktop 1440×1000 and Android 393×851 screenshots inspected; no page errors/horizontal overflow.
- Temporary loopback server started on 8787, awaiting owner setup. No private VPN route installed yet. Next: Stage 3 isolated task workers and control/recovery tests.


## Stage 3 verified checkpoint

- Fourteen tests pass, including confirmed interruption versus uncertain transport loss, workspace preservation, secret/path/symlink denial and an OS command sandbox check. Standalone local Git clones provide workspace isolation without shared mutable Git admin state. Source repositories remain unchanged.
- Real GPT-6.1 Sol/medium worker launched in a disposable repository, read README through the controlled manager_workspace capability, recorded reported progress and emitted persisted output. Completion moved the task to verifying, never directly to completed.
- A fresh worker process resumed the same persisted thread and isolated workspace and read the expected marker again. Evidence: scripts/smoke_worker.py.
- Logical agent, thread/turn, process start identity, branch/base commit, workspace, output, model/effort, usage, latency and errors persist. Two-worker cap and one active task per project enforced transactionally. Leases, interruption and restart recovery distinguish confirmed pause from unknown outcomes.
- Start/resume/interrupt/cancel controls and monitor added. Clones use committed baselines; uncommitted source changes remain untouched. Capabilities initially permit read/list/progress only. Next: Stage 4 planning, scheduling and independent verification; policy-gated edit/test capabilities follow in Stage 5.

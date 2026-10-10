# AI Manager

Self-hosted conversational manager for one owner's software portfolio. Permanent requirements are in [MASTER_SPEC.md](MASTER_SPEC.md); verified stage status is in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) and [STATE.md](STATE.md).

## Open the manager

The installed private interface is **https://codex-server.tail5ebb9d.ts.net:8787/**. Connect through your Tailscale network. The persistent user service and daily local backup timer are enabled. No default owner account is created.

Choose your username/password once, privately on the VM:

```bash
cd /home/codex/projects/Linko-Bot-Manager
uv run --offline python -m ai_manager.cli setup-owner
```

Then sign in through the interface. Alternatively, read `~/.local/share/linko-bot-manager/setup-token` privately and use the web setup form. Never put that token in logs, Git or a URL. Setup closes after account creation.

Select a project, set its objective and instructions, register trusted verification commands in Settings, and delegate a goal through chat. Planning creates persistent tasks; enabling a scoped scheduler batch runs up to two workers. Approval center shows exact requested operations. Agent monitor exposes activity, independent verification evidence and downloadable patches. Conversation history survives project switching and browser disconnection.

## Implementation

FastAPI supplies asynchronous streaming and typed request validation. SQLite WAL with full synchronous commits stores registry, project memory, conversations, tasks, executions, approvals, verification and audit events. Five model roles use the installed Codex App Server and existing ChatGPT authentication. Model/effort settings are validated against the actual catalog; GPT-6.1 Sol low/medium has passed real chat, resume, planning, coding and review checks. API-key providers are refused; no separately billed API was added.

Source discovery defaults to `~/projects`, with ten actual repositories registered. Existing documentation is adapted without modifying source files. Private PROJECT/STATE/TASKS/DECISIONS/HANDOFF Markdown is a recoverable projection of authoritative structured state. Revision checks and locked atomic exports reject concurrent stale updates. Context uses bounded snippets and on-demand selected-project document retrieval; App Server supports native compaction.

Workers use standalone local Git clones and branches, with durable leases and one active execution per project. Code stays in isolated workspaces until the owner deliberately integrates a reviewed patch. Tests run through owner-registered argv in bubblewrap without host home, credentials or network. Completion requires tests and independent file-inspecting review. Unexpected process loss blocks for reconciliation rather than replaying uncertain effects. Policies enforce brokered read/edit/test/delete/isolated-commit operations; ASK grants bind exact operations and expire.

Dependency installation, merge, push, deployment and infrastructure/network/credential/external-service/communication/payment capabilities are **not implemented**. The UI identifies unavailable capabilities; ALLOW cannot activate them. Trusted Python/App Server run as the host owner, not a separate service Unix user. Read [security boundaries](docs/security.md) before expanding autonomy.

## Verification and development

```bash
uv sync --locked
uv run --offline pytest -q
uv run --offline ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
uv run --offline python scripts/browser_smoke.py
```

Real subscription smoke tests use disposable databases/repos: `scripts/smoke_codex.py`, `smoke_worker.py`, `smoke_orchestration.py`, and `smoke_coding.py`. They invoke the existing authenticated subscription. The coding fixture simulates its own approval; it never approves a real owner's pending operation. [Verification record](docs/verification.md) distinguishes actual model/process checks from fixtures.

For local HTTP development, set `AI_MANAGER_SECURE_COOKIE=0`; production private HTTPS keeps secure cookies on. Configure `AI_MANAGER_DATA`, colon-separated `AI_MANAGER_PROJECT_ROOTS`, exact comma-separated `AI_MANAGER_ALLOWED_HOSTS`, and absolute `AI_MANAGER_CODEX` as needed. Runtime data is outside Git at `~/.local/share/linko-bot-manager`.

## Operations and remaining acceptance

[Operations](docs/operations.md) covers service installation, backup/restore, recovery and daily use. VM storage was 99% full at deployment (approximately 445MB free). Workspace/backup guards preserve a reserve and never clean unrelated data. Expand/free storage before running large projects. Stage 6 remains open for storage capacity and a coordinated VM reboot test; existing active sessions were preserved.

Daily SQLite backups include authoritative memory and history. Back up isolated workspaces and Codex thread storage separately for resumable execution. Restore to a new path, never overwrite a live database, and revoke old sessions after restoration. No remote backup transfer is enabled.

Implementation references: [official App Server protocol](https://learn.chatgpt.com/docs/app-server), [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [FastAPI streaming](https://fastapi.tiangolo.com/advanced/custom-response/), [Argon2 password handling](https://argon2-cffi.readthedocs.io/en/stable/howto.html). Installed generated schemas govern RPC fields when documentation differs.

Execution controls now distinguish a queued launch, a resumed interrupted thread,
a safe pre-thread launch retry, and independent verification of existing output.
Choose Conversation for discussion or Execute to create a persistent plan in the
selected project; scheduling follows the configured enablement, scope and budget.
Configure trusted argv through Settings or beside a verification-blocked task,
then explicitly Retry verification. Home-installed environments are not exposed
inside the verification sandbox. See [execution reliability evidence and remaining
project setup](docs/execution-reliability.md).

# Linko Bot Boss

Repository: [Linkoge/Linko-Bot-Boss](https://github.com/Linkoge/Linko-Bot-Boss). The existing project ID is `29018771ce9842f2bb52afb7dcf491d0`; owner display name remains **Bot Boss**.

The repository/product rename preserves `/home/codex/projects/Linko-Bot-Manager`, Python package `ai_manager`, distribution `linko-bot-manager`, `AI_MANAGER_*` configuration, `linko_bot_manager` native client identity, existing service/data names, browser storage keys and private URLs. These identify the installed runtime and retained history, and require a separate migration if ever changed. PWA ID `/`, scope/start URL and icon artwork remain stable.


A private conversational manager for software projects:

**User → AI Manager → native coding worker (Codex or Claude) → AI Manager → User**

Use Conversation for discussion, Auto to delegate requested work, or Execute to send a work objective through the same manager path. Discuss work naturally in a project context. The manager delegates a complete objective when requested. Codex investigates, edits, installs dependencies, runs commands, builds, tests, debugs and uses Git in its durably owned assignment workspace (or the retained original cwd for historical/non-Git sequential work). Results return automatically to the originating conversation.

Delivery is a short pipeline ([docs/AUTO_DEPLOY.md](docs/AUTO_DEPLOY.md)): each task runs smoke and targeted tests once with at most five repair rounds and no AI review; finished work merges into main one at a time; one deploy script waits for idle, backs up SQLite, restarts, health-checks and rolls back automatically, then records a short result. Per-project Auto-deploy ON means merge and deploy, OFF means neither. One global coding worker.

Attachment batching preserves dictionary references, their metadata and first-seen order; the retained regression passes. Current status and limits: [STATE.md](STATE.md).

Coding runs with one global sequential worker, retaining isolated workspace ownership. Historical chat/⇈ Send permission and capacity settings do not admit overlap. See [parallel execution](docs/PARALLEL_EXECUTION.md). Projects participate automatically. Tasks, conversations, native thread references, questions and execution history persist in SQLite. Temporary network, process and account usage interruptions recover automatically with persisted backoff. Restart handling inspects native outcomes before resuming the same assignment and conversation. See [automatic recovery](docs/AUTOMATIC_RECOVERY.md) and [Pause/Cancel and bundled updates](docs/AGENT_CONTROLS.md).

The private interface remains at **https://codex-server.tail5ebb9d.ts.net:8787/**. Existing owner authentication and private hosting are preserved. Initial setup, when required:

```bash
uv run python -m ai_manager.cli setup-owner
```

Configuration: `AI_MANAGER_DATA`, `AI_MANAGER_PORT` (default 8787), `AI_MANAGER_REPO` (live checkout; tests only), `AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE` and `AI_MANAGER_CODEX`. Runtime data defaults to `~/.local/share/linko-bot-manager`. Manager and bot settings are global: manual Manager, manual Bot or manager-selected permitted automatic pair, validated against the installed Codex catalog (see [model settings](docs/MODEL_SETTINGS.md)); the selected Codex or Claude subscription supplies inference. Existing assignments retain their original provider/model/thread when the owner switches providers.

Native workers have broad same-user access. They preserve user work and request explicit confirmation before catastrophic broad destruction. This is an agent-recognized conversational boundary, not a deterministic classifier of arbitrary programs. Completion is reported by the worker, not independently certified. See [security boundaries](docs/security.md).

Legacy unfinished assignments remain waiting until explicitly continued, preserving their source/workspace references and historical dispositions. Historical verification and integration evidence remains readable through task history. There is no migration-time replay or automatic merge of old clones.

Rich red chat theme candidate and truthful activation/evidence: [UI theme](docs/UI_THEME.md).

## Development

```bash
uv sync --locked
uv run pytest -q -m smoke   # every task, once
uv run pytest -q            # full suite: run by the deploy script, not per task
uv run ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. uv run python scripts/browser_smoke.py
PYTHONPATH=. uv run python scripts/smoke_minimal.py
```

The fast pytest smoke suite (20-second budget) runs once per task; the full suite runs once, in the deploy script. The real Codex smoke script uses a disposable calculator repository and database, fixes code, runs tests, commits locally and checks automatic manager delivery. It invokes the existing subscription. Browser acceptance uses an isolated local HTTP fixture. Neither script uses the production database.

See [operations](docs/operations.md), [autonomy](docs/AUTONOMY_POLICY.md) and [cleanup/deployment evidence](docs/CODEBASE_CLEANUP.md). Historical reports link to their complete versions in Git.

## One-time deploy bootstrap

After this pipeline is in main, run once on the server (no sudo; installs and enables the user units, then queues the first deploy):

```bash
cd /home/codex/projects/Linko-Bot-Manager && python3 scripts/install_deploy.py
```

Everything after that is automatic. Result of the last deploy: `~/.local/share/linko-bot-manager/deploy/last-deploy.txt`; manual deploy: `python3 scripts/deploy.py --now`.

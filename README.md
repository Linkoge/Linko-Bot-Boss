# Linko Bot Manager

A private conversational manager for software projects:

**User → AI Manager → native Codex worker → AI Manager → User**

Use Conversation for discussion, Auto to delegate requested work, or Execute to send a work objective through the same manager path. Discuss work naturally in a project context. The manager delegates a complete objective when requested. Codex investigates, edits, installs dependencies, runs commands, builds, tests, debugs and uses Git in the registered project directory. Results return automatically to the originating conversation. Codex chooses its own validation; there is no planner, independent reviewer, verification controller, command registry or permission-profile setup.

Different projects run concurrently, with one coding worker per project and a global capacity default of two. Projects participate automatically. Tasks, conversations, native thread references, questions and execution history persist in SQLite. Temporary network, process and account usage interruptions recover automatically with persisted backoff. Restart handling inspects native outcomes before resuming the same assignment and conversation. See [automatic recovery](docs/AUTOMATIC_RECOVERY.md) and [Pause/Cancel and bundled updates](docs/AGENT_CONTROLS.md).

The private interface remains at **https://codex-server.tail5ebb9d.ts.net:8787/**. Existing owner authentication and private hosting are preserved. Initial setup, when required:

```bash
uv run python -m ai_manager.cli setup-owner
```

Configuration: `AI_MANAGER_DATA`, `AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE` and `AI_MANAGER_CODEX`. Runtime data defaults to `~/.local/share/linko-bot-manager`. Manager and worker model settings are global and validated against the installed Codex catalog; the existing ChatGPT subscription supplies inference.

Native workers have broad same-user access. They preserve user work and request explicit confirmation before catastrophic broad destruction. This is an agent-recognized conversational boundary, not a deterministic classifier of arbitrary programs. Completion is reported by the worker, not independently certified. See [security boundaries](docs/security.md).

Legacy unfinished assignments remain waiting until explicitly continued, preserving their source/workspace references and historical dispositions. Historical verification and integration evidence remains readable through task history. There is no migration-time replay or automatic merge of old clones.

## Development

```bash
uv sync --locked
uv run pytest -q
uv run ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. uv run python scripts/browser_smoke.py
PYTHONPATH=. uv run python scripts/smoke_minimal.py
```

The real Codex smoke uses a disposable calculator repository and database, fixes code, runs tests, commits locally and checks automatic manager delivery. It invokes the existing subscription. Browser acceptance uses an isolated local HTTP fixture. Neither script uses the production database.

See [operations](docs/operations.md), [autonomy](docs/AUTONOMY_POLICY.md) and [cleanup/deployment evidence](docs/CODEBASE_CLEANUP.md). Historical reports link to their complete versions in Git.

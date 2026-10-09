# AI Manager

Self-hosted Python manager for a single owner's software portfolio. Requirements: [MASTER_SPEC.md](MASTER_SPEC.md). Progress: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [STATE.md](STATE.md).

## Local foundation

```bash
uv sync --locked
uv run python -m ai_manager.cli discover
uv run python -m ai_manager.cli overview
uv run pytest -q
```

Discovery defaults to `~/projects`. Configure colon-separated `AI_MANAGER_PROJECT_ROOTS` and `AI_MANAGER_DATA` if needed. Runtime database and generated Markdown memory live in `~/.local/share/linko-bot-manager`, outside Git. Source repositories are read only during discovery. Removing a registry entry retains history and never deletes source files. Re-add a removed entry by changing its status through administrative controls; automatic discovery will not resurrect it.

SQLite WAL and full synchronous commits keep short writes durable. Revisions reject stale updates; task claiming is transactional, respects dependencies, and permits one editing task per project with a global cap of two. An expired lease blocks for reconciliation; it never automatically repeats an uncertain operation.

## Dashboard

```bash
# Secure first owner setup, prompting privately on the server:
uv run python -m ai_manager.cli setup-owner
uv run python -m ai_manager.cli serve
```

The backend binds `127.0.0.1:8787`. Secure cookies are on by default for HTTPS through a private Tailscale proxy. For local HTTP development only, set `AI_MANAGER_SECURE_COOKIE=0`. For a private proxy, add its exact hostname to comma-separated `AI_MANAGER_ALLOWED_HOSTS`; no wildcard host is required.

Alternatively, create the owner in the web setup screen using the one-use token from `$AI_MANAGER_DATA/setup-token` (default `~/.local/share/linko-bot-manager/setup-token`). Read that file privately on the server; never paste it into logs, Git or a URL. The owner chooses their password in the form. Setup closes after account creation. Logout and session revocation invalidate stored sessions.

Chat uses the installed Codex App Server and its existing ChatGPT login. Five role routes are configurable with model/effort validation against `model/list`; a successful inference verifies access. API-key login/providers are refused. Tool execution is disabled in chat; proposals are explicitly distinguished from completed actions. Selected project summaries and bounded memory/task snippets accompany each message; native thread compaction keeps Codex history bounded.

Streams replay durable events with cursor support. Browser disconnection does not cancel a response. A service restart preserves partial output and marks interrupted work for reconciliation rather than silently replaying it.

```bash
uv run python scripts/smoke_codex.py   # Two minimal real subscription turns, including fresh-process resume
uv run python scripts/browser_smoke.py # Isolated owner/database; installed Chrome; desktop + mobile
```

Implementation references: [official App Server protocol](https://learn.chatgpt.com/docs/app-server), [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [FastAPI streaming](https://fastapi.tiangolo.com/advanced/custom-response/), [Argon2 password handling](https://argon2-cffi.readthedocs.io/en/stable/howto.html). Installed generated JSON schemas govern RPC fields when documentation differs.

## Backups

```bash
uv run python -m ai_manager.cli backup /path/to/new-backup.sqlite3
# Stop the service before switching databases. Restore to a NEW path:
uv run python -m ai_manager.cli restore /path/to/new-backup.sqlite3 /path/to/fresh-data/manager.sqlite3
```

The online SQLite backup includes authoritative memory, conversations, tasks and audit events; integrity is checked. Generated Markdown exports can be recreated from database memory. Never copy a live database without its WAL or overwrite a running database. Keep the Codex thread store independently backed up on the host for thread resume; do not include authentication tokens in repository backups.

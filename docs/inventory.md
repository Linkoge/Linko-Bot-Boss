# Inventory — 2026-10-09

- Target: empty `/home/codex/projects/Linko-Bot-Manager`, origin `git@github.com:Linkoge/Linko-Bot-Manager.git`.
- Ten repositories discovered and registered by the actual CLI: AI-Manager, Linko, Linko-Bot-Manager, Linko-SSH, Okashi-Frontliner, linko-dev-panel, linko-gym-coach, linko-screenshotter, linkoge-profile, my.gov.ge-Tracker.
- Other folders are not automatically assumed to be Git projects; manual registration supports them.
- Existing AI-Manager is a Chatwoot customer-service deployment with email/Messenger. No source or configuration copied; no secret files read. Its outstanding unrelated changes are preserved.
- Available tooling: Python 3.14.4 system; uv-selected CPython 3.12.15 for application; uv 0.12.23; Git 2.53.0; Codex 0.161.0 with App Server stdio and JSON schema generation.
- Host service and session sockets inaccessible in sandbox. `systemctl --user` and `tmux list-sessions` report permission failures. Tailscale daemon cannot be reached here. These do not establish host service/VPN health.
- Managed-cloud environment-status tool and `/etc/codex/network-policy.json` are not provided in this executor. Dependency installation required approved network escalation, succeeded through ordinary uv. No network configuration changed.

## Foundation evidence

`uv run --offline pytest -q`: 4 tests passed covering real temporary Git discovery, source preservation, registry deletion, stale revisions, path scope, dependency order, concurrent claim ownership, explicit completion verification, uncertain-outcome restart and integrity-checked backup restoration.

`uv run --offline python -m ai_manager.cli discover` and `overview`: ten real repositories displayed. No agents assigned work and no unrelated process started or stopped.

## Current non-secret environment — 2026-10-10 pre-reboot

Measured approximately2026-10-09T21:25Z (2026-10-10 01:25 Asia/Tbilisi). Earlier inventory above is historical. No services restarted or features implemented during checkpoint preparation.

| Item | Confirmed value |
| --- | --- |
| Repository | /home/codex/projects/Linko-Bot-Manager; git@github.com:Linkoge/Linko-Bot-Manager.git |
| Git entry state | main; HEAD0ccfaa6ebd955efe02d6968301cb875c9b463e8f; no uncommitted changes at entry; checkpoint changes documentation only |
| Runtime Python | .venv/bin/python, CPython3.12.15; uv-managed interpreter under /home/codex/.local/share/uv/python; declared Python>=3.12 |
| Toolchain | uv0.12.23, Git2.53.0, Codex CLI0.161.0 |
| Locked production dependencies | fastapi0.143.0; uvicorn0.54.0; argon2-cffi25.1.0 |
| Locked development dependencies | pytest9.1.1; pytest-asyncio1.4.0; httpx0.28.1; playwright1.63.0; ruff0.16.10; uv.lock committed |
| Database | /home/codex/.local/share/linko-bot-manager/manager.sqlite3; schema_version rows1/2/3, current3; additive executions.base_commit; journal_mode=wal; integrity ok; foreign keys clean |
| Persistence boundaries | Data directory0700, DB0600; memory/ contains50 Markdown documents for10 projects; backups/ private local copies; workspaces/ for isolated execution clones |
| Codex persisted storage | /home/codex/.codex/sessions exists (~104MiB); /home/codex/.codex/state_5.sqlite exists (733184 bytes); auth.json exists but was not read. Preserve protected Codex storage across reboot; never copy credentials into Git |
| Current boot | e3a37e08-1b0b-4ac0-a923-8b6b97767873; started2026-10-08 17:26:16 local; full reboot still untested |
| Owner/setup | No owner row; auth-status setup_required=true; choose owner credentials privately; one-use setup token value was not read |

### Services and access

All units are under /home/codex/.config/systemd/user; source templates are deploy/*.in. Installed configuration was inspected against its intended paths.

- linko-bot-manager.service: enabled/running; currentMainPID1028786; WorkingDirectory=/home/codex/projects/Linko-Bot-Manager; ExecStart=/home/codex/projects/Linko-Bot-Manager/.venv/bin/python -m ai_manager.cli serve. Restart=on-failure, RestartSec=5, TimeoutStopSec=30, KillMode=control-group, UMask0077, NoNewPrivileges=yes; WantedBy=default.target; After/Wants=network-online.target. PID is an observation, never a safe post-boot process identity.
- Explicit settings: AI_MANAGER_CODEX=/home/codex/.local/bin/codex; AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,codex-server.tail5ebb9d.ts.net; AI_MANAGER_SECURE_COOKIE=1; PYTHONUNBUFFERED=1. No AI_MANAGER_DATA/PROJECT_ROOTS override in the running service; defaults are the data path above and /home/codex/projects. Optional ~/.config/linko-bot-manager.env absent. Do not dump arbitrary future environment files or process environment.
- linko-bot-manager-backup.service: oneshot, same working directory/Python; ExecStart runs scripts/backup_daily.py; UMask0077, NoNewPrivileges=yes, optional same env file. LastResult=success, ExecMainStatus=0.
- linko-bot-manager-backup.timer: enabled/active; OnCalendar=daily, Persistent=yes, RandomizedDelaySec=15m. Last trigger2026-10-10 00:10:30+04; next then scheduled2026-10-11 00:14:00+04. Persistent timers may run a missed backup after boot.
- loginctl show-user codex reports Linger=yes. tailscaled is enabled/active. These establish configured boot startup, not completed reboot acceptance.
- Backend binds127.0.0.1:8787; private Tailscale Serve https://codex-server.tail5ebb9d.ts.net:8787 proxies that listener. Existing private HTTP3000/HTTPS443 proxy127.0.0.1:3000 for Chatwoot and remain intact. No Funnel/public exposure configured. Auth-status endpoint reached successfully over HTTPS. Preserve SSH, Tailscale, RustDesk and unrelated sessions/services.

### Persisted state and policies

Two independent read-only SQLite connections observe ten projects,50 memory rows,two blocked tasks and one implementation checkpoint. Every Markdown export matches its DB row. Production executions/conversations/messages/Codex thread identifiers/approvals/operations/owner/policy overrides are all zero. Existing tests verify storage and recovery with fixture data; historical real Codex smoke runs used disposable DBs, not this production database.

Scheduler defaults paused; maximumexecutionworkers2; one active execution/project. No model route override. Roles default interactive low, planning/execution/verification medium and summarization low; architecture can use a validated override. Catalog names/efforts are checked dynamically; only GPT-6.1 Sol low/medium historical inference is verified.

Global policy defaults: read_files/run_tests ALLOW; edit_files/create_commits/delete_files ASK. install_dependencies/merge_branches/push_remote/deploy/start_services/external_communications ASK but unavailable. modify_infrastructure/change_network/access_credentials/paid_services DENY and unavailable. No project overrides exist. Changing a policy cannot enable an unimplemented capability.

App Server uses JSONRPC stdio with initialize/initialized, account/read, model/list, thread/start/resume and turn/start/interrupt. Existing ChatGPT login is required; API-key providers refused. Built-in shell/exec/patch/web search/apps/plugins/MCP/subagents are disabled; only brokered tools execute. Threads/turns, PID plus /proc start identity, branch/workspace/baseline/output/usage/latency/errors persist in DB when jobs run. Native compaction threshold100000 tokens.

Startup holds the singleton data-directory lock, recovers chat/task/orphan role turns conservatively, discovers projects, refreshes memory projections, cancels outstanding approvals and starts the scheduler. Uncertain worker outcomes block; confirmed interruptions pause. Resume checks prior process identity and reconciles Git/files/workspace. It never assumes a disconnected socket means a stopped worker, nor automatically replays a destructive/uncertain operation. SQLite uses short full-synchronous transactions, task leases and revision guards. Model tests use controlled argv in bubblewrap; trusted backend remains same-user, not an independent Unix-user boundary.

### Space: confirmed measurements versus reports

At first check /dev/vda2 total41953755136 bytes, used38956138496, available833515520 (~795MiB /834 decimal MB), use98%. Earlier verified availability was ~445MB/99%. The owner reports approximately800MB and manual deletion of some files; exact deleted paths/history were not inspected or inferred. This task deleted nothing.

Read-only du measurements identify consumers, not safe deletion candidates. Approximate sizes may overlap and change:

| Consumer | Measured size |
| --- | --- |
| /home/codex/Android | 8.2GiB |
| /home/codex/.gradle | 2.5GiB |
| /home/codex/.android | 2.0GiB |
| /home/codex/projects | 4.1GiB; includes Linko-SSH Build1.8GiB, linko-gym-coach1.3GiB |
| /home/codex/.codex | 2.0GiB; packages1.7GiB, sessions104MiB; preserve authentication/rollout state |
| /home/codex/.cache | 1.5GiB; ms-playwright658MiB, go-build484MiB, google-chrome174MiB |
| /home/codex/tools, vanity-pattern | ~1.1GiB each |
| /home/codex/.pub-cache | 517MiB |
| /var/lib/containerd | 3.4GiB; overlayfs2.5GiB, content900MiB |
| /var/lib/docker | 113MiB; volumes81MiB, containers32MiB |
| /var/lib/snapd | 1.7GiB (unprivileged measurement) |
| System journal | 48MiB |
| This repository | ~197MiB, .venv195MiB |
| Manager private data at initial check | ~1.5MiB |

Root-only Docker/containerd directory sizes were measured with scoped read-only sudo du. Ordinary docker system df lacked socket permission; no daemon change/prune attempted. Other unprivileged /var paths may undercount inaccessible content. Do not run prune/rm or remove dependency caches, SDKs, sessions, images/volumes simply based on these sizes. Workspace/backup guards reserve128MiB; ~795MiB is still inadequate for many larger projects.

### Backups and verified restoration

Before the fresh checkpoint backup, four backups existed in /home/codex/.local/share/linko-bot-manager/backups:

- manager-20261009T191733336333Z.sqlite3 — 217088 bytes, ten projects, zero tasks.
- manager-20261009T193132730694Z.sqlite3 — 217088 bytes, ten projects, zero tasks.
- manager-20261009T193233502180Z.sqlite3 — 245760 bytes, ten projects, two tasks.
- manager-20261009T201030624199Z.sqlite3 — 245760 bytes, ten projects, two tasks; latest scheduled backup.

All four were reopened read-only: integrity ok and zero foreign-key errors. New checkpoint backup and isolated-restore evidence are added below before final handoff. Never overwrite a live DB, assume the oldest snapshot contains recent tasks, or copy a live SQLite file without its WAL. Existing CLI backup uses the online SQLite backup API. Restore into a fresh path; a production data-directory switch requires a separately authorized service stop/start. Revoke sessions on the restored selection; regenerate Markdown from DB and reconcile workers before scheduling. Detailed commands and post-reboot checklist: docs/operations.md.

Same-disk snapshots do not cover VM disk failure. No off-VM transfer configured. Backup the protected Codex thread store and isolated workspaces separately when needed; the database alone does not restore absent rollouts/clones. No secrets, DB files, logs or generated recovery artifacts belong in Git.

### Tests

Checkpoint rerun: uv run --offline pytest -q — 31passed in7.56s; one Starlette TestClient/httpx deprecation warning. Ruff and node --check ai_manager/static/app.js passed. Existing tests use disposable fixtures; native execution is needed for TestClient because the sandbox thread portal previously stalled. No production service restart, owner setup, browser smoke or new model turn in this checkpoint task. Historical real-model/browser/crash evidence: docs/verification.md.

### Checkpoint-specific restore evidence

The refreshed development documents were projected into this project's private memory without modifying other projects, task states, policies or service configuration. Online backup **manager-pre-reboot-20261010-draft.sqlite3** (278528 bytes) was created in the private backups directory. It restored into **/tmp/linko-bot-manager-restore-check-20261010-draft/manager.sqlite3**, an unused offline path. All20 application tables compared row-for-row equal to the snapshot; restored integrity=ok, foreign-key errors=0, projects10, memory50, tasks2. The temporary offline database is not active and is not committed; it may disappear on reboot without affecting source data/backups.

After the Git checkpoint receipt is recorded, final root documentation will be projected once more into this project's private memory and saved to **/home/codex/.local/share/linko-bot-manager/backups/manager-pre-reboot-20261010.sqlite3**. This canonical final snapshot is the path used by the handoff/checklist. Final creation/reopen/isolated-restore verification is the last checkpoint step; do not overwrite an existing destination. Draft snapshots and four earlier daily snapshots remain preserved, with no pruning. The primary Git checkpoint records only non-sensitive documentation, not these databases.

Before committing, a second df measurement showed829526016 available bytes (~791MiB), still98% usage. This is a later snapshot than the initial833515520-byte check; small fixture/checkpoint files changed space during verification. No existing data was deleted.

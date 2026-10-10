# Current handoff — compact navigation deployed, 2026-10-10

This section supersedes the older pre-reboot-only instructions below. Current authorization was to implement/test/deploy the compact navigation and create a Git checkpoint; that delivery is complete. Work remains in /home/codex/projects/Linko-Bot-Manager, main; use git log/status to identify the latest navigation checkpoint. No remote push.

UI now has exactly two compact sticky rows at all widths. Controls moved to the project menu/Settings. SQLite schema4 adds display_name and last_used_at; original name, ID, path, repository, agent/thread/task associations and memory directory remain stable. Recency is based on accepted meaningful submissions/assignments, not opening/renaming/polling. All Projects remains null/global context. Duplicate display labels include original name/short ID. Blank rename resets fallback. Dropdown uses case-insensitive display-name sorting with keyboard/touch support. Drafts survive context switches in the current page session; Memory/Git detail is fetched only on demand, and async switching is guarded.

Verified35 tests, Ruff, node syntax and expanded Chromium browser checks. Mobile393×851 navigation92px, conversation600.25px (previous351px); desktop1440×1000 content begins92px (previous192.98px). Smaller393×420 viewport keeps composer reachable. /tmp holds before/after screenshots; scripts/browser_smoke.py repeats comparison using incoming checkpoint990ed95fc35724a699e103ee7a238aba97ca22c2. Physical Android keyboard/safe-area behavior remains unverified; one pre-existing dependency warning remains.

Deployed through the established dedicated systemctl --user restart, with a verified pre-deployment backup at /home/codex/.local/share/linko-bot-manager/backups/manager-navigation-20261010-100802.sqlite3. Private HTTPS https://codex-server.tail5ebb9d.ts.net:8787 serves exact tested assets; service active/PID594062; auth boundary and Tailscale443/3000 preserved. No dependency, security, infrastructure or unrelated-service changes.

Actual production now has10 projects,9 conversations,8 messages,11 tasks and an owner account. Compared pre/post deployment identities/history/task/auth/policy records successfully; SQLite integrity/foreign keys and all memory exports pass. Scheduler configuration was preserved and there were no unfinished executions before restart. Current172GiB free/18% used supersedes old disk observations. Separate durable reboot/storage tasks were preserved; this work does not certify full Stage6 or a VM reboot. Refresh only this project's private memory with revision checks/Tasks.sync after development-document changes.

# Fresh Codex session handoff — pre-reboot2026-10-10

## Start here without conversation context

Work only in /home/codex/projects/Linko-Bot-Manager, origin git@github.com:Linkoge/Linko-Bot-Manager.git, branch main. Read this file, PROJECT.md, STATE.md, TASKS.md, IMPLEMENTATION_PLAN.md, then MASTER_SPEC.md. Detailed actual runtime inventory is docs/inventory.md; ten-step post-reboot checklist and restore commands are docs/operations.md; enforcement boundaries are docs/security.md. Use existing documents rather than creating competing project-memory files.

Incoming verified implementation HEAD was **0ccfaa6ebd955efe02d6968301cb875c9b463e8f**. Working tree was clean when checkpoint preparation began. Documentation-only checkpoint reference is recorded below after commit. Use `git status --short`, `git log -8 --oneline` and `git rev-parse HEAD` to identify any subsequent receipt commit/changes. No branch merge or remote push was performed. MASTER_SPEC.md must remain byte-for-byte preserved; SHA256 **cad1c23a5bd4ce8a521fc2fbdc12c1130076b63b8a72dd48f07266330c8377f7**.

## Immediate next action

The owner is preparing to reboot the VM. The checkpoint task explicitly forbids new features, deletions, service restart/stop and automatic reboot. If the reboot has happened, execute the read-only post-reboot checks in docs/operations.md and record actual evidence. If it has not happened, preserve the running environment and wait for owner steering. A planned reboot is not proof of successful reboot acceptance or permission for an agent to reboot.

Prior boot ID: **e3a37e08-1b0b-4ac0-a923-8b6b97767873**, started2026-10-08 17:26:16 local. Compare with `/proc/sys/kernel/random/boot_id`. Do not mark Stage6 complete until new-boot/service/data/web/recovery evidence passes and storage remains suitable. Both acceptance gates persist as blocked DB tasks; their old blocker wording is historical, not a current measurement.

## Verified delivery and current state

Stages1–5 have local verified checkpoints for supported capabilities. Stage6 hardening/private deployment is partially verified. Authentication, responsive dashboard, scoped persistent chat, project memory, Codex launch/resume/streaming, tasks/leases/dependencies, automatic scoped scheduling, exact approvals and independent verification are implemented. Critical unresolved work is in TASKS.md. Additional dependency/integration/deployment capabilities are unavailable; do not claim the permanent specification is fully delivered.

Production DB: **/home/codex/.local/share/linko-bot-manager/manager.sqlite3** (schema3, WAL,0600; containing directory0700). Confirmed ten projects,50memory records/exports,two blocked tasks,one implementation checkpoint. All50 exports existed and matched the DB. **No production conversations/messages/executions/Codex thread IDs/approvals/operations/owner/policy overrides exist**. There is no production active worker to resume. Empty histories are the actual baseline, not lost histories. Scheduler defaults paused, maxworkers2, one active worker/project. Stored override policies are empty; code defaults remain effective. Recovery tests/model smoke fixtures are separate from production state.

Root PROJECT/STATE/TASKS/DECISIONS/HANDOFF files describe development. Runtime memory/PROJECT_ID/*.md is the private managed projection of structured project memory. When root development docs change, refresh only this project's DB memory with revision-checked Registry.update_memory and Tasks.sync; do not overwrite other projects or direct-edit generated projections. SQLite task state remains authoritative. Private snapshot paths and restore evidence are in docs/inventory.md. The canonical checkpoint snapshot is **/home/codex/.local/share/linko-bot-manager/backups/manager-pre-reboot-20261010.sqlite3**, verified286720bytes with all20 tables matching live/restored state. A subsequent manager-pre-reboot-20261010-evidence.sqlite3 preserves this final receipt/evidence projection. DB snapshots/workspaces/Codex storage must stay outside Git.

## Runtime and operational constraints

- Private UI: **https://codex-server.tail5ebb9d.ts.net:8787/** via Tailscale, proxying127.0.0.1:8787. Existing Chatwoot443/3000 listeners and SSH/RustDesk/Tailscale configuration must be preserved. No public Funnel.
- Dedicated user units: linko-bot-manager.service; linko-bot-manager-backup.service; linko-bot-manager-backup.timer under ~/.config/systemd/user. Manager enabled/running, timer enabled/active, last backup successful; linger=yes; tailscaled enabled/active. Correct absolute AI_MANAGER_CODEX=/home/codex/.local/bin/codex persisted because default systemd PATH omits ~/.local/bin. Manager uses repository .venv/bin/python, private umask, NoNewPrivileges and cgroup-owned cleanup. These settings are verified; full boot survival is not yet verified.
- Python3.12.15 via uv, uv0.12.23, Codex0.161.0. Dependencies and exact installed versions are in docs/inventory.md and uv.lock; preserve .venv and its uv interpreter. Do not reinstall/change dependencies as an incidental checkpoint/boot check.
- The owner must choose initial credentials privately via `uv run --offline python -m ai_manager.cli setup-owner` or one-use local-token UI setup. No real owner exists; setup_required=true. Never read/print tokens/auth.json/password hashes/private keys or invent a password.
- First current disk measurement **833515520 available bytes (~795MiB /834MB),98% usage**. Previous verified availability ~445MB/99%; owner reports ~800MB and manual deletions, with exact paths unverified. During final checks free space rose to1335345152bytes (~1.24GiB),97%; cause unverified and no agent deletion occurred. Re-measure after boot rather than relying on an older snapshot. No deletion during checkpoint preparation. Largest measured consumers are listed in docs/inventory.md; size alone is not deletion authorization. Keep large-task scheduling paused.
- Backup timer makes local daily verified SQLite snapshots without pruning/off-VM transfer. Same-disk snapshots do not protect against VM disk failure. Preserve isolated workspaces and ~/.codex/sessions/state store separately for resumable executions; do not copy credentials into Git.
- Existing /home/codex/projects/AI-Manager is unrelated Chatwoot, not this manager. Preserve its prior dirty docs/whatsapp-coexistence.md and all unrelated repos/tmux/Codex activity. Unrelated session context might be lost on reboot; this handoff covers this repository's development, not their unsaved work.

## Execution, policy and recovery model

Codex App Server is an owned JSONRPC stdio subprocess using existing ChatGPT authentication, with account/catalog validation and persisted logical thread IDs. No separate paid API is enabled. Standard tier requested; historical actual GPT-6.1 Sol low/medium chat/resume/planning/coding/review passed. Do not assume catalog presence proves inference access for every model.

Workers have independent physical lifetimes, durable task leases and isolated shallow local clones/branches. PID+/proc start identity prevents unsafe signaling after PID reuse. Task output goes to verifying; completion requires trusted registered tests and independent file inspection/evidence. Browser disconnect does not cancel work. Confirmed interruption pauses; uncertain process/transport/service loss blocks. Startup recovers chat/workers/orphan role turns, cancels outstanding approval grants, discovers repositories and refreshes memory. Reconcile actual process/thread/Git/workspace before deliberate resume. Never repeat an uncertain destructive/non-idempotent operation automatically.

Available brokered operations: scoped reads, hash-checked writes/deletes, owner-registered isolated tests, isolated Git commits. Approval binds exact args/hashes/execution/tool call/policy epoch, expires15min and is single-use. Tests use bubblewrap without host home/credentials/network. Built-in shell/patch/plugins/MCP/apps/subagents are disabled. Global defaults allow reads/tests and ask for edits/commits/deletes. Dependency installation, merge/push/deploy, infrastructure/network/credential/external-service/communication/payment capabilities remain unavailable regardless of ALLOW. Trusted Python/App Server are same-user host processes; do not claim a separate Unix-user compromise boundary.

## Verification and continuation

Existing test rerun during checkpoint: **31passed in7.56s**, one Starlette TestClient/httpx deprecation warning; Ruff/JavaScript syntax pass. Prior actual service-crash recovery/backup restoration and real-model/browser checks passed; no new service restart/browser/LLM turn was run now. Commands:

```bash
cd /home/codex/projects/Linko-Bot-Manager
uv run --offline pytest -q
uv run --offline ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
```

TestClient needs approved native execution if the managed sandbox thread portal stalls. Do not leave hung test processes running. Existing disposable real-subscription scripts: smoke_codex.py, smoke_worker.py, smoke_orchestration.py, smoke_coding.py; browser_smoke.py verifies desktop/Android. Do not run them by default during read-only reboot checks; use the no-turn App Server connectivity command first. No credentials/logs/generated DBs/test artifacts belong in commits.

After post-boot checks, update actual STATE/plan evidence and next-task priorities, preserve unresolved blockers, refresh this project's private memory, create a local Git checkpoint, then continue only within current owner authorization. Never ask the owner to resend the master requirements or manually transport prompts between stages.

## Documentation checkpoint reference

**Complete development checkpoint: bd6a5c6455174b8bec6afaa9b5d853f601940b7b** (main, documentation only). Its parent implementation checkpoint is0ccfaa6ebd955efe02d6968301cb875c9b463e8f. This reference is recorded in a separate documentation receipt commit; the receipt records this hash and final backup verification evidence in existing documentation only; no implementation/configuration changes. Run `git log -2 --oneline` / `git rev-parse HEAD` to identify that later receipt HEAD. No commit is required to contain its own hash. No push was performed.


## Current handoff — execution reliability, 2026-10-10

Read docs/execution-reliability.md and the latest STATE/TASKS/DECISIONS sections first; older pre-reboot/empty-history/low-disk passages above are historical. Real fixture task 4961242df6d14bf49f9018933f593fb3 completed through Codex, exact ASK approval, bubblewrap unittest, independent file review, dashboard and manager restart. 48 tests plus Ruff/JS/Chromium checks passed. The dedicated production service runs corrected code (PID 675325), after a verified 101769216-byte pre-deployment backup; histories, task dispositions, grants/policies/settings and authentication were preserved.

No production tasks were completed or requeued. 5db40e44 and 5b9a10fd remain verification-blocked; c78c0078/8589648a/ae1ba454 wait on their chain. Reboot/storage originals remain cancelled; actual boot changed and storage is now healthy. No duplicate investigation exists. Production projects still have no trusted verification commands. Owner must select adequate commands and resolve the isolated uv/dependency runtime before retrying verification. Existing host/browser task acceptance also exceeds broker capabilities. Do not equate registering a syntax check with satisfying that acceptance.

Use task-adjacent Configure verification → explicit owner argv registration → Retry verification for confirmed completed worker output. Use Resume only for interrupted saved threads and require evidence for uncertain outcomes. Inspect execution records exposes task/thread/turn/workspace/operations/approvals/tests; generic requeue cannot bypass history. Scheduler enablement/scope/budget remains the autonomy gate.

Private artifacts: ~/.local/share/linko-bot-manager/reliability-evidence-20261010; backup: backups/manager-execution-reliability-20261010.sqlite3. Isolated fixture DBs/screenshots/logs and owner-approved operation receipts stay outside Git. All original production events retained. No unrelated infrastructure/settings or security changes. No remote push.

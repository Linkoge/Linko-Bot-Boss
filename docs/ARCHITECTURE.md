# Architecture overview

A short, plain-language map of how the application works, for the owner and for new agents. [README.md](../README.md)
stays the detailed reference; this page links to its sections instead of copying them. It describes the code as it is
now, except the clearly marked [Planned](#6-planned-not-built-yet) section.

## 1. Big picture

```
owner ──chat──▶ manager ──delegate──▶ worker queue ──▶ checks ──▶ merge ──▶ deploy ──▶ GitHub backup
```

1. **Owner** talks to the **manager** in a chat (a web page/PWA, `ai_manager/static/`).
2. The **manager** is an AI turn (Codex or Claude) that reads project state and acts through a fixed set of actions
   (delegate a task, read history, To-Dos, deploy, ...). Code: `ai_manager/chat.py`, `codex.py`, `providers.py`.
3. A delegated job becomes a **task** in that project's **queue**: one running task per project, strict order, different
   projects in parallel. The **worker** gets its own Git worktree and branch, edits, adds permanent tests and commits.
   Code: `ai_manager/core.py`, `orchestration.py`, `runner.py`, `pipeline.py`.
4. The application (never the worker) runs the **checks**: the project's permanent tests plus the fixed smoke suite
   (`scripts/worker_check.py` is the same check as a self-check). A failure goes back to the same worker as a **repair
   round**, at most 5, then the task stops and reports.
5. Passing work is **merged** to `main`, then **deployed** (full tests, health check, rollback on failure). Code:
   `deploy_auto.py`, `deploy_setup.py`, `git_commands.py`.
6. Each project is **backed up to GitHub** by the application. Code: `github_backup.py`, `github_sync.py`,
   `github_projects.py`.

Rules for agents live in [RULES.md](../RULES.md). Details: README
[How work runs](../README.md#how-work-runs), [GitHub backups](../README.md#github-backups),
[Deploy](../README.md#deploy).

Other components worth knowing:

| Part | Files |
| --- | --- |
| Web server and API (FastAPI) | `ai_manager/web.py`, `auth.py` |
| Database and migrations | `ai_manager/db.py`, `history.py` |
| Settings and paths | `ai_manager/config.py`, `model_settings.py` |
| Worker isolation (separate Linux user) | `ai_manager/isolation.py`, `fence.py` |
| Auto mode and follow-ups | `auto_mode.py`, `followups.py` |
| Chat memory and compaction | `chat_memory.py` |
| Harnesses | `harness.py`, `harnesses/` |
| Instances registry and per-instance settings | `registry.py`, `instance_models.py`, `code_view.py` |
| Health, doctor, emergency repair | `doctor.py`, `recovery.py`, `scripts/emergency_repair.py` |

## 2. Environments

One copy of the code can serve several **instances**. Each instance is only settings: its own data directory (database,
login, workspaces), port, systemd unit and env file. There is no per-user routing inside the app; each instance has
one owner login. See README
[Multiple instances](../README.md#multiple-instances-and-the-restricted-work-instance).

- **Main**: the owner's instance, reached over Tailscale, with the full UI (projects, tabs, harness panel, usage,
  deploy, GitHub, Compact/Archive).
- **Other instances** (for example **kaxa**): each is a *restricted instance* with its own database and its own login
  on a website address. They run in **office mode**: `/api/auth-status` reports `office` and the UI is one clean chat
  screen (project row, tabs, harness button, usage display and the Compact/Archive menu items are hidden by CSS only;
  server-side restrictions are the real protection). Chat, voice, attachments and automatic background compaction keep
  working.
- **Environment picker.** In Main's harness panel (the ☼ button), an *Environment* selector lists Main plus every
  instance in the deploy registry (`DATA/deploy/installed.json`, `instances`). For an instance it sets that
  instance's manager harness, autonomy and manager model/effort by writing into the instance's own
  `manager.sqlite3`; the instance reads them at its next manager turn, with no restart. If the database cannot be
  opened the panel says so and changes nothing. Code: `ai_manager/instance_models.py`; see
  [Harnesses](../README.md#harnesses), "Environments (Main only)".
- **Registration and isolation.** Instances are registered in the deploy registry (created by the install/provision
  scripts, see [INSTALL.md](INSTALL.md#6-optional-extra-instances)). An instance running as another Linux user sees the
  code only through a root **host adapter**'s bind-mount list (outside the repo). Every top-level path the app reads
  at runtime must be in that list and in `RUNTIME_PATHS` (`ai_manager/code_view.py`); deploy and doctor check it before
  a restart. A new top-level runtime directory therefore needs the host adapter updated too.

## 3. Harnesses

A **harness** is a folder (`harness.json` + a prompt file) that decides the prompt, the allowed manager actions and the
default autonomy. Built-ins live in `harnesses/`; the owner can add more in `AI_MANAGER_HARNESS_DIR`.

- **Manager harnesses**: **Linko manager** (`linko`, default, the full manager) and **Zero** (`zero`).
- **Zero** is a plain agent for brainstorming: only RULES.md rules 1, 3 and 10, a short prompt, the delegate,
  task-control, read/inspect and To-Do actions, no deploy, host repair, pause/resume or auto-mode actions, and Ask
  autonomy by default.
- **Worker harness**: **Coding** (the only one so far): worktree, permanent checks, repair rounds, then merge and deploy
  by the application. Each task records its harness once, when it is created.
- **Autonomy**: `ask` (report and wait) or `finish_request` (bounded follow-ups plus auto mode).

No harness can change what the code enforces (checks, merge/deploy by the application, restricted-instance
rejections). Details: README [Harnesses](../README.md#harnesses). Code: `ai_manager/harness.py`,
`ai_manager/static/harness.js`.

## 4. Chat memory

Code: `ai_manager/chat_memory.py`. A chat is long; the manager only sees recent messages plus memory. **Compaction**
turns an old chat into a summary and a few memory items.

**When it runs.** One click on **Compact** in the chat menu, or automatically once the chat passes the token threshold
(default 150,000 tokens, estimated as characters / 4; allowed 10,000 to 1,000,000). It runs as a **background job**,
one per chat, never during a running manager turn; the chat stays usable and shows "Compacting chat in the
background...". Messages sent meanwhile stay after the summary.

**What happens.**

1. The **full chat is archived first** as a normal named chat archive (nothing is deleted; it stays searchable with
   Search in chat, `list_history`, `read_history`).
2. The compaction model reads the newest chat text (up to 200,000 characters) and the current memory, and returns JSON:
   a summary and a list of memory items.
3. The server validates the result, applies it, and the chat **continues from the summary message**.

**Compaction model setting.** Settings record `chat_compaction`: model (default *Latest Haiku*, resolved to the newest
Haiku in the catalog at call time), effort (default `low`) and the automatic threshold in tokens. The settings panel
and the compaction use the same `route()`, so they cannot disagree.

**Memory items.** Two tiers, per chat scope (one project, or All Projects):

- **Long-term**: lasting facts (decisions, preferences, how things work).
- **Short-term**: current state that may become outdated.

For every item the compaction model answers with one action: **new**, **update**, **keep** or **drop**. Each compaction
that keeps or updates an item counts as a reuse and refreshes its last-used time; a short-term item reused **3 times**
is **promoted** to long-term (`PROMOTE_AFTER`).

**Capacity, not age.** There is no time-based expiry. Each tier has a cap (**50** long-term, **30** short-term); when a
tier is over its cap, its **least recently used** items move to the memory archive with reason `capacity`. Items the
model drops go there too (reason `dropped`). When a tier is nearly full the compaction model is told, so it can merge
items.

**Searchable archive.** Archived items are not deleted. The manager sees only a count and can search them with
`read_memory` (path `chat_memory_archive` plus a query). Archived chats are searched with the history actions.

**Who receives what.**

- *Compaction model*: memory as plain numbered lines (`L1..` long-term, `S1..` short-term), the newest chat text and the
  tier-fullness note. It answers with those refs; the server maps them back to real item ids. It never calls tools.
- *Chat (manager) model*: the item texts only, as plain lines under the two tier headers, plus the archived-item count and
  how to search it. No ids, refs or bookkeeping.

Secrets are masked before text reaches either model. README: [Data and history](../README.md#data-and-history).

## 5. Data

- **Data directory**: `AI_MANAGER_DATA`, default `~/.local/share/linko-bot-manager` (per instance). It holds the SQLite
  database, task workspaces (`assignment-workspaces/`), the deploy registry (`deploy/`), harnesses added by the owner
  and temporary working folders (for example `chat-compaction-workspace`).
- **SQLite database**: `manager.sqlite3` in that directory (`ai_manager/db.py`, schema migrations run once at start).
  Projects, chat messages, tasks, task events, To-Dos, conversations and chat archives, and the `memory` and
  `settings` tables live there. History is kept with lossless compression only.
- **Settings records**: key/value JSON rows in `settings` (for example `chat_compaction`, `model_choice`, `machines`,
  harness choices, `chat_compaction_notice:<chat>` and `chat_autocompact:<conversation>` state).
- **Chat memory**: the `memory` table, two rows per chat scope: `chat_memory` (active items, sent to the manager) and
  `chat_memory_archive` (archived and dropped items).
- **Chat archives**: `conversations` plus `chat_archives` rows; the compaction archive is one of them.
- Git worktrees and branches (`linko/assignment-<id>`) are kept on disk beside the project, not in the database.

README: [Data and history](../README.md#data-and-history).

## 6. Planned, not built yet

> **Not implemented.** Nothing in this section exists in the code today. It is a plan for the office environment.

- **Chat sidebar** in office mode with **multiple chats**: search, pin and delete, and a **New chat** button.
- **Titles**: each chat gets a title from Haiku, set **once** from its first message.
- **Files** are saved with their chat and read only when needed, not sent with every turn.
- **Memory on leaving a chat**: when the owner leaves a chat that has enough new messages, its memory is summarized,
  with a **cool-down** so it does not repeat too often.

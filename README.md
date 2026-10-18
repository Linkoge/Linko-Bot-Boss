# Linko Bot Boss

A private, single-owner conversational manager for software projects: **you → manager → one worker → merge → deploy**.
FastAPI + SQLite + plain JavaScript; Codex or Claude does the thinking and coding. The installed names stay unchanged
(`ai_manager`, `AI_MANAGER_*`, the service, the data directory and the private URL
`https://codex-server.tail5ebb9d.ts.net:8787/`). Repository: `Linkoge/Linko-Bot-Boss`.

## How work runs

One queue per project: at most one running task per project, in strict queue order, while tasks of different
projects run in parallel (no global cap, no exceptions per project). States: **queued → running → done | failed**. A
**held** task is a queued task with the hold flag set; it is skipped until released. Order within a project is the only
dependency. The code is `ai_manager/core.py`; the rules both the manager and every worker read are [RULES.md](RULES.md).

1. **Worker.** The oldest eligible queued task gets an isolated Git worktree and branch
   (`linko/assignment-<id>`, kept on disk, never deleted). The worker implements, adds the permanent tests for its change
   and commits. Projects that are not Git repositories run in place, with no checks and no merge.
2. **Checks.** After every worker turn the application runs the permanent checks: the one fixed smoke suite
   (`python -m pytest -q -m smoke`, 180 s) plus the test files the branch added or changed (600 s). Only permanent
   repository suites can block delivery; one-off probes are informational. Probes run under a 60 s timeout. Checks run
   with disposable data directories.
3. **Repair rounds** (a counter on the task row, never reset by retry, restart or continue). A failing check goes back to
   the same worker as a repair round, at most 5 times; the task then stops with a plain reason and the manager reports it.
4. **Merge and deploy.** A passing branch always merges into `main` under the checkout fence (one merge at a time across projects, the
   deploy request is made under the same fence hold); there is no per-project switch. Main is first merged into the branch; a conflict goes to the same worker as a repair round of the
   same task. Every project with a saved deploy setup then requests the same deploy script automatically, including
   the manager itself. Without one it reports **no deploy setup; merges only**. The manager's `deploy` action
   (optionally with a `task_id` of a finished, never-merged task) uses the same path on request.
   After every passing merge the application schedules a GitHub backup push for that project. This runs independently
   of the queue and deployment; workers never push. See GitHub backups and secret checks below.
5. **Queue runner** (`ai_manager/runner.py`; independent of any open chat or owner message). A tick every second starts
   the oldest eligible queued task of every project that has nothing running, so a project runs one task at a time and
   different projects run in parallel. Not eligible: held, project paused, waiting for its retry time, or a
   deploy request pending (for at most 30 minutes, see Deploy). On service start, whatever was running is resumed
   without penalty (same branch, same worker thread, repair counters untouched) and retry times are forgotten, so a restart or reboot always retries at
   once; every task that was running (in any project) is resumed this way. A provider usage limit or a lost connection
   never fails a task: it goes back in the queue with a visible retry
   time and is tried again every 5 minutes, indefinitely (no backoff, no wait counter, the provider's reset time is not
   consulted, so a limit that resets early is picked up by the next try). Only real errors fail a task. The watchdog (every
   minute) requeues a dead or hung worker (30 min without activity) once, then fails it; fails queued tasks of a removed
   project; withdraws a deploy request that is still waiting after 30 min; and reports an idle-queue stall: eligible work
   (not held, project active, retry time passed, no deploy pending) that nothing has started for 5 minutes gets a
   `queue.stalled` event and a visible reason on the task (for example a deploy or merge holding the live checkout), once per
   stall. Held, paused, retry-waiting and deploy-waiting work is never a stall. Nothing stays blocked without a visible reason.

Owner controls on a task: **Hold / Release** (queued), **Cancel** (queued or running; the branch is kept),
**Continue** appends instructions to queued/held tasks and releases a hold; failed/done tasks re-queue at the end,
with counters preserved. Manager `continue` accepts added instructions in `objective`, `details`, or both;
distinct text from both fields is appended to the original objective and reaches the worker's execution input,
including when resuming the retained thread. `acceptance` appends criteria without replacing earlier requirements.
A worker may ask the owner a question; the task stays running and says so.
Finished results return to the originating chat automatically.

**Progress indicator.** The owner can always see that something is still in progress. `ai_manager/progress.py` derives
one phase from existing state (task and execution rows, `task.check_started`/`task.check` events, the deploy
`request.json`/`state.json`; no new state, gate or wait) and `/api/portfolio` returns it as `progress`
(`active`, `phase`, `label`, `project_id`, `task_id`, `since`), scoped to the selected project or all projects. Phases, most advanced
first: a running task (`agent` "Working"/"Starting", `checks` "Running checks", `repair` "Repair round N of 5",
`merge` "Merging", `question` "Waiting for your answer"), a pending deploy (`deploy`: "Deploying: running tests / waiting
for idle / backing up / restarting / updating", "Health check", "Rolling back"), an interactive manager turn
("Responding"), a queued task ("Queued", "Queued to merge", "Waiting to retry"; held and paused work is not progress), and
the hand-off of a finished result to the chat ("Reporting result", at most 5 minutes). Done, failed, cancelled, deployed,
rolled back and withdrawn all clear it, and the final result message appears as before. Bounds: lease reconciliation and
the 30-minute deploy withdrawal end their phases; a deploy request older than 3.5 hours, or whose script process is gone and
silent for 2 minutes, is not shown. The chat shows one pill (`#progress-chip`) over the composer, in the position of the Stop
button, so it takes no layout space; it is replaced by the message badge and Stop button while the manager streams a reply,
rides the existing portfolio polling (so it survives reload: shown = saved) and hides itself after 90 seconds
without a successful poll. Permanent coverage: `tests/test_progress.py`, `tests/test_progress_browser.py`.

Project manager chats receive the registered repository's real `project.path` and use that directory as their
working directory, including projects other than Linko Bot Boss. All Projects includes each repository path and
requires `project_id` for code inspection. Questions and advice are answered directly after reading the relevant code;
only changes, tests or other execution work are delegated to the worker queue.

Both providers use the same read-only `manager_action` file actions: `list_files` lists one directory,
`search_code` searches literal, case-sensitive text with file/line references, and `read_document` reads source or docs
in 12,000-character pages (including files over 100KB). Paths may be relative or absolute inside the selected
repository; traversal and symlinks escaping it are rejected. Listing/search pages have 50 entries. Search skips binary
files and generated/dependency directories (`.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `vendor`, `build`,
`dist`) and has a five-second budget; narrow `path` when needed. Explicit reads/listings can still inspect these
directories. Manager tools cannot edit files, commit, or execute project code. Codex uses its read-only sandbox with
shell and patch tools disabled; Claude exposes only Read (also used for attachments) and the application tools.
The project brief and repository path travel together. The scratch directory is used only for All Projects and
attachment storage. Permanent coverage: `tests/test_manager_repository.py` and `tests/test_claude_provider.py`.

## Manager screenshots

Before giving UI/design advice, the manager reads the relevant project code and takes screenshots of the actual
page with `manager_action` action `screenshot`. Both Codex and Claude receive native image blocks, not image paths
or base64 embedded in text. Select a project (`project_id` is required in All Projects) and provide an HTTP(S) `url`.
Only pages reachable from the server can be captured, including localhost previews and Tailscale sites when the
host has access. Login-protected pages, session/cookie import and automated login are out of scope.

The screenshot engine is the existing Dev Panel feature reworked into `ai_manager/screenshots.py`: real viewport
scrolling, lazy-content preparation, image/font readiness, and the panel's session image contract are retained.
There is one browser engine shared by the manager and the Dev Panel compatibility launcher. No server-side
project code or shell command supplied by the model runs; Chromium renders the requested page in a fresh context.

| Request fields (plus `action: "screenshot"` and `url`) | Result |
| --- | --- |
| Omit `tiles` and `selector` | One small whole-page layout thumbnail (at most 320×900), CSS-pixel `page_height`, `screen_count`, and viewport size/scale |
| `tiles: "1-3,8,last"` | One viewport image for each selected one-based index; inclusive ranges and comma-separated indexes; `last` always reaches the end |
| `selector: "#product-grid"` or `selector: "product grid"` | One viewport centered on a unique visible CSS match, accessible landmark/heading/control name, common id/class name or exact text |
| `selector: "footer"` | The footer's centered viewport, clamped at the page end |
| `device: "mobile"` | 390×845 CSS pixels at 2×, producing 780×1690 images |

Desktop defaults to **1920×1080**, one screen per tile. The last tile clamps to the page end and can overlap the
previous tile. Named elements must be unique; use a specific CSS selector for ambiguity. Sticky headers may repeat.
The model downscales images to about 1500 pixels on the long side, so taller multi-screen tiles would lose detail.
At most **8 tiles per call**, with no eight-tile limit on the page: a 15-screen review uses `1-8` then `9-15`.
Each call renders the current page again; repeat the overview if content/layout changed between requests.

Approximate image cost is **1.5–2k tokens per tile**. A quick check is the overview plus 1–2 relevant tiles;
a full review reads all tiles (about 22.5–30k tokens for 15). The small overview is for layout only, not text/detail
judgment. Select middle and end tiles as needed, rather than judging only the first screen.

Every call has a **60-second hard wall-clock timeout**, including browser startup, navigation, preparation and
capture. Timeout/cancellation kills the owned browser process group and discards temporary files. The manager
receives a clear failure; a page that keeps growing or exceeds the finite 100-screen preparation walk fails explicitly.
Failures do not enqueue work or block the project queues. Images are returned to the model and temporary
capture files are removed; they are not saved as project files or login sessions.

Install the runtime dependencies with `uv sync --locked` and Chromium with `uv run playwright install chromium`.
An existing Chrome executable can be selected with the retained `LINKO_PANEL_CHROME` environment variable.
To switch the installed Dev Panel to this **same** engine at deployment, set its existing screenshot executable
setting to the absolute executable launcher path, for example in its service environment:

```ini
Environment=LINKO_PANEL_NODE=/home/codex/projects/Linko-Bot-Manager/scripts/dev_panel_capture.py
```

The launcher accepts the panel's existing `node capture.mjs` invocation and stdin/stdout JSON contract; it runs the
shared engine instead of executing the old `capture.mjs`. It selects Boss's `.venv/bin/python` and enforces the same
hard timeout. The panel keeps its configured pages, overlap/sticky-stage sampling, saved sessions, duplicate filtering,
selection/download interface and latest-success recovery. Its existing Node/Chrome availability checks and terminal
Node dependencies still apply. Apply this setting when deploying the feature so the old engine is no longer used;
do not maintain a second capture implementation in Dev Panel. Permanent coverage is `tests/test_screenshots.py` (real local Chromium pages, transport,
Dev Panel compatibility, selection, per-call limits, and a stuck-page timeout).

## Architecture design principles

This section is included verbatim in the manager prompt. It is advisory only: briefly evaluate each new feature
against it and warn of concrete complexity or reliability risks (extra states, conflicting settings, hidden routing,
unbounded waits). Advice never blocks answering, delegation or delivery, requires no approval or review receipt,
and adds no model or routing restrictions. Proceed with the owner's requested work.

Prefer deletion and one clear path. Add no new gate without a concrete incident it prevents; speculative safeguards
create their own failure modes. Keep one source per setting and **shown = saved = dispatched** across reload and execution.
No feature should stall a project queue; keep waits visible, bounded and recoverable.

Deliberately removed machinery and its replacements:

- **Model restrictions / permitted pairs; Pin/Global:** hidden pair policies and overlapping controls could reject or
  misrepresent a saved choice. Manager and worker are independent; any installed catalog model/effort is valid.
  One global manager/worker record remains. A repo lock owns a complete copy; unlocked repos follow global.
  Manual fixes the worker; Automatic lets the manager choose from the catalog. A task retains its first execution pair.
- **Auto-deploy toggle:** a second delivery policy could strand passing work. Passing Git branches always merge;
  every project with one saved deploy setup requests deployment automatically. Without a setup report "no deploy setup"
  and merge only. Explicit deploy uses the same backup, mandatory health check and rollback path, including this manager.
  Never improvise a normal deploy through emergency repair.
- **Repair checkpoints:** extra stop/resume states obscured progress. Permanent checks return failures to the same
  worker for at most five durable repair rounds; retry, restart and Continue never reset that counter.
- **Delivery/brief bookkeeping:** manager-written delivery receipts and brief-upkeep actions duplicated application
  state. The application records results and returns them to the originating chat. The project brief remains read-only
  navigation for the manager, with owner-run review; it is not assignment authority or a delivery prerequisite.
- **Chat modes; tiered/review machinery:** mode switches, helper/reviewer tiers and integration/follow-up paths
  multiplied handoffs and waiting states. The manager answers, inspects or delegates as asked to the project's worker queue;
  permanent checks and the existing secret check remain, with no separate architecture approval stage.
- **Deploy blocking the queue:** checkout safety still pauses new tasks and merges while a deploy is pending.
  Before backup, a 30-minute deadline withdraws a stuck request and releases the queue; after backup, deployment finishes
  or rolls back. Queued, held and retry-delayed tasks do not block restart; active work does. Preserve this bounded behavior.

Ship small rollouts: replace one path at a time and delete the old path in the same change, updating its tests and docs
in place. Give a one-line before/after summary, for example: "Before: two worker selectors could disagree; after:
one saved selection drives display and dispatch."

## GitHub backups and secret checks

The owner has authorized automatic application pushes, including verified existing backlog. Every Git project uses its
single `origin` push URL on `github.com` (SSH or HTTPS). Projects created as Personal or Company use that tier's saved
token over HTTPS; existing projects without a tier use the service user's Git credentials. The application pushes local `main` (legacy `master` where that is the project's main branch) to the same remote branch. It never forces,
resets local commits or rewrites history, and the backup itself never creates repositories or changes remote
configuration (only the owner's menu actions below do). Non-Git projects keep
their in-place workflow. Missing/unsupported origins or authentication failures need attention, without holding work.

`ai_manager/github_backup.py` runs network work in a background thread without the checkout fence. A merge requests an
attempt; failures retain all local commits and retry every five minutes, and a later merge requests another attempt.
Startup also schedules repositories without a backup receipt, so safe existing backlog does not require another task.
Durable per-project receipts in SQLite settings (`github_backup:<project-id>`) preserve pending work and retry times
across restarts. Project tabs show **GitHub needs attention** on failure; project API/manager records include the reason,
retry timestamp and safe findings. Backup failures do not fail tasks, consume repair rounds, or prevent merge/deploy.
A later successful push includes all retained commits. Each Git command has a 60-second timeout and no interactive
credential prompt. Raw Git/SSH/hook output is discarded, since it can contain credentials.
An untouched repository with an unborn `main` or `master` and an empty GitHub remote is synced with reason
`empty-repository`; no artificial commit is created. Existing remote history, other local refs or uncommitted files
still require attention when the local main branch is missing.

Before the first push, and every subsequent attempt, the application queries the actual remote branch and scans every
unpushed commit, including intermediate secrets deleted later. An empty remote requires scanning all local history;
shallow history, unavailable history or a diverged remote refuses only the backup. The verified commit ID is pushed
explicitly, so changes arriving during a push stay pending for another attempt. Local and remote commits remain intact
on rejection. A secret in existing backlog stays needs-attention for owner remediation; the application never rewrites
that backlog or bypasses GitHub protection.

Delivery also checks **only `main..branch` commits**, after committing worker changes and syncing main, before merging.
The built-in scanner (`ai_manager/secret_check.py`) checks added lines for obvious GitHub/OpenAI tokens, AWS access-key
IDs, private-key headers and long literal credential assignments. It flags changed `.env`/`.env.*` files, `*.pem`, `*.key` and
`id_rsa*` filenames at any depth (`.env.example` is permitted). Unchanged historical lines are outside the delivery
scope; merge resolutions are checked without treating inherited main changes as task additions. Findings enter the
existing five-round repair loop; each finding contains only **file, line, rule** (line 0 for filenames). The worker must
remove the credential from every affected unmerged task commit, including intermediate commits. There is no owner
approval gate. This is a small obvious-secret check, not comprehensive credential discovery. Gitleaks may be used by an
operator separately; the application needs no optional scanner, installation, download or external service to check.

Never commit credential-looking values, even fake ones (RULES.md rule 10): real credentials come from environment
variables or ignored files outside Git, and tests/fixtures generate values at runtime, keep them under 12 characters, or
avoid password/secret/api_key/access_token/passwd names. Fix a flagged dummy value; do not allowlist it.

Only fixtures that cannot be changed (e.g. file-format samples) can use a committed `.linko-secret-allowlist.json` in
each repository. Its JSON array has
exact entries like `{"path":"tests/fixtures/token.txt","line":1,"rule":"github-token","reason":"synthetic fixture"}`.
Use line 0 with rule `sensitive-filename` for fixture names. Paths are repository-relative; no globs, regex exclusions,
wildcards or whole-file content bypasses. The delivered tree's policy applies to its history, so an approved fixture
can be added together with its exact entry. Entries require a nonempty reason; malformed policy fails with a safe rule.
Review allowlist edits as code: this mechanism is for fixtures, never live credentials. It applies to both delivery and
backup, and never to another repository.

The backup fixture audit (2026-10-16) classified all 14 originally reported findings as **dummy**. A full-history
scan at `caaa0d0` additionally found six audio-test fixtures, for 20 distinct path/line/rule findings; all 20 are
**dummy**, with no real credential found. The exact entries in this repository's `.linko-secret-allowlist.json`
cover only those historical findings under `credential-assignment`; scanner rules and matching behavior are unchanged.
Line numbers below are historical added-line coordinates, so they may differ from the current source. Each value was
reviewed in its test/harness context, including the deleted files; credential values are omitted from this report.

| Finding path | Historical line(s), each classified dummy | Evidence |
| --- | --- | --- |
| `.data/auto-deploy-bootstrap/review.diff` | 1336: dummy | Copy of browser setup fixture in archived review diff. |
| `scripts/browser_audio.py` | 23: dummy; 34: dummy | Synthetic audio key; temporary localhost harness and fake provider. |
| `scripts/browser_auto_deploy.py` | 54: dummy | Setup password for temporary localhost deployment fixture. |
| `scripts/browser_deployment.py` | 49: dummy | Setup password for temporary localhost deployment fixture. |
| `scripts/browser_fetch_recovery.py` | 44: dummy | Setup password for temporary localhost recovery fixture. |
| `scripts/browser_tasks.py` | 65: dummy | Setup password for temporary localhost task fixture. |
| `tests/test_audio.py` | 44: dummy; 194: dummy; 222: dummy; 247: dummy; 274: dummy; 298: dummy | Synthetic audio keys; mocked providers/transports, upload deadlines, forwarding and safe-error assertions. |
| `tests/test_deployment.py` | 240: dummy | TestClient login in temporary deployment admission test. |
| `tests/test_hardening.py` | 169: dummy; 173: dummy | TestClient setup/login in temporary secure-cookie test. |
| `tests/test_web.py` | 30: dummy; 33: dummy; 46: dummy; 55: dummy | TestClient authentication fixture and rejected setup requests in temporary test data. |

`.data/` is ignored by Git and has no tracked files in the delivered tree. Its historical review diff remains in
history; the single reviewed fixture entry above permits that finding without excluding `.data/` from scanning.
No history rewrite or push is needed for this remediation. Future findings require individual review; if a real
credential is found, stop and report it without committing its value or adding an exception for it.

Enable GitHub's server-side protection as well: repository **Settings → Advanced Security** (under Security and quality)
→ enable **Secret Protection**, then enable **Push protection** in that section. Availability depends on repository and
plan; an organization may manage this setting. Follow the current
[GitHub setup instructions](https://docs.github.com/en/code-security/how-tos/secure-your-secrets/prevent-future-leaks/enable-push-protection).
Provision the service user's normal write access to each origin separately. A protected push rejection stays
needs-attention and retries; no bypass URL or secret value is exposed or acted on automatically.

Permanent synthetic regression coverage is in `tests/test_github_backup.py`: detection and exact allowlists, safe
diagnostics, optional scanner absence, commit scope and intermediate history, bounded repairs, first-push backlog,
rejected pushes and later catch-up, diverged history, and queue/merge/deploy progress during backup failure or latency.

### Create and rename a project on GitHub

The project menu (▾ left of the model picker, main instance only) groups projects/stashed projects, project actions,
and **Settings** with dividers. **All Projects** is always available at the end of both the project tab strip and the
menu's project list; it has no visibility checkbox. Project tabs otherwise keep their recent-use order, while menu
projects are alphabetical. On mobile the menu fills the visible viewport with a sticky **Projects** header and close
button; its content scrolls independently. Compact checkboxes keep larger tap targets. Close or Escape returns focus
to the menu button, and keyboard navigation stays inside the open menu.

**Change display name** only edits the label; the repository address stays directly underneath it.
**Rename project** appears only when the selected project's token tier is configured. **Create project** is followed
by a single **Settings** entry, which opens the settings view at **GitHub Settings** with the Personal token field
focused, alongside the Company token and organization fields. The menu has no duplicate GitHub settings shortcut or
disabled rename hint. The token forms are absent from the main project navigation. Restricted instances continue to
hide the project menu, All Projects, and GitHub settings.
The repository actions remain create and rename; **there is no repository delete code or route**. The API client
refuses every request except `GET /user`, `GET /user/memberships/orgs/{configured-org}`, `GET /orgs/{configured-org}`,
`POST /user/repos`, `POST /orgs/{configured-org}/repos` (exactly the saved organization only), and
`PATCH /repos/{owner}/{repo}` (`ai_manager/github_projects.py`).

*Credentials.* **GitHub Settings** has separate **Personal token** and **Company token** password fields, each with
**Save**, **Validate**, **Replace** and **Remove**. Save validates a new token; Replace validates and replaces the saved
token only on success. Validate checks the saved token again without returning it to the browser. Remove deletes only
that tier's local credential file; it does not revoke the token on GitHub or delete a repository. Each form shows the
connected username and validation status, including after reload. Pasted values are cleared after an operation and
never stored in browser local/session storage.

Both tokens must be GitHub **classic** personal access tokens with **only the `repo` scope**, leaving `delete_repo`
unticked. The server checks `GET /user`, rejects fine-grained tokens and rejects every scope other than `repo`.
`repo` is broad (read/write access to repositories). Personal uses `~/.config/linko-bot-manager/github-token`
(override `AI_MANAGER_GITHUB_TOKEN_FILE`); Company uses the sibling `github-company-token`
(override `AI_MANAGER_GITHUB_COMPANY_TOKEN_FILE`). Files have mode 0600 in a private 0700 directory, outside Git.
The existing single file is migrated **in place as Personal**, preserving its path and contents; it is never copied
into Company. Validate once to populate the legacy token's username/status. No token is put in SQLite, events, logs,
API responses, tasks, prompts or Git configuration. Token request validation errors also omit submitted inputs.

Company validation checks membership and public repository creation policy for the configured organization when
GitHub exposes them with `repo` scope. Known inactive membership or a policy denial blocks validation. If GitHub does
not expose membership/policy, the UI says permission could not be fully checked; actual creation remains authoritative.
No extra token scope is requested. Revalidate after changing the organization.
The [organization membership API](https://docs.github.com/en/rest/orgs/members#get-an-organization-membership-for-the-authenticated-user)
and [organization details API](https://docs.github.com/en/rest/orgs/orgs#get-an-organization) document these permission
checks and their scope limitations. See the
[GitHub organization repository API](https://docs.github.com/en/rest/repos/repos#create-an-organization-repository).

Each tier uses its own token for **create, rename and application backup push**. A missing token disables its create
option, hides its menu rename action, and blocks rename/push at the API. The create dialog points to GitHub Settings;
there is no fallback to the other token or SSH.
Stored remotes retain the existing SSH URL style. For tiered projects, the backup worker uses the equivalent HTTPS URL
and an ephemeral askpass helper; the credential is passed only in the network subprocess environment, never in command
arguments or a remote URL. The helper contains no credential and is removed after the operation. Existing projects
without a saved tier retain their service-user Git credentials. Secret checks and non-forcing backup behavior remain.

*Destinations.* Save the single **Company organization** in **Settings → GitHub**, alongside the two token entries.
Use its GitHub name (1–39 letters/digits with single hyphens between them), not a URL. The saved value is the value
shown in Settings and used for creation. Company needs both the Company token and the organization. Leave it blank
to make Company unavailable; the create dialog points back to Settings. The main project list has no organization field or tier labels.

*Create project*: choose a name and **Personal** or **Company**. Personal always creates a **private** repository in
the token owner's account via `POST /user/repos`. Company always creates a **public** repository directly in the
configured organization via `POST /orgs/{configured-org}/repos`. The dialog requires an explicit “will be public”
confirmation. For Company, the manager prepares `<first project root>/<name>` with `git init -b main` and one initial
`README.md` commit, then runs the existing secret check over its full history **before** creating the public GitHub
repository. Findings or a failed check block creation and remove the prepared folder. On success it sets `origin` and
registers the project so the existing backup can push. Personal creates on GitHub before initializing and registering
the local project. The backup secret check still runs before pushes for both tiers. No transfer, copy, mirror,
publication or destination-change action exists. A small Personal/Company label accompanies the selected project's
repository information in the project menu and Settings; the saved tier survives rename and organization changes.
Labels apply to repositories created through this flow.

*Rename project*: (1) rename the GitHub repository under its actual owner (GitHub redirects old links); (2) rename the local folder; (3) update `origin`, repair retained task worktrees, and update the
project's name, path and repository in the manager. Rename sends only the new name to GitHub and never changes an
existing repository's visibility. A display name equal to the old name is cleared; a custom one stays.
Rename holds the checkout fence (no task starts, merge or deploy meanwhile) and is refused while the project has a
running task or execution, a saved deploy setup or pending deployment (those embed the folder path), or when it is the
manager's own live checkout.

**Cancel** (or Escape) closes an unsaved create, rename or other shared editor without validating fields, sending a
request or refreshing the project. Once **Save** dispatches an operation, both buttons and Escape dismissal are
disabled until it finishes; closing a dialog cannot undo an operation already sent. A failed save keeps the editor
open with the error so the owner can inspect the result, retry or cancel.
A successful save closes even if the following project refresh fails; that read failure is reported separately.

If a step fails the action stops, undoes what is safely reversible (renames are renamed back, a just-created empty
folder is removed) and returns the per-step report (`steps`: `done`, `failed`, `rolled back`, `not run`). A repository
created on GitHub cannot be undone by the application (no delete): the report says so and it must be deleted or
reused on GitHub. Restricted instances reject all GitHub routes (`/api/github*`) with `Not available on a restricted
instance`. The push-time secret check is unchanged.

Tests: `tests/test_github_projects.py` covers mocked GitHub creation, rename, fixed tiers, saved organization,
confirmation and secret checks, rollback, token lifecycles and permissions, legacy migration, each tier's push
credentials, token-free responses/logs and restricted routes. `tests/test_github_projects_browser.py` covers shared
editor cancellation and saves, public confirmation, unavailable destinations, Settings persistence, both token
lifecycles, menu labels/grouping and repository details, All Projects ordering, stash/restore, keyboard focus,
viewport fit and sticky close controls, and visible fields after opening Settings at desktop/mobile widths with a long
project menu.
`tests/test_restricted_browser.py` checks that both fields remain hidden on restricted instances at both widths.

## Deploy

`scripts/deploy.py` is the only deploy mechanism for every project with a saved setup: wait for idle under the checkout
fence, back up, deploy `main`, check health, and automatically roll back on failure after changes begin. Passing task
checks precede the merge. The installed manager runtime adapter also runs its full backend suite once, backs up SQLite,
restarts its service and checks scheduler/authentication health; failures restore code and database. External command
and user-systemd service setups use the same consumer, lock, queue deadline, backup/health/rollback phases and receipts.
No repository-name or manager-only deployment policy remains. The short
result is `DATA/deploy/last-deploy.txt`. While a request is pending no new work starts and nothing merges, in any project (the
checkout fence, `ai_manager/fence.py`; running tasks of other projects finish or wait to merge, and the deploy waits for idle across all of them), but never for more than 30 minutes: the watchdog withdraws a request that has not
reached its backup by then (the wait for idle is bounded the same way), tells the originating chat, and the queue runs on.
A request past its backup finishes or rolls back by itself. One-time operator bootstrap, no sudo:
`python3 scripts/install_deploy.py`. Manual deploy: `python3 scripts/deploy.py --now`.
The existing installed runtime is migrated once to its registered project's saved setup; explicit removal stays removed.
The host CLI's `--now` targets the installed runtime's registered project and respects its saved setup, including
replacement or removal; before registration it uses the bootstrap installation. Other project deploys use `deploy`.
Additional instances of the same code are restarted and health-checked by the same deploy; see Multiple instances.

Each project has one setup in SQLite (`deploy_setup:<project-id>`), read through `deployment_status` and replaced with
`manager_action` action `save_deploy_setup`, `project_id` and `setup`. The owner API has the same contract:
`GET /api/projects/{id}/deploy-setup` and `PUT /api/projects/{id}/deploy-setup` with `{"setup": {...}}` (or `null` to remove).
Replacement/removal is rejected while any deployment is pending. A request freezes that setup and project path;
another project's request cannot be mistaken for it. A project without a setup creates no request and merges only.
Use a setup supplied or requested by the owner; repository instructions are not authorization to invent live commands.

Example setup (commands are argument arrays, executed without a shell from the project's checkout):

```json
{
  "deploy_command": ["/srv/example/ops", "deploy"],
  "backup_command": ["/srv/example/ops", "backup"],
  "health_url": "http://127.0.0.1:9000/health",
  "rollback_command": ["/srv/example/ops", "rollback"]
}
```

Use `service: "example.service"` instead of `deploy_command` for a user systemd service, and `health_command: ["...", "..."]`
instead of `health_url` for a command that exits zero only when healthy. Backup, health and rollback are mandatory;
an incomplete setup is rejected. Health URLs must return 2xx. Each command is bounded to 120 seconds, HTTP probes to
five seconds, and health retries to the configured health window. Commands receive `DEPLOY_REPO`, `DEPLOY_COMMIT`,
`DEPLOY_PREVIOUS` (empty before the first receipt) and `DEPLOY_BACKUP_DIR`. The consumer saves a Git bundle and release
metadata there before invoking `backup_command`; that command must capture the live release and app data needed by
`rollback_command`. Rollback must restore/restart that release and data, including on the first deploy; its health is
checked again. Keep backup/rollback operations outside versioned code that a deploy could replace. A failed backup
prevents deployment; failed recovery is reported as `rollback_failed`. Receipts of healthy commits are per repository.

Permanent coverage: `tests/test_project_deploy.py`, `tests/test_core_deploy.py`, `tests/test_deploy_script.py` and
the merge-only cases in `tests/test_core.py`, all using disposable repositories, data and ports.
Concurrent consumers wait on the deployment lock and re-read the request after it is released; a systemd consumer can
resume a request abandoned by a manual caller. During the idle wait, `DATA/deploy/state.json` records the deploy PID,
blocker counts and bounded record identities, refreshed at least every 30 seconds (also in `deploy.log`). Queued,
held and retry-delayed tasks remain durable and do not block restart; unfinished executions, task leases, running
tasks, streaming messages and running deliveries do. Stale leases are reconciled by the queue runner only after
both the runner and its recorded process are gone, preserving workspaces and limiting recovery retries.


## Public access through a tunnel

The service listens on `127.0.0.1` only (uvicorn runs with `proxy_headers=False`); a tunnel such as cloudflared
forwards its public hostname to it. Single owner, username/password login; there is no Google sign-in.

- **Hosts**: `AI_MANAGER_ALLOWED_HOSTS` (localhost and the Tailscale name) is always extended by
  `AI_MANAGER_PUBLIC_HOSTS` (comma-separated; no built-in default, so the main app accepts only what is configured;
  a restricted instance lists its own hostname in `AI_MANAGER_ALLOWED_HOSTS` or `AI_MANAGER_PUBLIC_HOSTS`). Other `Host` headers get 400; a cross-site `Origin` on a write gets 403.
  There is no CORS: the API sends no CORS headers.
- **Client IP**: `CF-Connecting-IP`, then the right-most untrusted `X-Forwarded-For` hop, are believed only when the
  direct peer is in `AI_MANAGER_TRUSTED_PROXIES` (default `127.0.0.1,::1`; IPs or CIDRs). Otherwise the peer address is
  used. Login and setup-token attempts are throttled on that real address (10 per IP and 100 in total per 15 minutes),
  so one visitor cannot lock out the owner behind the shared tunnel address.
- **Cookie**: `ai_manager_session` is `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` when
  `AI_MANAGER_SECURE_COOKIE=1` (default) or the request is https (including `X-Forwarded-Proto: https` from a trusted
  proxy). Sessions last 30 days by default (`session_seconds`, overridden by `AI_MANAGER_SESSION_SECONDS` in seconds).
  Active sessions refresh their database expiry and cookie together at most once per hour (half the lifetime for
  lifetimes under two hours). This applies to the main and per-user/restricted instances; idle sessions past expiry
  are rejected. Logout immediately deletes the session and expires the cookie with the same attributes.
- **Setup**: `POST /api/setup` answers 409 as soon as an owner exists, without using a throttle slot.
- **Headers** on every response: CSP (`frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy: same-origin`,
  `Permissions-Policy` (microphone for this origin only, for audio recording), `Cache-Control: no-store`, and
  `Strict-Transport-Security` over https.

Permanent coverage: `tests/test_public_access.py` and `tests/test_sessions.py` (disposable data; cookie security,
sliding renewal, expiration and revocation in both instance modes).

## Multiple instances and the restricted work instance

One shared copy of the code can serve several instances. Each instance is only settings: its own data directory (database,
login, workspaces, setup token), port, systemd unit and env file. There is no per-user routing inside the app, so each
instance has one owner login (`python -m ai_manager.cli setup-owner` with that instance's env). The main instance
(coding) needs no new setting and behaves exactly as before: all values default to `AI_MANAGER_DATA`
`~/.local/share/linko-bot-manager`, `AI_MANAGER_PORT` 8787, `AI_MANAGER_PROJECT_ROOTS` `~/projects`. The audit found no
other hard-coded port, path or hostname: the Tailscale name above is documentation, and `scripts/install_service.py`
only asks Tailscale for a hostname when `--hostname` is not given.

A **restricted** instance (`AI_MANAGER_RESTRICTED=1`, the single mode setting) is for work that is not coding: Word
documents, essays, forms, email, browsing. It requires `AI_MANAGER_PROJECT`, one existing folder, and refuses to start if
that folder contains or sits inside the application code or the instance's data directory (no self-edit). Workers still
run in their usual branch/worktree flow on that one project (a Git repository). The application, not the prompt, enforces:

- **One project.** `POST /api/projects`, `POST /api/projects/discover` and a status change to `archived`/`removed` are
  rejected; startup registers only the fixed project and hides any other project left in an older database. There is no
  All Projects view: every project-scoped request (portfolio, chat, history, To-Dos, attachments, project routes) is
  the fixed project when `project_id` is omitted and a 403 for any other id.
- **No deploy.** Deployment status/cancel, deploy-setup read/save, and the manager actions `deploy`,
  `deployment_status`, `save_deploy_setup` are rejected; a stored setup is ignored, so merges are merges only.
- **No host agent.** `GET/POST /api/repair` and the manager actions `emergency_repair`/`repair_status` are rejected; no
  host-repair spool is written. Do not install `scripts/install_repair.py` for such an instance.
- The manager is offered a tool without those actions, and manager and workers get a short restricted note
  (`RESTRICTED_NOTE` in `ai_manager/codex.py`). `/api/auth-status` reports `restricted`, and the UI then hides All
  Projects, the project menu, the model/effort picker button, the Models settings panel, the GitHub panel,
  Discover/Add project and the Deployment panel (cosmetic: `body.restricted` CSS only, elements stay in the DOM so no script breaks; the server-side
  rejections are the real protection). Word editing and browsing tools stay
  allowed. Rejections use `Not available on a restricted instance` (HTTP 403, or a rejected manager action).

The per-project GitHub backup keeps working through the existing path and its secret check stays on. The synced folder is
a Git repository only for that: put `.git` in the folder's Syncthing ignore list (a `.stignore` file in the folder root
containing `.git`) so Syncthing never syncs repository internals, and add large binaries to `.gitignore` so they are
neither committed nor pushed.

**Create an instance** (the host side, such as its Linux user, the Syncthing folder, Cloudflare Tunnel and Access, is set
up separately and is not done by the application):

1. Make the folder a Git repository with a `main` branch and an `origin` on GitHub if backups are wanted.
2. Copy `deploy/instance.env.example` to `~/.config/linko-bot-manager-NAME.env` and set `AI_MANAGER_PROJECT`,
   `AI_MANAGER_DATA`, `AI_MANAGER_PORT` (a free port, not 8787) and `AI_MANAGER_ALLOWED_HOSTS` (its public hostname).
3. `python3 scripts/install_service.py --instance NAME --start` installs the template unit
   `linko-bot-manager@.service` (from `deploy/linko-bot-manager@.service.in`) and enables
   `linko-bot-manager@NAME.service`. Create its login with the env file loaded
   (`set -a; . ~/.config/linko-bot-manager-NAME.env; set +a; python -m ai_manager.cli setup-owner`).
4. `python3 scripts/install_deploy.py --instance NAME` adds it to the deploy list (see below).
5. Daily database backup of an instance: run `scripts/backup_daily.py` with that env file loaded.

**Deploys cover every instance.** `DATA/deploy/installed.json` (the saved runtime setup of the main instance) gets an
`instances` list: `{"name", "service", "data", "url"}` for each, written by `install_deploy.py --instance NAME` from that
instance's env file, and kept on re-runs (edit the file to remove one). One deploy request then: waits for idle in
every instance database (blockers are labelled `NAME:`); holds every instance's checkout fence from the final idle check
until all are healthy again; snapshots every database (`backups/<id>/NAME/before.sqlite3`, `stopped.sqlite3`); stops the
main service and every instance, updates the shared checkout, starts them all; and health-checks each one with its own
URL and database (scheduler running, expected commit, authentication boundary, working session). Any failed check rolls
back the shared code and restores every database, restarts all and checks them again. The 30-minute bounded wait,
cancellation and receipts are unchanged. An instance under another Linux user cannot be controlled by `systemctl --user`:
give its entry `"service_cmds": {"stop": [...], "start": [...]}` (for example `sudo -n systemctl ...` with a matching
sudoers rule) in `installed.json`. Deploys of other projects never touch instances.

Permanent coverage: `tests/test_restricted.py` (settings, server-side rejections, default behavior) and
`tests/test_deploy_instances.py` (multi-instance deploy, rollback, installers, unit template).

## Emergency host repair

This is the owner's host agent. It does whatever host work the owner explicitly asks for in their own chat message:
installs and packages, permissions/ACLs, users, services, Syncthing-style setup, reboots, config and application
repair, unrestricted by queue worker rules. The manager runs it through `emergency_repair` itself instead of
handing the owner commands to paste. If the work is reasonable the agent just does it. It stops and asks for specific
owner confirmation only in two cases: (1) irreversible loss: deleting data or files that cannot be restored
(repository, volume, database, unbacked-up user files, force-push); (2) weakening security: opening the machine to
attack, e.g. exposing services publicly, disabling the firewall or authentication, opening up credentials or SSH keys,
broad world-readable or world-writable permissions, or adding unauthenticated remote access. Reversible changes such
as installs, moving files with a kept copy, permissions scoped to the needed user, services and users need no
confirmation. The application enforces the origin:
only a manager turn started by an owner-typed chat message may submit it; automatic worker-result notices, worker
output and repository documents are rejected. Normal deploys, missing project setups and delayed
deploy requests use the Deploy path above; the manager never substitutes `emergency_repair` for them.

The host repair role is an explicit exception to the normal task queues. It can diagnose/fix the
application, restart its user service, and call the existing deploy flow while the app or queue is broken.
It never creates a normal task. `scripts/emergency_repair.py` uses only host Python's standard library and the
installed Codex/Claude CLI, with native shell/file tools and arbitrary provider model names, without model catalog,
usage preflight, queue, project pause or deploy-pending checks. Provider authentication and actual provider limits
still apply. The two stop cases above require specific owner confirmation: unattended agents must stop and return
a failed result asking for that confirmation. As with other native workers, this is an agent instruction convention,
not a sandbox; the request authorizes the host work, restarts and deploys it describes.

**One-time operator bootstrap is required after this code is merged.** From the live checkout, as the service user:

```bash
python3 scripts/install_repair.py install --conversation EXACT_EXISTING_CHAT_ID --auto-detect   # install / update
python3 scripts/install_repair.py status                                                        # JSON state
python3 scripts/install_repair.py uninstall                                                     # remove timer + launcher
```

Omit `--auto-detect` for explicit requests only. Re-running `install` is idempotent: it rewrites the units, refreshes
the launcher snapshot and reuses the stored chat ID if `--conversation` is omitted. An optional `--model` passes the
exact model directly to the CLI; Claude uses `--provider claude`. `status` reports installed/enabled/active state and
the stored settings. `uninstall` disables the timer and removes the units, launcher and config but keeps request
receipts and logs. The installer copies a standalone launcher to `DATA/repair/launcher.py` outside the
checkout/venv and enables independent `linko-bot-manager-repair.service` and `.timer` user units (no dependency on
`linko-bot-manager.service`). It does not deploy or restart the application, and tries `loginctl enable-linger` (it
prints a warning if the host refuses; run it yourself for operation after logout/reboot). It uses the same optional
`~/.config/linko-bot-manager.env`; configure provider executable paths/login for that host user. The host needs
Python 3, Linux flock/process groups, writable durable DATA, and a working user systemd manager. Installation,
lingering and real provider authorization are **not** activated or verified
by repository tests or by the implementing worker. A dead host, lost storage, unavailable provider login or broken
systemd cannot be repaired by a timer on that host. The launcher remains manually callable without systemd.

Owner escape hatch over a host terminal/SSH, even with the app stopped (replace DATA and the chat ID):

```bash
python3 DATA/repair/launcher.py --data DATA request --key owner-incident-1 \
  --conversation EXACT_EXISTING_CHAT_ID --instructions-file /path/to/repair-instructions.txt
python3 DATA/repair/launcher.py --data DATA status
python3 DATA/repair/launcher.py --data DATA tick
```

`request` durably accepts only; the timer wakes within about a minute, or `tick` runs immediately. Do not launch that
manual tick inside the application's service cgroup if you intend to restart it: use an independent terminal or the
repair systemd unit. The manager's `emergency_repair` action accepts an `objective` and returns its
receipt; `repair_status` reads receipts. Authenticated owner endpoints `POST /api/repair` (request_id, instructions,
conversation_id, optional provider/model) and `GET /api/repair` also bypass task checks, with existing CSRF protection.
Those entry points need a responding app; the host launcher is the service-down entry point. The exact original
conversation ID is retained, including archived chats; missing chats are never silently replaced.

Requests, instructions, attempts, status, errors, final result and pending chat notices live in fsynced atomic JSON
receipts under `DATA/repair/requests`; native JSONL/output logs live under `DATA/repair/runs`. The request key is
idempotent forever. A different request while a repair is active is rejected with its receipt ID; its instructions
are not silently discarded. A host-wide flock permits one repair at a time; the CLI inherits it across launcher
failure. Verified orphan process groups are killed only after their recorded 30-minute deadline. The systemd unit
also kills its own cgroup at 35 minutes. Interrupted attempts consume the same durable five-attempt budget; completed
native results are reconciled from logs before another turn. Otherwise the retained native session is resumed with
instructions to inspect effects/receipts before repeating operations. A crash before a native session ID was emitted
can require a new session; retained logs and reconciliation instructions still apply. This does not promise exactly-once
execution of arbitrary shell commands. A crash between CLI spawn and PID receipt can leave an unidentifiable orphan
holding the lock: the unit's cgroup timeout handles installed runs; a manual operator must inspect that process before
terminating it. The launcher never breaks an unproven lock to start a duplicate.

Connection/timeouts and usage/rate-limit errors retry at 1, 2, 4, 8 minutes (one-hour delay cap); five total attempts,
30 minutes per turn, with counters and retry deadlines preserved across restart. Other errors fail immediately.
There is no provider fallback or indefinite usage-limit loop. Unattended detection, when enabled, checks app health
and read-only task activity each timer wake. Thirty minutes of app outage or running task silence requests a repair;
pending owner questions are excluded. A durable incident key prevents relaunching a completed/failed repair for the
same incident; recovery followed by a new outage is a new incident. Inspect host status if automatic repair fails;
an owner may submit a new key after addressing the cause.

Status and success/failure notices are delivered without another model turn: after app health recovers, the host
launcher inserts stable-ID assistant messages into the exact original chat in a SQLite transaction. An absent/offline
app keeps delivery pending without consuming delivery attempts. Database/destination failures back off, up to ten
attempts with a one-hour delay cap, retaining the error and result on the host. After fixing delivery (or restoring an
older database), `python3 DATA/repair/launcher.py --data DATA retry-delivery` explicitly replays retained notices;
stable message IDs prevent duplicates. A stopped timer requires manual `tick` for delivery too.

The agent can invoke `python3 scripts/deploy.py --now --emergency` on the host. This uses the same full suite,
checkout fence, SQLite backups, health check and code/database rollback as normal deployment. It stops the app under
the fence and refuses to proceed while any recorded native worker is alive, then bypasses stale queue rows without
mutating them; service startup retains normal queue recovery. Existing pending deploy consumers remain serialized
by the deploy lock. A durable emergency intent for the exact request wakes a consumer already waiting for idle;
that consumer performs the same emergency stop and worker check on its next poll, without waiting for the queue. This mode still needs a readable database, the existing deploy installation,
repairable checkout/dependencies and a known previous release for code rollback. The agent must diagnose those
failures instead of claiming this flag fixes corruption or bypasses backup/health safeguards.

Permanent coverage: `tests/test_emergency_repair.py`, emergency cases in `tests/test_deploy_script.py` and
`tests/test_web.py`: mocked CLIs, disposable spool/database, absent app/queue, deduplication, orphan lock, interruption,
bounded retries, detection, result delivery, bootstrap snapshot, deploy safeguards and rollback. No paid provider
probes, live database changes, service restarts or host installation are part of those checks.


## Product behaviour that is unchanged

- Chat has no modes: the manager does what is asked. Composer with recording/transcription,
  Discard/Upload, arbitrary attachments (only verified raster images become native image input), drafts and sequential Send.
- Model settings: one global manager/worker choice and optional repo locks. The header is
  **Manager · [current repo name] · ×**. The repo name is a picker-style toggle showing the active repository tab,
  clearly highlighted when ON. ON creates `model_choice:<project_id>` as a copy of current global `model_choice`;
  OFF deletes it completely. Re-enabling copies global again, with no prior repo state restored.
  One resolution rule applies everywhere: use the repo record if it exists, otherwise global. Every manager model/effort,
  worker Manual/Automatic and worker model/effort pick edits that scope. Unlocked repos share global changes; locked repos
  keep independent settings for chats, tasks, reports and memory reviews. Task reports use the task repo settings
  even when delivered to an All Projects conversation. All Projects edits global and has no repo toggle.
  `GET /api/model-settings?project_id=...` returns effective `settings`, `locked`, `repo_name`, favorites and catalog choices.
  `PUT /api/model-settings?project_id=...&locked=true|false` saves the same effective record with optimistic `revision`
  and scope checks. `PUT /api/projects/{id}/model-lock` accepts `{revision, locked}`. The old worker override API and UI
  are removed. Favorites store manager pairs; Insert reads the current scope and Recall edits the current scope.
  Manager and worker can use different providers. Per provider the picker shows a model row and effort row: Codex
  Luna/Sol/Astra and Claude Haiku/Sonnet/Opus, each family's newest installed model with Low/Medium/High. Missing
  families are disabled. Saved or favorited off-family models stay visible with their supported efforts. Dispatch accepts
  any catalog model/effort, with no usage-limit fallback or automatic model switching.
  **Manual | Automatic** remains in the Worker heading. Automatic stores `worker: null`; `manual` remembers the last
  fixed pair. New tasks then use the manager's explicit delegate model/effort, or provider defaults for omitted values;
  a fixed worker wins over delegate arguments. Tasks retain their first selected execution pair across later settings
  changes and record the actual model/effort used. Permanent coverage in `tests/test_model_picker_ui.py`,
  `tests/test_model_picker_browser.py` and `tests/test_project_worker_models.py` checks layout, toggle/scope, reload,
  migration, resolution and real thread/turn arguments. Earlier picker visual evidence remains in `docs/evidence/model-picker/`.
- Project list with display names, visibility/stash, pause, discovery; To-Do list (never executes by itself; Start hands the
  To-Do directly to one queued task); read-only project memory brief for the manager with owner-run review
  (temporarily doubled limits in `ai_manager/project_memory.py`: brief target 3000 / maximum 3200 UTF-8 bytes, whole project
  context ceiling `CONTEXT_MAXIMUM` 4000 bytes; chat reads the ceiling from there and errors state the actual size and limit);
  chat clear/trash/archive/search. **Archive all** saves the entire conversation and opens a fresh chat.
  **Archive part 10** saves all earlier messages while keeping the latest 10 visible messages
  (usually 5 owner and 5 AI) in the active chat. Both use a named, searchable archive and require
  confirmation with no response running. Partial archiving is unavailable with 10 or fewer messages;
  it resets the AI thread and summary so subsequent responses use the retained history.
  Message IDs, attachments, execution evidence, other projects and trash are preserved.
  Permanent coverage: `tests/test_chat_history.py` and `tests/test_archive_browser.py`.
- Installable PWA (`/` scope, no service worker), account usage display, Tailscale-only private hosting.
- Security: Argon2 owner login, one-time setup token, revocable sessions, CSRF/origin/host checks, CSP, throttling.
  See Public access through a tunnel below.
  Workers run as the service user with broad access; the rules ask them to confirm before irreversible loss.
  That is an agent convention, not a sandbox.

Chat opening (reload, context selection, reopening Conversation and authentication recovery)
positions the loaded history at its latest end after fonts, images and composer sizing
settle. A deliberate chat gesture cancels pending positioning. Subsequent streaming,
polling and sends preserve the reader’s scroll position; loading older messages keeps
the previously visible message in place. Permanent Chromium coverage lives in
`tests/test_chat_open_start_browser.py`, including 155-message histories and delayed layout.

**Project ideas** opens from the first group in the project menu, above the project
list. It is a global notebook for projects that do not exist yet, separate from
per-project To-Dos. Add free-form idea/instruction text (up to 24000 characters) and
an optional title (up to 200 characters). A blank title uses the first nonblank text
line, with whitespace collapsed and trimmed to 80 characters; no model is called.
Descriptions are collapsed by default. Each entry has Copy, Edit and Delete;
Delete asks for confirmation. Copy puts the title, a blank line and the complete
original description on the clipboard, ready to paste into chat. To use an idea,
create the repository through **Create project**, then paste the copied prompt.
Nothing in this notebook creates projects, repositories, tasks or dispatches work.

Ideas persist server-side in the global `project_ideas` database table. The
owner-authenticated `/api/project-ideas` API supports GET/POST and PATCH/DELETE
`/api/project-ideas/{id}`; mutations use the standard CSRF and origin checks.
Restricted instances hide the notebook and reject all its endpoints with
"Not available on a restricted instance", preserving their single-project scope.
Permanent coverage: `tests/test_project_ideas.py` and
`tests/test_project_ideas_browser.py` (including clipboard, collapse, reload and
confirmation behavior).

To-Do **Start** uses authenticated, CSRF-protected `POST /api/todos/{id}/start` to queue
one task directly with its full title and details verbatim in the worker objective,
including multi-paragraph text and whitespace at the maximum saved length. The backend resolves the
source project’s effective worker selection (Automatic uses the provider default),
retains the original conversation and complete source, and archives the entry in the
same transaction that accepts the task. A durable per-entry handoff makes repeat clicks,
concurrent requests and retries after a lost response return the original task. Catalog,
model or persistence failure leaves the entry available for retry. The UI disables
pending Start controls and displays either the assignment ID/status or an actionable
error. `tests/test_todos.py` covers atomic failure/replay; the browser suite covers the
full click-to-queue path, duplicate clicks and lost-response recovery.

The manager's `save_todo` instructions in `ai_manager/codex.py` require complete worker
briefs with the goal, observed problem and why, all agreed chat decisions, concrete
details, acceptance criteria and relevant ordering. Workers do not receive the chat.
Each To-Do is one task; larger work uses numbered family parts (`1/3 <family>: <part>`),
each self-contained with its own criteria and order note, started individually by the
owner. Later refinements rewrite the existing entry and affected siblings by ID.
Every To-Do has a `kind` (`todos.kind`, schema 17): `idea` or `ready`. An **Idea** is an
unrefined capture that may be short; it is only discussed in chat, shows no Start button
and can never be dispatched (`POST /api/todos/{id}/start`, chat `todos` and
`delegate`/`continue` with `todos:[id]` all reject it with a message asking to refine it
first). When chat has refined an idea the manager rewrites it into a full brief with
`save_todo` (`kind: "ready"`), or the owner switches the Kind in the Edit dialog; a Ready
To-Do shows Start as before. The UI keeps one list with two groups, Ready first, then
Ideas, each entry carrying a kind badge. `list_todos` accepts an optional `kind` filter,
`save_todo` and `POST`/`PATCH /api/todos` accept `kind` (new entries default to `ready`,
updates keep the current kind). The schema 17 migration turned every old `Draft:`-titled
entry into an Idea and removed the prefix; there is no other "Draft:" convention and no
manager draft-expansion on Start. No extra approval gates.

## Whole-session audio recording

Microphone capture requires a secure context (HTTPS, or localhost for development),
MediaRecorder, microphone permission and a supported recording format. One app-level
session retains its original project and conversation across internal views,
project switches and stashing. In Conversation, the recording controls sit in the
composer bottom row immediately left of Send: elapsed time only, Upload and Discard.
Record, Attach file and Send retain their right-hand stack, size and visibility during
capture on desktop and mobile. The existing Record button toggles capture off; there
is no second Stop button. `tests/test_audio_continuity.py` checks unchanged button
bounds and the bottom recording row in real Chromium at desktop and mobile widths.
The destination remains available in the controls’ tooltip; after interruption,
Download/Retry and the recovery explanation appear in the same bottom row. Other
views hide the composer while the same capture continues. Toggling Record off transcribes the whole
recording once into the original draft; explicit composer Send retains the existing
whole-recording send flow. Navigation suppresses automatic Send. Before insertion,
the original conversation is checked; a replacement chat never receives its transcript.

Changing browser tabs/apps or document visibility does not stop capture. Delayed timers
are not evidence that the microphone stopped. Where available, a screen wake lock is
requested during visible recording, released on hide/stop/discard, and requested again
on return. Denial, low-power policy and missing wake-lock support leave capture usable;
the recording timer remains the only active status text. This API keeps a visible screen
awake; it does not grant background execution. See the
[Screen Wake Lock specification](https://w3c.github.io/screen-wake-lock/).

Recorder errors/stops, ended tracks and page freeze/departure retain captured audio
for the existing review/retry flow and remove the active indicator. A muted microphone
records a pause warning in the controls’ tooltip, continues the same recorder where
possible, and requires review after capture ends because audio may be missing. All
MediaRecorder data events are assembled into one Blob and one transcription request after capture ends: no separately transcribed
segments or partial transcript insertion. Existing IndexedDB recovery checkpoints are
best effort; final data may never arrive if the browser/process is killed. Download
important interrupted recordings before leaving. Ending capture or Discard releases
microphone, timers and screen lock, including a lock granted after the session has ended.

Permanent regression coverage: `tests/test_audio_continuity.py` uses real headless
Chromium capture with a fake microphone and transcription provider, internal navigation,
a second browser tab, visibility events, recorder/track interruption, and simulated
denied/unsupported wake locks, plus timer-only bottom-row layout and Record-toggle
stopping at 1200/393/320px. The browser suite generates disposable login credentials at runtime.
`scripts/browser_audio.py` additionally covers upload, Send, retry,
recovery and narrow-layout flows. Visibility simulation does not establish mobile OS
background support. Mobile app switching, screen lock, browser freezing, calls,
permission revocation and power policy can pause or terminate microphone capture.
Physical iOS/Android locked-screen behavior has not been verified here. Neither a PWA
nor screen wake lock bypasses OS/browser suspension; keep the app visible for the best
chance of continuity. Existing 20-minute/32-MiB limits still apply.

## Whole-message Copy and Delete

Each whole owner/assistant message in Chat and saved/archive history has one small, low-opacity
lower-right arrow after its text and attachments. Expand it to reveal Copy and Delete underneath.
There is no Edit. Keyboard activation is supported. Copy includes the entire source text across all
rendered paragraphs, lists, tables and code blocks; during streaming it includes all text received so far.
The existing clipboard fallback applies.

Delete opens a confirmation dialog with Cancel focused. Queued/running/recovering responses and their
owner messages must finish or be stopped first. The owner/CSRF-protected endpoint independently checks
confirmation and exact project/conversation/message membership. Deletion persists across restart and
removes the whole display message from Chat, saved/archive readers, search and chat copy exports.
Idempotent SQLite tombstones and an audit event retain native/provider history, internal prompt context,
execution output, tasks and attachments; this is not provider-side erasure. Polling removes deleted IDs
while preserving older undeleted rows outside the rolling history tail. Clear/archive/trash confirmation
uses the visible tail after deletion.

## Code block Copy

Every fenced code block in an assistant or worker message shows a small Copy button at its top-right. It copies
the block's exact text (not the rest of the message) and shows "Copied" for about a second and a half. It uses the
Clipboard API and falls back to a hidden textarea with `execCommand('copy')` on non-secure pages. The button is
created by the Markdown renderer, so streamed, polled, cached and saved-history messages all get it.
Permanent check: `tests/test_code_copy_browser.py`.

Permanent checks: `tests/test_message_actions.py`, `tests/test_chat_history.py` and
`scripts/browser_markdown.py` (streamed multi-block responses, owner/assistant copying, Cancel/confirm,
reload, saved-reader removal, keyboard and narrow layouts). Historical source and assignment evidence:
[retained Copy/Delete report](docs/MESSAGE_ACTIONS_978211.md).

## Run and develop

```bash
uv sync --locked
uv run playwright install chromium               # manager screenshots and browser regression tests
# browser_pwa.py opens a headed Chromium app window: on a display-less host run it as `xvfb-run -a uv run python scripts/browser_pwa.py`
uv run python -m ai_manager.cli setup-owner     # first time only
uv run pytest -q -m smoke                       # the fixed smoke suite, every task
uv run pytest -q                                # full suite: run by the deploy script
uv run ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. uv run python scripts/browser_smoke.py   # also browser_tasks.py and the other browser_*.py checks
```

Configuration: `AI_MANAGER_DATA` (default `~/.local/share/linko-bot-manager`), `AI_MANAGER_PORT` (8787),
`AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_RESTRICTED` and `AI_MANAGER_PROJECT` (restricted work instance, see Multiple instances), `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_PUBLIC_HOSTS`, `AI_MANAGER_TRUSTED_PROXIES`, `AI_MANAGER_SECURE_COOKIE`, `AI_MANAGER_CODEX`,
`AI_MANAGER_CLAUDE`, `AI_MANAGER_CLAUDE_MODELS` (comma-separated Claude model ids offered in the picker, default
`claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5`; any id the installed `claude` accepts), `AI_MANAGER_REPO` (live checkout, tests only). Daily database backup: `scripts/backup_daily.py`
(restore only to a fresh path with the service stopped; native thread storage and project files are backed up separately).

## Data and history

Existing projects, chats, To-Dos (including their hand-off records) and task/execution history rows are preserved. Schema 13 (the task queue) added columns to
`tasks` and closed every older non-final task once (failed, "Cancelled: closed when the task queue was rebuilt"; branches and
workspaces untouched). Old columns (`lifecycle`, `legacy`, …) and old settings keys are ignored history; the old per-provider model
settings (`model_settings`, `model_settings:claude`) are adopted once (the most recently edited one) into `model_choice`. Schema 14
dropped the per-project Auto-deploy values unread (older tasks that were left unmerged keep their branches and are never merged
retroactively; `deploy` with their `task_id` merges one on request); the chat mode column and manager brief-upkeep records
are ignored history. Schema 15 drops the unused columns `tasks.round_limit`, `open_round`, `checkpoints`, `merge_requested`
and `waits` and the stored `todos.revision`; no data is read from them. The earlier release/helper/tier/reviewer/integration/follow-up machinery is
gone; its history is in Git. Schema 16 migrates each explicit legacy `project_worker:<project_id>` override into a
full repo lock, combining its worker/manual with the current global manager. Inherited null overrides remain unlocked.
The old keys are deleted; migration runs once and never recreates a lock after OFF. Existing full locks take precedence.

History retention uses lossless compression only; there is no 90-day purge. The current chat loads
its latest **30 messages**; scrolling (or wheeling up) to the top retrieves preceding 30-message
pages without any visible control in insertion order. Saved-chat pages retain their 50-message **Load earlier messages** pages.
New manager threads receive the last 50 context
messages (plus the latest owner request if it falls outside that window); native thread continuity and
explicit historical search/read actions remain available. Hidden transport rows and display tombstones
keep their existing behavior.

Each loaded project's chat, parsed message nodes, older pages and reading position stay in client memory.
Switching back shows them synchronously, then immediately syncs in parallel with the portfolio request.
A bounded session-storage preview (latest 30 messages for up to ten contexts) also renders before the
first chat request after a page reload. It is cleared on logout/authentication recovery; storage failure
still permits the in-memory cache. Every preview syncs immediately, and failed syncs show the existing
retry status and retry through polling. Late responses from another project are ignored.

`GET /api/history?project_id=…` returns a latest page, `before=<message-id>` an older page, and
`since=<cursor>` only newer rows in ascending insertion order. `cursor` advances through bounded
catch-up pages while `sync_has_more` is true. Optional `watch=<comma-separated-message-ids>` refreshes
cached unfinished replies whose content/status can change without new rows (up to 100 IDs per request, automatically batched by the client).
`updates` carries those rows separately; completed cached bodies are not re-fetched. Deletion tombstones
and `first_position` reconcile deletion/partial archive, and `reset` plus a new tail reconcile an invalid
cursor or replaced conversation. Older pages remain in memory through ordinary delta refreshes.

Switch profiling (2026-10-16, disposable server/Chromium, 150 replies with 100 formatted paragraphs each):
the old return switch took **519 ms** unthrottled, or **3,056 ms at 6× CPU throttling**. In the throttled
run, SQLite history reads were 14–21 ms, the history round trip 23 ms, and Markdown rendering 2,725 ms.
Rebuilding parsed nodes dominated; the portfolio/history waterfall and per-message scroll/layout reads
also contributed. With cached nodes, deferred preview writes, parallel sync and one scroll restoration
per history batch, the measured return was **64 ms** to restore unthrottled; at 6× it restored in
**552 ms**, painted in **686 ms**, and completed background sync in **713 ms**, with **0 ms Markdown
reparsing**. Cold first-switch time at 6× fell from 3,429 ms (50 rows) to 1,600 ms (30 rows). These are
fixture measurements, not live-host timings. Reproduce with
`python -m pytest -q -s tests/test_chat_switch_browser.py`; it records query, API, render, restore and paint
times and verifies node reuse, immediate delta sync, reload previews, deletion/reset, older scrolling and
late-response isolation.

Task and worker history opens with the last **20 events** and up to **20 execution references** per page.
The Tasks and Agents menus each show **all unfinished work plus the latest 10 terminal cards** in the
selected repository, or across every repository and global manager executions in All Projects. Held,
queued, running and retry-delayed work stays visible regardless of age or count. Terminal tasks are
ordered by their last update; finished executions by finish time, with insertion order breaking ties.
**Load older tasks** / **Load older agents** add 10 older terminal cards using database cursors.
Refresh preserves the explicitly opened range and includes new finishes without duplicates; switching
scope or reloading resets both lists to the default. This SQL listing limit is independent of message/event
retention and introduces no archive state or deletion. Task headlines derive from the objective's first line.
The manager's `list_tasks` uses the same listing path with five terminal summaries by default, SQL
`query`/`status` filters and `next_offset` for older terminal pages; unfinished matches appear on the first
page. Compact summaries omit full criteria, gate logs and workspace/merge payloads. `read_task` and
`read_result` retain full retrieval. **Browse older executions**, task **History**, and worker
**History and full output** retrieve older records and full output on demand. Routine portfolio refreshes
include only a short result tail (at most 1,200 characters), execution metadata and task summaries;
they do not load historical prompts or inflate compressed results. Task records, full final reports,
deployment receipts, report paths, attachments and delivery/execution references remain permanent.
The listing root cause and permanent validation are recorded in
[the bounded listing result](docs/evidence/task-agent-listing.md).

A single maintenance loop starts with the existing scheduler, runs outside the task
queues, and retries once per minute. Each atomic transaction handles at most 250 messages, 250 events
and 250 executions. Inactive content outside the hot windows or older than seven days moves to the
`history_archive` table in the existing SQLite database as deduplicated zlib-compressed UTF-8 payloads.
Original rows keep their IDs, order, timestamps, scope and references. Unfinished executions and pending
result deliveries remain hot. Selected content is restored transparently through `Database` connections;
SQL content-search/JSON predicates use `history_text(...)`. Archive digests are verified on retrieval.
Short output previews are stored separately, so default result lists need no decompression.

Archive insertion and reference replacement share one SQLite transaction: interruption either commits
both or rolls both back, and repeat runs/restarts are idempotent. Existing SQLite backup/restore includes
the cold archive automatically; back up through the application's SQLite backup path rather than copying
a running database without its WAL. No separate cron job, service, deletion schedule or manual archive
file management is needed. Maintenance failure is logged and retried without stopping queue progress.
Compression reuses free SQLite pages; it does not promise an immediate reduction in the database file's
allocated size, and retention does not remove referenced files from disk.

Permanent regressions: `tests/test_retention.py` covers windows, bounded default reads, chronological
cold retrieval, scoped HTTP paging, restart/small-batch idempotency, atomic rollback, active/delivery
exemptions, backup/restore and permanent references. `tests/test_retention_browser.py` exercises the
actual chat, event and full-output controls in Chromium with disposable data and a disposable server.

`ai_manager/static/icons/README.md` records the owner artwork provenance.

## Account usage

The usage button follows the provider of the **saved effective manager selection**, resolved by the
same `manager_route` rule used for dispatch: the active repository's full lock when enabled, otherwise
global. `GET /api/account-usage?project_id=…` resolves that saved pair and returns both providers through
its `providers` map; the top-level reading remains scoped to the effective manager for the compact widget.
Both views reuse the existing `AccountUsage` caches/listeners and metadata readers, with one concurrent,
throttled read per provider. Provider caches/listeners stay isolated, so an unavailable provider never
falls back to the other's readings. The response includes the effective manager pair; changing selection
while a read is in flight discards that compact reading.

The upper bar is the five-hour allowance; the lower bar is weekly. Details show reported remaining
percentages and bare reset countdowns (e.g. `4h 12m`, no "Resets in" prefix); reset timestamps are available to assistive technology and on hover.
The footer reads `Last refresh HH:MM` (time of day only; no date or zone label).
Opening the widget always shows **OpenAI / Codex** and **Claude** together, regardless of the selected
manager. Each provider has a separate large rounded card with its bundled inline SVG logo (no visible provider name; the logo carries the accessible name), five-hour
and weekly remaining bars, and its own Unavailable states. Cards use the Model Picker's neutral fill,
muted section typography, compact spacing and buttons. No logo is fetched externally. Credit/reset
metadata remains available under each card's **Credits & resets** disclosure; a failure or stale reading
for one provider does not hide the other provider's values.
A project switch or saved model/lock change clears the widget immediately and starts a scoped refresh.
Late context responses are ignored. Missing, expired, failed or stale readings display **unavailable**
instead of a previous percentage (stale after three minutes or a passed reset). Claude's reader accepts
both nested `unifiedWindows` and flat `rateLimitType`/`utilization` events; the previous nested-only parser
silently dropped flat events. Separate five-hour/weekly events merge without refreshing the other
window's observation time. Claude readings retain their original event time across disk reads and
restarts; reading saved metadata does not refresh the observation.

Claude limits are fetched actively. The Claude host's `account/rateLimits/read` makes one read-only GET to
Anthropic's OAuth usage endpoint (the source behind Claude Code's `/usage`) under an 8-second timeout, using the
CLI's stored login (`$CLAUDE_CONFIG_DIR` or `~/.claude/.credentials.json`). This is the only place credentials are
read: the token is never logged, persisted or returned, an expired token is not refreshed, and raw error text is
discarded. The five-hour and weekly utilization and reset times are normalized into the saved
`claude-rate-limits.json` snapshot (primary 300 min, secondary 10080 min), so the 60-second refresh picks up new
values after a reset. Passive `rate_limit_event` updates still merge in. On any failure (no login, network/HTTP error,
unexpected shape) the last saved reading is returned and shows as stale; with none it shows unavailable.

The metadata-only `account/rateLimits/read` request includes available reset-credit details. The dialog
shows each provider's available reset count, individual available-credit expiration dates, and reported
credit balance/unlimited status. Counts are never inferred from the detail list: it may be partial or
unavailable. Explicit null expiration means no expiration; absent/invalid expiration is Unavailable.
Balance expiration is unavailable in the current protocol. Claude's retained event adapter supplies
windows only, so reset credits, balance and expiration remain Unavailable unless authoritative metadata
is supplied. Unsupported data is never represented as zero. Account/provider changes discard old readings.
There is no reset/redeem action. Cached limits, empty balances, unavailable metadata and refresh failures
never disable Send or preflight a message. Real provider errors still follow the existing queue behavior.

Permanent regressions: `tests/test_account_usage.py`, `tests/test_usage_display.py`, and
`tests/test_claude_provider.py`, `tests/test_claude_usage_fetch.py` (mocked endpoint and fake login), plus `tests/test_usage_limits_browser.py` for saved manager/repo-lock
switching, simultaneous provider cards for either manager, independent stale/unavailable states, inline logos,
credits, keyboard focus and desktop/mobile/narrow layouts; responsive interaction checks remain in `scripts/browser_account_usage.py`.
Protocol reference: installed Codex 0.162.1 generated `GetAccountRateLimitsResponse`,
`RateLimitResetCreditsSummary`, `RateLimitResetCredit`, and `CreditsSnapshot` (also available in the
[upstream schema](https://github.com/openai/codex/tree/main/codex-rs/app-server-protocol/schema/typescript/v2)).

Selected source: To-Do `4e9e0e7b530543e3a19dee56acddb351`, revision 0, implemented by assignment
`a51aadfc0f6f455d8873efe1c67ab91c` after inspecting the retained implementation and
[original assignment handoff](docs/MESSAGE_ACTIONS_978211.md). Its selected description is:
“Fix Claude usage/reset display to match available Codex information. Expose available reset
counts/credits and expiration for both where authoritative data exists. Information only, no reset
action; unsupported fields honest; never block Send.” The original assignment
`978211a9c69a48dfa9347b3620b4db1b` runs only Copy/Delete (revision 8); this sequential assignment does
not amend or resume it. Original descriptions, links, screenshot and archived records remain preserved
in that handoff and commit `11ff4765c8fcd897bc03baf99cde1d19bdd31087`. This worker does not consume or
archive the source To-Do; the application owns disposition only upon successful acceptance.

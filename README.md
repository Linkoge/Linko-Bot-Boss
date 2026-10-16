# Linko Bot Boss

A private, single-owner conversational manager for software projects: **you → manager → one worker → merge → deploy**.
FastAPI + SQLite + plain JavaScript; Codex or Claude does the thinking and coding. The installed names stay unchanged
(`ai_manager`, `AI_MANAGER_*`, the service, the data directory and the private URL
`https://codex-server.tail5ebb9d.ts.net:8787/`). Repository: `Linkoge/Linko-Bot-Boss`.

## How work runs

One global queue, strictly sequential. States: **queued → running → done | failed**. A **held** task is a queued task with
the hold flag set; it is skipped until released. Order is the only dependency. The code is `ai_manager/core.py`; the rules
both the manager and every worker read are [RULES.md](RULES.md).

1. **Worker.** The oldest eligible queued task gets an isolated Git worktree and branch
   (`linko/assignment-<id>`, kept on disk, never deleted). The worker implements, adds the permanent tests for its change
   and commits. Projects that are not Git repositories run in place, with no checks and no merge.
2. **Checks.** After every worker turn the application runs the permanent checks: the one fixed smoke suite
   (`python -m pytest -q -m smoke`, 180 s) plus the test files the branch added or changed (600 s). Only permanent
   repository suites can block delivery; one-off probes are informational. Probes run under a 60 s timeout. Checks run
   with disposable data directories.
3. **Repair rounds** (a counter on the task row, never reset by retry, restart or continue). A failing check goes back to
   the same worker as a repair round, at most 5 times; the task then stops with a plain reason and the manager reports it.
4. **Merge and deploy.** A passing branch always merges into `main` under the checkout fence (one at a time); there is no
   per-project switch. Main is first merged into the branch; a conflict goes to the same worker as a repair round of the
   same task. For the manager's own project the deploy script is then requested automatically; other projects are merged
   only. The manager's `deploy` action (optionally with a `task_id` of a finished, never-merged task) deploys on request.
   After every passing merge the application schedules a GitHub backup push for that project. This runs independently
   of the queue and deployment; workers never push. See GitHub backups and secret checks below.
5. **Queue runner** (`ai_manager/runner.py`; independent of any open chat or owner message). A tick every second starts
   the oldest eligible queued task, one at a time. Not eligible: held, project paused, waiting for its retry time, or a
   deploy request pending (for at most 30 minutes, see Deploy). On service start, whatever was running is resumed without penalty (same branch, same worker
   thread, repair counters untouched) and retry times are forgotten, so a restart or reboot always retries at
   once. A provider usage limit or a lost connection never fails a task: it goes back in the queue with a visible retry
   time and is tried again every 5 minutes, indefinitely (no backoff, no wait counter, the provider's reset time is not
   consulted, so a limit that resets early is picked up by the next try). Only real errors fail a task. The watchdog (every
   minute) requeues a dead or hung worker (30 min without activity) once, then fails it; fails queued tasks of a removed
   project; withdraws a deploy request that is still waiting after 30 min; and reports an idle-queue stall: eligible work
   (not held, project active, retry time passed, no deploy pending) that nothing has started for 5 minutes gets a
   `queue.stalled` event and a visible reason on the task (for example a deploy or merge holding the live checkout), once per
   stall. Held, paused, retry-waiting and deploy-waiting work is never a stall. Nothing stays blocked without a visible reason.

Owner controls on a task: **Hold / Release** (queued), **Cancel** (queued or running; the branch is kept),
**Continue** (failed or done task: appends instructions, re-queues at the end, counters stay). A worker may ask the owner a
question; the task stays running and says so. Finished results return to the originating chat automatically.

## Architecture design principles

This section is included verbatim in the manager prompt. It is advisory only: briefly evaluate each new feature
against it and warn of concrete complexity or reliability risks (extra states, conflicting settings, hidden routing,
unbounded waits). Advice never blocks answering, delegation or delivery, requires no approval or review receipt,
and adds no model or routing restrictions. Proceed with the owner's requested work.

Prefer deletion and one clear path. Add no new gate without a concrete incident it prevents; speculative safeguards
create their own failure modes. Keep one source per setting and **shown = saved = dispatched** across reload and execution.
No feature should stall the global sequential queue; keep waits visible, bounded and recoverable.

Deliberately removed machinery and its replacements:

- **Model restrictions / permitted pairs; Pin/Global:** hidden pair policies and overlapping controls could reject or
  misrepresent a saved choice. Manager and worker are independent; any installed catalog model/effort is valid.
  One global manager and default worker remain, with one optional project worker override that can follow the default.
  Manual fixes the worker; Automatic lets the manager choose from the catalog. A task retains its first execution pair.
- **Auto-deploy toggle:** a second delivery policy could strand passing work. Passing Git branches always merge;
  the manager's own project requests deployment automatically, other projects merge only. Explicit deploy remains available.
- **Repair checkpoints:** extra stop/resume states obscured progress. Permanent checks return failures to the same
  worker for at most five durable repair rounds; retry, restart and Continue never reset that counter.
- **Delivery/brief bookkeeping:** manager-written delivery receipts and brief-upkeep actions duplicated application
  state. The application records results and returns them to the originating chat. The project brief remains read-only
  navigation for the manager, with owner-run review; it is not assignment authority or a delivery prerequisite.
- **Chat modes; tiered/review machinery:** mode switches, helper/reviewer tiers and integration/follow-up paths
  multiplied handoffs and waiting states. The manager answers, inspects or delegates as asked to one worker queue;
  permanent checks and the existing secret check remain, with no separate architecture approval stage.
- **Deploy blocking the queue:** checkout safety still pauses new tasks and merges while a deploy is pending.
  Before backup, a 30-minute deadline withdraws a stuck request and releases the queue; after backup, deployment finishes
  or rolls back. Queued, held and retry-delayed tasks do not block restart; active work does. Preserve this bounded behavior.

Ship small rollouts: replace one path at a time and delete the old path in the same change, updating its tests and docs
in place. Give a one-line before/after summary, for example: "Before: two worker selectors could disagree; after:
one saved selection drives display and dispatch."

## GitHub backups and secret checks

The owner has authorized automatic application pushes, including verified existing backlog. Every Git project uses its
single `origin` push URL on `github.com` (SSH or HTTPS), with the service user's existing Git credentials. The application
pushes local `main` (legacy `master` where that is the project's main branch) to the same remote branch. It never forces,
resets local commits, rewrites history, creates repositories, or changes remote configuration. Non-Git projects keep
their in-place workflow. Missing/unsupported origins or authentication failures need attention, without holding work.

`ai_manager/github_backup.py` runs network work in a background thread without the checkout fence. A merge requests an
attempt; failures retain all local commits and retry every five minutes, and a later merge requests another attempt.
Startup also schedules repositories without a backup receipt, so safe existing backlog does not require another task.
Durable per-project receipts in SQLite settings (`github_backup:<project-id>`) preserve pending work and retry times
across restarts. Project tabs show **GitHub needs attention** on failure; project API/manager records include the reason,
retry timestamp and safe findings. Backup failures do not fail tasks, consume repair rounds, or prevent merge/deploy.
A later successful push includes all retained commits. Each Git command has a 60-second timeout and no interactive
credential prompt. Raw Git/SSH/hook output is discarded, since it can contain credentials.

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

Reviewed synthetic fixtures can use a committed `.linko-secret-allowlist.json` in each repository. Its JSON array has
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

## Deploy

`scripts/deploy.py` is the only deploy mechanism: full backend suite once, wait for idle, back up SQLite, restart the
service on `main`, health check, and automatic rollback of code and database on any failure after the stop. The short
result is `DATA/deploy/last-deploy.txt`. While a request is pending no new work starts and nothing merges (the
checkout fence, `ai_manager/fence.py`), but never for more than 30 minutes: the watchdog withdraws a request that has not
reached its backup by then (the wait for idle is bounded the same way), tells the originating chat, and the queue runs on.
A request past its backup finishes or rolls back by itself. One-time operator bootstrap, no sudo:
`python3 scripts/install_deploy.py`. Manual deploy: `python3 scripts/deploy.py --now`.
Concurrent consumers wait on the deployment lock and re-read the request after it is released; a systemd consumer can
resume a request abandoned by a manual caller. During the idle wait, `DATA/deploy/state.json` records the deploy PID,
blocker counts and bounded record identities, refreshed at least every 30 seconds (also in `deploy.log`). Queued,
held and retry-delayed tasks remain durable and do not block restart; unfinished executions, task leases, running
tasks, streaming messages and running deliveries do. Stale leases are reconciled by the queue runner only after
both the runner and its recorded process are gone, preserving workspaces and limiting recovery retries.


## Emergency host repair

The host repair role is an explicit exception to the normal sequential work queue. It can diagnose/fix the
application, restart its user service, and call the existing deploy flow while the app or queue is broken.
It never creates a normal task. `scripts/emergency_repair.py` uses only host Python's standard library and the
installed Codex/Claude CLI, with native shell/file tools and arbitrary provider model names, without model catalog,
usage preflight, queue, project pause or deploy-pending checks. Provider authentication and actual provider limits
still apply. Catastrophic destruction requires specific owner confirmation: unattended agents must stop and return
a failed result asking for that confirmation. As with other native workers, this is an agent instruction convention,
not a sandbox; ordinary repairs/restarts are authorized by the repair request.

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
- Model settings: one global manager choice and one default worker choice, independent of each other (a Claude manager can
  have a Codex worker). Each project can optionally override the worker; an empty override follows the current global
  `model_choice` worker. In a project, picking a worker saves only that project's override. **Use default worker** clears it;
  **Following default worker** indicates inheritance. Manager edits and favorites remain global and leave worker overrides
  alone. The picker is the layout deployed at `208554a` (Manager, Worker, Favorites; per provider a model row and an effort
  row; three families in fixed order - Codex Luna, Sol, Astra; Claude Haiku, Sonnet, Opus - each as its newest installed model,
  with Low/Medium/High; a family missing from the catalog shows disabled) plus the **Manual | Automatic** switch in the Worker
  heading row, drawn exactly as the Bot switch of `81711bd` was. Automatic stores the worker as null in the selected scope;
  the last fixed worker is kept as `manual` and returns when Manual is chosen. Project settings use one revisioned
  `project_worker:<project_id>` setting: `override: null` means inheritance; an explicit `{worker, manual}` selection can
  itself be Automatic (`worker: null`). `GET /api/model-settings?project_id=...` returns the global `settings`, the persisted
  `project_worker`, and the effective `worker_selection`. `PUT /api/projects/{id}/worker-model` saves `{revision, override}`;
  `PUT /api/model-settings` continues to save global defaults. There are no Pin/Global controls or permitted-pair policies.
  While Automatic, a new task runs on the pick the
  manager passes with `delegate` (`model`/`effort`, any catalog provider/model/effort; the provider default for what it leaves
  out) and the manager is told the available catalog; a fixed worker always wins. Dispatch accepts any model and effort the
  provider catalog offers (`PUT /api/model-settings`); what the panel shows is what is saved and dispatched. A saved or
  favorited model outside the families is added as one extra button (with its own efforts) while selected. A task keeps the
  pair it first ran on and each run records the model and effort actually used. Two manager favorites. Pinned by
  `tests/test_model_picker_ui.py` (original markup/styles and reviewed client code) and `tests/test_model_picker_browser.py`
  (rendered geometry at 1200/393/320, persistence, project isolation/clearing, independence, favorites, Send).
  `tests/test_project_worker_models.py` verifies reloads, inherited defaults, overrides, direct tasks, delegation and actual
  thread/turn arguments plus execution/task route evidence for separate projects. Evidence and the comparison with
  `81711bd`, `208554a` and `db292c6`: `docs/evidence/model-picker/` (`FINDINGS.md`, `side-by-side-*.png`).
- Project list with display names, visibility/stash, pause, discovery; To-Do list (never executes by itself; Start hands the
  To-Do directly to one queued task); read-only project memory brief for the manager with owner-run review;
  chat clear/trash/archive/search.
- Installable PWA (`/` scope, no service worker), account usage display, Tailscale-only private hosting.
- Security: Argon2 owner login, one-time setup token, revocable sessions, CSRF/origin/host checks, CSP, throttling.
  Workers run as the service user with broad access; the rules ask them to confirm before catastrophic destruction.
  That is an agent convention, not a sandbox.

Chat opening (reload, context selection, reopening Conversation and authentication recovery)
positions the loaded history at its latest end after fonts, images and composer sizing
settle. A deliberate chat gesture cancels pending positioning. Subsequent streaming,
polling and sends preserve the reader’s scroll position; loading older messages keeps
the previously visible message in place. Permanent Chromium coverage lives in
`tests/test_chat_open_start_browser.py`, including 155-message histories and delayed layout.

To-Do **Start** uses authenticated, CSRF-protected `POST /api/todos/{id}/start` to queue
one task directly, without waiting for manager interpretation. The backend resolves the
source project’s effective worker selection (Automatic uses the provider default),
retains the original conversation and complete source, and archives the entry in the
same transaction that accepts the task. A durable per-entry handoff makes repeat clicks,
concurrent requests and retries after a lost response return the original task. Catalog,
model or persistence failure leaves the entry available for retry. The UI disables
pending Start controls and displays either the assignment ID/status or an actionable
error. `tests/test_todos.py` covers atomic failure/replay; the browser suite covers the
full click-to-queue path, duplicate clicks and lost-response recovery.

## Whole-session audio recording

Microphone capture requires a secure context (HTTPS, or localhost for development),
MediaRecorder, microphone permission and a supported recording format. One app-level
session retains its original project and conversation across internal views,
project switches and stashing. In Conversation, the recording controls sit in the
composer bottom row immediately left of Send: elapsed time only, Upload and Discard.
The existing Record button toggles capture off; there is no second Stop button.
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

Permanent checks: `tests/test_message_actions.py`, `tests/test_chat_history.py` and
`scripts/browser_markdown.py` (streamed multi-block responses, owner/assistant copying, Cancel/confirm,
reload, saved-reader removal, keyboard and narrow layouts). Historical source and assignment evidence:
[retained Copy/Delete report](docs/MESSAGE_ACTIONS_978211.md).

## Run and develop

```bash
uv sync --locked
uv run playwright install chromium               # required by audio browser regression tests
# browser_pwa.py opens a headed Chromium app window: on a display-less host run it as `xvfb-run -a uv run python scripts/browser_pwa.py`
uv run python -m ai_manager.cli setup-owner     # first time only
uv run pytest -q -m smoke                       # the fixed smoke suite, every task
uv run pytest -q                                # full suite: run by the deploy script
uv run ruff check ai_manager scripts tests
node --check ai_manager/static/app.js
PYTHONPATH=. uv run python scripts/browser_smoke.py   # also browser_tasks.py and the other browser_*.py checks
```

Configuration: `AI_MANAGER_DATA` (default `~/.local/share/linko-bot-manager`), `AI_MANAGER_PORT` (8787),
`AI_MANAGER_PROJECT_ROOTS`, `AI_MANAGER_ALLOWED_HOSTS`, `AI_MANAGER_SECURE_COOKIE`, `AI_MANAGER_CODEX`,
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
gone; its history is in Git.

History retention uses lossless compression only; there is no 90-day purge. The current chat and
saved-chat pages load the last **50 messages**, with **Load older messages** / **Load earlier messages**
retrieving preceding pages in original insertion order. New manager threads receive the last 50 context
messages (plus the latest owner request if it falls outside that window); native thread continuity and
explicit historical search/read actions remain available. Hidden transport rows and display tombstones
keep their existing behavior.

Task and worker history opens with the last **20 events** and up to **20 execution references** per page.
The Agents view retains the latest 20 completed executions per worker/project plus unfinished work,
within its existing 100-row presentation cap. **Browse older executions**, task **History**, and worker
**History and full output** retrieve older records and full output on demand. Routine portfolio refreshes
include only a short result tail (at most 1,200 characters), execution metadata and task summaries;
they do not load historical prompts or inflate compressed results. Task records, full final reports,
deployment receipts, report paths, attachments and delivery/execution references remain permanent.

A single maintenance loop starts with the existing scheduler, runs outside the sequential assignment
queue, and retries once per minute. Each atomic transaction handles at most 250 messages, 250 events
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

The usage button follows the selected provider (Codex or Claude). Details show reported five-hour
and weekly remaining percentages and reset countdowns; reset timestamps are available to assistive
technology and on hover. Missing windows remain Unavailable. Cached readings are marked stale after
three minutes, a passed window reset, or refresh failure. Claude readings retain their original event
time across disk reads and restarts; reading saved metadata does not refresh the observation.

The metadata-only `account/rateLimits/read` request includes available reset-credit details. The dialog
shows the provider's available reset count, individual available-credit expiration dates, and reported
credit balance/unlimited status. Counts are never inferred from the detail list: it may be partial or
unavailable. Explicit null expiration means no expiration; absent/invalid expiration is Unavailable.
Balance expiration is unavailable in the current protocol. Claude's retained event adapter supplies
windows only, so reset credits, balance and expiration remain Unavailable unless authoritative metadata
is supplied. Unsupported data is never represented as zero. Account/provider changes discard old readings.
There is no reset/redeem action. Cached limits, empty balances, unavailable metadata and refresh failures
never disable Send or preflight a message. Real provider errors still follow the existing queue behavior.

Permanent regressions: `tests/test_account_usage.py`, `tests/test_usage_display.py`, and
`tests/test_claude_provider.py`; responsive interaction checks remain in `scripts/browser_account_usage.py`.
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

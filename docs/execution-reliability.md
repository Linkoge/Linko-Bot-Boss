# Execution reliability investigation — 2026-10-10

The corrected manager completed a real isolated browser → chat delegation → planning → scheduler → Codex worker → exact owner approval → file edit → independent verification → completed task → dashboard → manager restart test. This establishes the supported brokered execution pipeline. It does not establish arbitrary host administration, browser automation by workers, dependency installation, or autonomous execution for all registered projects.

## Production findings

Inspected the implementation, SQLite records, user service, historical journal, worker workspaces, installed App Server and actual Chromium controls. Incoming Git checkpoint: `6009e04f754631ec4a8a410e4f27faad25e96f14`. Production had 10 projects, 11 tasks, 10 executions, 8 messages and no approval/policy overrides. No unfinished execution existed before deployment. Codex used its existing ChatGPT login; actual planning/execution/review inference used `gpt-6.1-sol`, medium effort. Chat used low effort. No new API provider or authentication flow was configured.

The scheduler was **enabled**, scoped to Linko-Bot-Manager (`29018771ce9842f2bb52afb7dcf491d0`), with a six-completion batch containing task `5db40e44`. Its loop was running in dedicated service PID 594062. No scheduler error records existed. Queue accumulation primarily came from dependency gates, not absent model calls or a concurrency cap. All 10 projects lacked `verification_commands:{project_id}` settings.

Historical HTTP logs confirm Start on dependent tasks returned 409, Resume on the original reboot task without a thread returned 409, and Resume on `5b9a10fd` returned 200 twice. The latter caused repeated actual turns without resolving its verification prerequisite. Production chat requests did create persistent plans/tasks, and workers ran; they could not independently finish their planned acceptance work.

| Requested task | Actual status and evidence |
| --- | --- |
| `5db40e44cf464ec5a5a59f3293b2c3b2` | Blocked after real worker execution `df2e7555370045d9b4c6f996da42d838`, thread `01a122d1-ef36-7fb3-8b86-df714c7c30d7`, turn `01a122d1-efc6-7ec2-8001-16544fc26752`. Worker inspected source/docs and recorded capability limitations. No trusted verification configuration. |
| `c78c0078738f4493a6cf2393519dfe3c` | Pending; dependency `5db40e44` is unfinished. No worker execution. |
| `8589648a4024468da0c6bfa9405ac84a` | Pending; dependency `c78c0078` is unfinished. No worker execution. |
| `ae1ba4548d6e4a8e98594174edc2e87e` | Pending; dependency `8589648a` is unfinished. No worker execution. |
| `5b9a10fd37e14a1fba509561bb21e09a` | Blocked after three real read-only worker turns in persisted thread `01a122bd-a5be-7b73-a74e-4b96dfd81439`; latest execution `9891122724f84d57a44c82a1ce730f62`, turn `01a122c7-f585-7e01-a4c6-e4658ea1e4a9`. Missing trusted verification commands. |

All these production dispositions/revisions, dependencies, execution records and history were preserved. No duplicate investigation task was created, no uncertain execution was replayed, and none was marked completed by this maintenance work.

## Root causes and corrections

1. **Verification absence gates completion.** Workers start without trusted commands, but worker tests reject an empty list and independent completion transitions verifying → blocked. This stalls dependent tasks. The task's free-text verification field and commands documented in README do not register trusted commands.
2. **Resume conflated execution and verification.** A task whose worker turn had finished was offered Resume, which started another turn in the same workspace instead of retrying verification. A real isolated Chromium baseline reproduced two worker executions and the same repeated blocker. Completed worker output now offers **Retry verification**, which uses the existing workspace and does not replay the worker. Missing configuration explicitly rejects it without changing task state.
3. **Launch acknowledgement claimed success too early.** Start previously returned only an execution ID while launching asynchronously, and the UI immediately announced “Worker started.” It now returns accepted/queued with a clear message; actual process and turn records produce worker.process_started/worker.started events and journal entries. Errors are persisted and displayed even when output is nonempty.
4. **Scheduler reconciled every ineligible candidate.** Dependency checks occurred after project reconciliation, producing more than 98,000 events by investigation time, mostly repeated observations. Eligibility checks now precede dispatch; repeated ticks with blocked dependencies produce no reconciliation storm. Scheduler loop/enabled state, scope, cap, last tick, errors and active jobs are visible. Completed batch limits now pause even if no pending tasks remain.
5. **Unclear control eligibility and failed mutations.** UI controls now follow recorded capabilities: fresh Start, safe Retry launch before any thread/turn, Resume a saved interrupted thread, and Retry verification are separate. Rejected resume checks leave task dispositions intact. Latest worker selection excludes verification controller/reviewer records. Stored workspace validation is inside failure handling, so bad paths cannot strand task leases. Generic requeue cannot bypass existing execution history.
6. **Uncertain recovery needed explicit evidence.** Resume of uncertain blocked work requires owner reconciliation notes after inspecting thread/workspace/operation records. Active process identity must match PID and nonempty /proc start time. Expired detached claims block without replay or signaling. Recovery clears obsolete leases and preserves completed/cancelled task dispositions. Heartbeats cover launch preparation as well as execution.
7. **Conversational promises were insufficient receipts.** Conversation mode rejects mutating manager actions. Execute mode deterministically submits the owner message to persistent planning and dispatch under existing scheduler settings. Auto mode instructs clear action requests to delegate and appends manager-generated task receipts, or explicitly states no task was created. Scheduler pause/scope/dependencies/policies remain authoritative; no automatic policy changes or uncertain retries. Receipts provide task/project/status/worker/blocker; accepted launch records distinguish a pending PID/thread/turn.

The supported worker cannot inspect the host database, systemd, network or browser merely because a plan asks it to. The existing host/browser investigation tasks exceed that broker scope. This maintenance investigation used the owner's authorized native tools; adding arbitrary shell access to workers was not needed and was not done. Registering tests alone will not substantiate acceptance criteria that depend on unavailable host observations.

## Trusted verification workflow and owner decisions

Commands live in SQLite settings under `verification_commands:{project_id}`. Authenticated owner GET/PUT: `/api/projects/{project_id}/verification`; mutations require CSRF and same-origin checks. Saving records `verification.configured` with exact argument arrays. Task free-text is descriptive, not executable. Effective run_tests policy still applies. Tests run through bubblewrap with no network or host home/credentials; only system runtime and isolated workspace are exposed.

The dashboard already had a Settings configuration dialog. It is now also reachable beside a verification-blocked task, explains the runtime boundary and requires explicit JSON argv registration. Saving configuration does not change any blocked task or replay work. Select **Retry verification** explicitly afterwards. Inspect execution records before resuming uncertain work. These controls were tested in Chromium and API regression tests; the acceptance fixture registered its owner-approved command through authenticated HTTP.

Projects without configuration: AI-Manager, Linko, Linko-Bot-Manager, Linko-SSH, Okashi-Frontliner, linko-dev-panel, linko-gym-coach, linko-screenshotter, linkoge-profile, my.gov.ge-Tracker. Projects with production configuration: none. The separate acceptance fixture has its explicitly approved unittest command.

Proposals copied from existing documentation, **not registered or approved for production**:

| Project/source | Commands for owner review | Constraint |
| --- | --- | --- |
| Linko-Bot-Manager README.md:34–37 | `uv run --offline pytest -q`; `uv run --offline ruff check ai_manager scripts tests`; `node --check ai_manager/static/app.js`; documented browser smoke | Actual isolated probe: uv command fails with `bwrap: execvp uv: No such file or directory`; node syntax passes. Home-installed uv, Python environment and browser/cache are outside the verification sandbox. A trusted offline runtime/dependency solution is needed before registering full project checks. Syntax alone cannot establish these investigative acceptance criteria. |
| linko-dev-panel README.md:466 | `python3 -m unittest discover -s tests -v` | Needs owner selection and feasibility/coverage review; not executed or registered here. |
| linko-gym-coach README.md:217–219 | `uv run pytest -q`; `uv run ruff check app scripts tests`; `uv run ruff format --check app scripts tests` | Needs owner selection and isolated dependencies; no installation or model download was authorized by this investigation. |

No arbitrary production verification command was invented. Owner decisions remain: select sufficient commands/runtime for each project; decide how to supersede completed-in-maintenance investigations or support their missing capabilities; decide whether existing reboot/storage follow-ups need further acceptance work. Do not blindly retry the old tasks to clear their queue.

## Cancellation, disk and reboot

Storage task `9e43efbd36b645d4a5d3937769ba4739` and reboot task `e6f17f3b2859416b94412f01f642a882` were explicitly transitioned blocked → cancelled in persisted events (historical HTTP PATCH at 02:21:28 and 02:27:13 local). Cancelled is terminal in the state machine, so expanded disk and reboot do not reopen them. Reboot task had no persisted worker thread, explaining its earlier Resume rejections.

Measured current boot ID `ec59df2b-f599-4ae8-8d9e-ad50791e441a`, boot start 2026-10-10 01:58:07; prior documented boot ID `e3a37e08-1b0b-4ac0-a923-8b6b97767873` differs. Root volume 217 GiB, 171 GiB available, 18% used; RAM 7.8 GiB with 5.4 GiB available at measurement. Old low-storage wording is historical. Manager service and persisted data are currently healthy. This does not silently complete old tasks or certify every old Stage 6 checklist item. The already-existing follow-up plan beginning `5b9a10fd` supersedes the original operational checks in intent; retain the cancelled history rather than reopen terminal tasks automatically or create duplicate investigations.

## Real acceptance evidence

Isolated source: `/tmp/linko-reliability-acceptance/project`, immutable committed fixture. Manager data is separate from production. Original add(a,b) subtracted; the approved change makes it add, leaving tests byte-for-byte identical and the source repository unchanged.

| Step | Actual evidence |
| --- | --- |
| Browser owner command | Execute mode POST /api/chat accepted; chat execution `d557502d359d4e68b2a387c51527d33b` |
| Persistent planning | Planning execution `93d84d54a2b74afab3a058fde3610d97`; task `4961242df6d14bf49f9018933f593fb3` |
| Scheduler dispatch | Enabled scoped one-completion batch; worker.queued persisted automatically, no per-task Start |
| Physical worker and inference | Worker `650160c740a241d6a452b7469a9961c2`, PID 649552, thread `01a12491-5799-71c3-be20-9ca3491147aa`, turn `01a12491-5857-7ce2-ac19-281e535e529f`; gpt-6.1-sol/medium |
| Approval enforcement | Actual ASK `73e9f68209b74a7991e27e734768f8ab`; owner explicitly approved command and exact edit; authenticated decision recorded and approval consumed once. No edit ALLOW override. |
| File operation | Hash-bound calculator.py write, exactly `def add(a, b):\n    return a + b\n`; final SHA256 `ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772` |
| Trusted tests | Approved `["python3","-m","unittest","discover","-v"]`; worker test and independent controller test both ran in bubblewrap; independent record exit 0, unittest OK, assertions 2+3=5 and -2+3=1 |
| Independent review | Real Codex review `248295415c1f4579be31100639ca38ff` inspected calculator.py, unchanged test_calculator.py and README.md; file hashes, test output and diff digest persisted |
| Completion/dashboard | verification_results.passed=1, atomic completed task/checkpoint; Chromium displayed completed badge and screenshot |
| Restart | Fixture manager PID 648842 → 664975; task and execution records identical after restart; dashboard still completed, no replay |

Screenshots, before/after DB state, exact HTTP requests/responses, backend logs, scheduler settings, process/thread/turn records, approval ledger and complete verification evidence are retained privately under `/home/codex/.local/share/linko-bot-manager/reliability-evidence-20261010/`. These include fixture-only credentials in isolated databases and must stay out of Git. Original /tmp fixtures are also retained.

## Validation and deployment

48 pytest tests passed; Ruff and JavaScript syntax passed. Existing Chromium desktop/mobile smoke passed with no page errors or horizontal overflow. Baseline and corrected browser tests exercised positive Start, already-running Start/Resume, verification-blocked Resume/retry, cancelled rejection and Cancel. Additional Chromium tests exercised a genuinely missing Codex binary, safe Retry launch, displayed execution error, dependency-blocked Start and absent-thread Resume. Positive Chromium Resume confirmed pause of worker d5b868e7d4a7428a8ebb1203cd8883ab, then started 626efe36aeba4dd7a1ddc6a9abed4b7c: same thread 01a124a0-ac3b-7853-9c87-5b289579a7bc and workspace, different PIDs 676328/676469 and turn IDs. Intentional negative cases returned explicit errors without success simulation. One first positive-interruption harness attempt raced turn completion and returned the explicit 503 “no active turn to interrupt”; its assertion failed and the evidence was retained. The stable positive Resume test uses a stricter fixture-only read ASK gate for confirmed interruption, then restores the inherited read policy before resuming. No unresolved application test failure remains; one pre-existing Starlette/httpx deprecation warning remains. The documented uv command's isolated feasibility failure is a remaining runtime blocker, not a passing project verification result.

Production backup: `/home/codex/.local/share/linko-bot-manager/backups/manager-execution-reliability-20261010.sqlite3`, integrity-checked 101769216 bytes. Restarted only dedicated linko-bot-manager.service, now active PID 675325. Before/after task, execution, message, conversation, approval, operation, policy, settings, checkpoint, owner and session rows were identical; all original audit events retained; 10 projects/11 tasks retained; SQLite integrity/FK checks passed. Exact tested assets served and unauthenticated portfolio still returned 401. Scheduler configuration preserved. Live owner-authenticated browser actions were exercised on isolated instances; the owner's production login was not impersonated. No SSH, Tailscale, firewall, unrelated service, provider, dependency or security-policy configuration changed.

Final review also enforced project/global concurrency on verification retry, rejected generic mutations while a task awaits active approval, and aligned project process observations with PID/start identity. Final pre-reload snapshot: `backups/manager-execution-reliability-final-20261010.sqlite3`; final service PID 675325, histories/policies/authentication preserved again. Five seconds of idle scheduler observation showed zero additional dependency reconciliation events.


## Owner follow-up: clicks appear to do nothing

The exact deployed HTTPS URL, including ?utm_source=chatgpt.com, is valid. Journal entries on 2026-10-10 show Retry verification for 5db40e44 (15:20:29) and 5b9a10fd (15:23:48) rejected with HTTP 409 for missing trusted commands. Start for c78c0078 (15:21:08) and ae1ba454 (15:23:27) returned HTTP 409 for unfinished dependencies. These are real owner requests; no execution was dispatched by those rejected requests.

An isolated Chromium regression reproduced the visible failure: the previous global rejection banner lay at y=-1531 on desktop and y=-2892 on phone when clicking a lower task. The new receipt appears in the clicked card and is scrolled fully inside the viewport (desktop y=947.016, height=41.188, viewport=1000; phone y=783.266, height=41.188, viewport=851). Receipts survive refresh/polling; busy actions prevent repeated submission. Cards prominently explain blockers, navigate directly to prerequisite tasks and distinguish verification recovery from worker resume. The workflow is Configure verification → owner registration of adequate commands → explicit Retry verification of confirmed worker output; configuration alone does not alter task disposition or replay work.

The UI regression uses a clearly labelled display-only finished execution row; it never launches Codex and is not additional execution proof. Actual isolated API rejections, verification editor and accepted cancellation were exercised. Existing real execution evidence remains the earlier acceptance fixture. All 48 existing tests passed; Ruff/JS syntax and both browser scripts passed. The restricted test runner stalled and was stopped; native isolated pytest passed in 8.42 seconds. Updated static bytes were checked at the live HTTPS address. No service restart was required, PID remains 675325, all 11 production task statuses/revisions remain intact, no unfinished execution exists, and production trusted verification settings remain absent. Private receipts: task-feedback-baseline.json, task-feedback-fixed.json and task-feedback-deployment.json in the existing evidence archive.

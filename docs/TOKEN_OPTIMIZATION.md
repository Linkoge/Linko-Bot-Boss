# Token and context optimization

Measured on 2026-10-11 against baseline commit `19b0de1`. Implementation and deployment receipts are recorded below. This is an implemented optimization, with recorded production baselines and a disposable real Codex acceptance run.

## Findings and execution path

The five production workers reported **183,387 tokens**: 181,555 input, 147,456 cached input, 34,099 estimated uncached input, and 1,832 output. Their returning manager consumed another **253,907 tokens**. The complete workflow therefore consumed **437,294**, not just the worker total. Manager usage existed in native events but was not copied into `executions.usage`.

| Stage | Code / operation | Inference and context |
| --- | --- | --- |
| User submission | `web.py` → `Chat.submit` | Persist user message and queued manager execution; select existing interactive route. |
| Manager initialization | `AppServer.start`, `thread/start` or `thread/resume` | Initialize/account/catalog/thread RPCs are not inference calls. Native Codex supplies its base instructions, environment and tool interface. Linko supplies manager developer instructions and `manager_action`. |
| Manager decision | `Chat._run` → one `turn/start` | Baseline sent project identity/path/objective/criteria/instructions, global instructions, owner notes, latest 20 tasks, mode and current message on **every** turn. New native threads also received 16 stored messages. Resumed threads already retained previous turns, tool calls and results. |
| Delegation | `Scheduler.manager_action` → `Tasks.add` | No separate planner or classification model. The manager's dynamic tool creates/reuses one durable assignment. Baseline returned the entire task row, including legacy and internal fields. Codex normally performs a second internal inference request after the tool result to acknowledge delegation. |
| Worker dispatch | Scheduler → `Workers._run` | One owned native App Server and one `turn/start` per attempt. New assignments start separate threads; continuing an assignment resumes its own thread. Input contains objective, project name/objective/instructions, global instructions and owner notes. Legacy attempts additionally retain previous workspace/output references. No entire manager conversation or task history is injected into ordinary workers. |
| Native execution | Codex tools | Codex chooses its own tool calls, validation and internal inference requests. Streamed agent output, command/tool events and usage are persisted. Routine native development has full access and retains the existing approval/question behavior. |
| Completion | `Workers.finish` | Persist output/error/status and pending delivery; release project claim after closing the owned worker process. |
| Automatic return | `Chat.deliver_results` → `Chat.submit` | Claim the pending result transactionally and start one manager turn in the exact originating conversation. Baseline completion text also included raw usage JSON, thread/turn/execution IDs, timestamps, model/effort, latency, workspace/branch/commit metadata. |
| Report / next assignment | Same manager turn and tool | Manager interprets actual results and reports or delegates the next authorized objective. Successful delivery is persisted. Duplicate completion/claim guards and failed-delivery backoff already existed and remain. |

All measured manager calls used **gpt-6.1-sol, low**; all workers used **gpt-6.1-sol, medium**. Model selection, reasoning settings, service tier, native tools, permissions, worker capacity and scheduler behavior were retained. The local Codex installation was 0.162.1. No separate summarizer, reviewer, planning agent or independent verification model exists in this pipeline.

### Application versus native overhead

Inspection of the actual saved production worker rollouts found:

- Codex base instructions: **21,769 characters**, supplied by the installed harness.
- First worker developer block: **10,358 characters**, of which Linko's worker instruction was **377 characters**. The remainder includes native skills/configuration/permission guidance; separate native collaboration blocks were also present.
- Environment user block: **398 characters** in the inspected project.
- Linko worker task/context JSON: **2,348 characters** for the first sequential assignment; the objective itself was only part of this, with saved owner notes also included.
- No applicable ancestor or project `AGENTS.md` existed for the measured project. Startup overhead was not caused by a giant project instruction file.
- The first worker read the cloud-environment skill and used two tool cycles; other sequential workers used one cycle. They generated three and two usage-bearing inference requests respectively, despite having only one application-started turn each.

These are **character counts, not token allocations**. Rollouts do not expose a complete per-source tokenizer accounting for native tools and instructions. They establish where context originates without pretending all provider-reported input is Linko-controlled. In particular, a file operation followed by a final answer normally requires a tool request and a subsequent inference request, each seeing the native prefix.

The manager rollout likewise contained Codex's base instructions and native developer/tool context in addition to Linko's 728-character manager instructions. Manager input per internal request increased from roughly 14,242 to 33,570 tokens during the five-task production chain. Reinjecting project/task context and returning full internal task rows made that retained conversation grow unnecessarily.

The [official App Server documentation](https://learn.chatgpt.com/docs/app-server) describes retained threads, turns, streamed tool items and `thread/tokenUsage/updated`. Native totals in the observed records are cumulative across a thread; `last` reports the latest inference request. Summing cumulative manager totals across executions would badly overcount. The benchmark sums distinct recorded request reports within each execution instead.

## Implemented changes

1. **Manager context updates.** Send full project context when attaching a fresh thread/context snapshot, then send only changed top-level context fields on subsequent turns. Save the snapshot after successful completion, bound to the native thread ID, so restart and archived conversations retain continuity. An empty replacement clears a removed instruction. Global project ordering is stable rather than changing with UI recency.
2. **Relevant assignment index.** Automatically include unfinished assignment references, status, blocker and a 300-character objective preview. Completed/cancelled/failed task history no longer accompanies every decision. Unfinished tasks are not dropped behind the former latest-20 cutoff. Full objectives and results remain retrievable.
3. **Compact action receipts.** Delegation/continuation/wait return task ID, objective, status and blocker instead of complete SQLite rows. Project pause/resume returns ID/status. UI and persistence still retain complete records.
4. **Compact worker completion.** Remove the nested execution metadata object. Keep task ID, actual attempted objective, task/attempt status, blocker/error and actual worker output with the existing truncation indicator. Detailed raw events, usage, native thread references and transcripts remain in persistence. Completion responses remain model-generated.
5. **Historical retrieval through the existing manager tool.** Add read-only `read_history`, `read_task`, `list_tasks` and `read_memory` actions with pagination/search and project/conversation isolation. History search gives message references and excerpts; full message pages can then be requested. This uses existing SQLite storage, with no embeddings, summarization model or memory framework.
6. **Fresh-thread continuity.** Keep the existing recent-16-message restoration and additionally preserve the latest owner request when a long automatic chain has pushed it outside that window. Owner messages remain complete. Large assistant/worker messages restore a marked excerpt and remain accessible in full by reference. Native resumed-thread history and its existing automatic compaction are retained.
7. **Worker startup/continuation.** Remove generic instructions to complete work intelligently, preserve work and report validation that native Codex already supplies. Worker developer instructions shrink **377 → 235 characters**, retaining autonomy and destruction/permission boundaries. Omit empty prompt fields. When resuming an already-started assignment, send the objective and changed context rather than unchanged project notes/instructions again. Keep complete context snapshots and actual sent prompts for diagnostics.
8. **Usage instrumentation.** Both manager and worker executions now persist native thread totals plus `usage.turn`, summed from distinct request reports during that execution, and `usageReports`. Manager latency is now recorded. Original events remain intact, including duplicate notifications; only aggregate accounting deduplicates them. `model.context` events preserve the actual application prompt sent.
9. **Deployment diagnostics.** `/api/auth-status` exposes the Git commit captured at backend creation and whether the scheduler task is running, allowing verification of the restarted release without invoking a model.

Manager instructions grew **728 → 982 characters** to explain context-update semantics, historical retrieval and concise delegation while preserving explicitly requested distinct workers. The measured savings come from removing repeated payloads, not from claiming every instruction string got shorter.

No application-started planner, classifier, duplicate worker turn or routine status-reasoning call was found to remove. The essential post-worker manager call remains. Exactly five distinct assignments remain five; native tool/final-answer inference cycles were not disabled or artificially capped.

## Baseline and after measurements

Baseline: online SQLite snapshot `/tmp/linko-token-baseline.sqlite3`, original production events and native rollouts. A is the recorded greeting; B creates `auto-test.txt` with `Hello from Codex.`; C changes it to `Second version.`; D is the owner's five-worker `10 → 15 → 30 → copy → verify` chain.

After: real subscription inference in `/tmp/linko-token-after`, using a disposable project/data directory, existing model routes/tools, the same saved owner memory, historical task descriptions, and equivalent requests. All fixture historical tasks were frozen as completed to prevent unintended dispatch. Existing unrelated fixture content was preserved. No production acceptance assignment was created.

**Comparability limits:** these are single observed runs, not randomized paired experiments. Paths, context age, historical task state and cache warmth differ. B/C followed a fresh greeting in the optimized fixture; the original B started a different manager thread. D in the fixture followed B/C and used slightly different wording with the same five dependent objectives. Model-selected commands and skill reads vary. Cache differences and worker call-count changes cannot be attributed solely to Linko. There was no second expensive baseline inference run.

Input, cached input, output and totals below come from **actual App Server usage reports**. Uncached input is the arithmetic estimate `input − cached`; it is not a provider billing statement. Native output includes reported reasoning output within the reported output total. Latency is wall time from the initial manager execution to the final returning manager completion, excluding pauses between benchmark cases.

### Complete pipeline

`M/W turns` are application-started manager/worker turns. `M/W requests` are distinct usage-bearing updates, a proxy for completed native inference requests; unreported failed/retried requests are not observable.

| Case | Version | M/W turns | M/W requests | Input | Cached input | Est. uncached input | Output | Total | Seconds | Result |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A conversation | Before | 1/0 | 1/0 | 12,452 | 0 | 12,452 | 12 | 12,464 | 3.778 | Success |
| A conversation | After | 1/0 | 1/0 | 11,406 | 0 | 11,406 | 18 | 11,424 | 3.876 | Success |
| B create file | Before | 2/1 | 3/4 | 108,226 | 86,912 | 21,314 | 978 | 109,204 | 44.999 | Success |
| B create file | After | 3/2 | 5/5 | 145,298 | 133,632 | 11,666 | 1,281 | 146,579 | 54.513 | Success after correction |
| C modify file | Before | 2/1 | 3/3 | 103,458 | 92,544 | 10,914 | 889 | 104,347 | 38.863 | Success |
| C modify file | After | 2/1 | 3/4 | 102,734 | 92,928 | 9,806 | 740 | 103,474 | 39.352 | Success |
| D five assignments | Before | 6/5 | 11/11 | 433,725 | 376,704 | 57,021 | 3,569 | 437,294 | 143.343 | Success |
| D five assignments | After | 6/5 | 11/12 | 353,629 | 309,760 | 43,869 | 2,436 | 356,065 | 111.727 | Success |

All four final outcomes succeeded in each set. One observation per case does not establish a statistical success rate. In optimized B, the worker initially omitted the final period; the manager detected that actual result and resumed the **same task/thread** to correct it without user intervention. The extra attempts/calls are included, not removed as an outlier. A benchmark wait initially counted attempts instead of distinct assignments; it was corrected, and only remaining C/D inference was run using the retained fixture. A/B were not rerun or discarded.

### Manager / worker breakdown

| Case / role | Version | Input | Cached | Est. uncached | Output | Total |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| B manager | Before | 39,796 | 24,576 | 15,220 | 220 | 40,016 |
| B manager | After | 59,421 | 57,600 | 1,821 | 330 | 59,751 |
| B worker | Before | 68,430 | 62,336 | 6,094 | 758 | 69,188 |
| B worker | After | 85,877 | 76,032 | 9,845 | 951 | 86,828 |
| C manager | Before | 51,792 | 47,104 | 4,688 | 214 | 52,006 |
| C manager | After | 37,844 | 36,736 | 1,108 | 180 | 38,024 |
| C worker | Before | 51,666 | 45,440 | 6,226 | 675 | 52,341 |
| C worker | After | 64,890 | 56,192 | 8,698 | 560 | 65,450 |
| D manager | Before | 252,170 | 229,248 | 22,922 | 1,737 | 253,907 |
| D manager | After | 160,957 | 140,800 | 20,157 | 1,174 | 162,131 |
| D workers | Before | 181,555 | 147,456 | 34,099 | 1,832 | 183,387 |
| D workers | After | 192,672 | 168,960 | 23,712 | 1,262 | 193,934 |

D observed **18.6% fewer total tokens**, **23.1% less estimated uncached input**, and **22.1% lower latency**. Manager input fell **36.2%**, with estimated uncached manager input falling **12.1%**. Worker total tokens increased **5.8%**, while estimated uncached worker input fell **30.5%** with better caching. This is not evidence that native worker startup overhead has been eliminated. Across all four cases, observed total tokens fell **6.9%**; B regressed in total tokens/latency and C's worker cost increased. No overall reduction in native inference request count is claimed.

Raw metric summaries preserve execution IDs in the private deployment evidence directory. Baseline greetings/initial calls, per-request cache values and final output sizes remain inspectable in original stored events; new runs additionally expose aggregates directly in `executions.usage`. Old usage records were not rewritten.

## Reproduction and tests

`scripts/token_benchmark.py` defaults to reporting selected recorded executions **read-only**, without inference:

```bash
PYTHONPATH=. .venv/bin/python scripts/token_benchmark.py \
  --database /path/to/recorded.sqlite3 --ids execution-id,another-id \
  --output /tmp/recorded-usage.json
```

`--live-directory /new/disposable/path`, `--seed-database`, and `--seed-project` explicitly opt into the four-case live acceptance pass. Fixtures and usage remain on disk for diagnostics. This is test instrumentation, not an application recovery mechanism.

Validation:

- **39 automated tests passed**, including new usage deduplication, resumed-thread accounting, context updates across restart, instruction removal, long-chain owner-request continuity, historical full-record retrieval and cross-project/conversation isolation.
- Existing tests cover Conversation/Auto/Execute, direct delegation, project capacity/exclusivity, result delivery after restart/archive, cleared origins, idempotent finish/claims, failed-delivery backoff and native questions/permissions.
- **Real acceptance:** greeting without delegation; one task for file creation (two native attempts with automatic correction); one modification task; five distinct sequential tasks with ordered completion; automatic manager return; zero approval questions; one completion message per attempt; exact final file bytes; unchanged unrelated fixture file and no extra project files.
- Established **browser smoke passed**: login/chat, streaming/selection/copy, all 425 historical messages, history archive/search, project rename, existing model routes and responsive layouts without JavaScript errors.
- Ruff, JavaScript syntax and Git whitespace checks passed. The existing Starlette TestClient/httpx deprecation warning remains; it did not affect results.

## Remaining overhead and ranked opportunities

1. **Native harness prefix and automatic skill/tool context** dominate fresh worker startup. Investigate supported upstream ways to avoid irrelevant startup material, preserving every useful tool and model/effort. Do not replace native worker base instructions or disable skills/tools just to shrink this benchmark.
2. **Manager's Codex harness prefix** remains substantial even with only a manager dynamic tool. A future supported lean manager integration could help; it would need separate authentication/tool/continuation validation and is outside this change.
3. **Very long native conversation history** still uses the existing 100,000-token manager auto-compaction threshold. Changing that trades cached input for compaction inference and possible lost continuity; measure dozens-of-task real workloads first. No new summarization calls or forced thread rotation were introduced.
4. **Large unfinished assignment lists** can still be material because unresolved references are intentionally preserved. If measured, send granular assignment changes rather than replacing that entire field. Completed history no longer grows the injected index.
5. **Process startup latency and model variance** remain. Workers still own separate App Servers and threads as appropriate. Pooling servers might save initialization time, but it has no proven token benefit here and adds lifecycle complexity. Exact literal constraints and concise manager objectives deserve further real-world observation after the B correction.

## Commit and deployment

Release implementation is prepared and validated in a separate checkout. Deployment follows `docs/operations.md`: verified online database backup and startup rehearsal, idle-execution check, stop only the dedicated user service, fast-forward the installed repository, start the same backend, verify commit/scheduler through the API, private HTTPS, assets and all preserved database records. No frontend rebuild or separate frontend restart is necessary: this backend serves the unchanged static frontend.

**Implementation commit:** `fb5fa2c570f89969eb966c4e6e89077d8878f1a2`.

**Deployed and verified:** the installed repository was fast-forwarded and `linko-bot-manager.service` safely restarted at **2026-10-11 19:07:15 +04**. The backend is active/running with exit status 0. Loopback and existing private HTTPS returned HTTP 200 for the web interface and API. The API reported the implementation commit and an active scheduler; served HTML/JavaScript/CSS matched release files. Startup/shutdown logs contained no errors. The existing daily backup timer remains active. Automatic continuation was validated by the real acceptance chain and persisted-delivery regression tests; no inference test was run on production data.

Backup, startup rehearsal, complete before/after database hashes and raw benchmark summaries are retained privately at:

```text
/home/codex/.local/share/linko-bot-manager/backups/token-optimization-20261011/
```

Production integrity and foreign keys passed. **Every original table, column, row count and row-value hash matched** after restart: 11 projects, 56 tasks, 10 conversations, 66 messages, 117 executions, 111,118 events, 55 memory rows, all settings/authentication/session records and historical diagnostics. No schema/data migration or historical usage rewrite was introduced. Tailscale listeners, unrelated processes, project files, task dispositions and settings were retained.

This deployment receipt is committed after the implementation. The final release includes that documentation-only follow-up; its exact running hash and service checks are recorded in private `after.json` and exposed as startup-captured `release_commit` by `/api/auth-status`. The final release uses the identical tested implementation above.

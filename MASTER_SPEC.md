# Linko Bot Manager requirements

Current architecture, superseding prior supervision requirements as authorized on 2026-10-11:

**User → AI Manager → Codex worker → AI Manager → User**

- Conversation mode is read-only; Auto delegates requested work; Execute treats the message as an objective through the same manager path.
- Natural conversational requests; the manager decides when requested work should be delegated.
- One ordinary request becomes one complete objective, with necessary project/conversation context.
- Native Codex tools handle investigation, editing, commands, dependency installation, builds, testing, debugging and Git.
- Codex decides validation. Completion reports evidence and limitations; it does not imply independent acceptance certification.
- No separate planner, task DAG, reviewer, verification controller, registered verification commands, permission profiles, capability broker or integration controller.
- No routine owner approval. Preserve explicit confirmation for catastrophic broad destruction, using native interaction where available and conversational continuation otherwise.
- Automatic durable worker-result delivery to the originating manager conversation, including when the browser disconnects or another project is selected.
- Existing and future registered projects participate automatically. One active coding worker per project, concurrent different projects, one global capacity limit.
- SQLite preserves conversations, assignments, execution history, question answers and result-delivery identity. Native Codex thread storage is also required for resumable model context.
- Restart recovery inspects prior outcomes before continuation. Never launch a competing worker while a prior owned process remains unresolved. Do not blindly replay a turn with unknown launch identity.
- Preserve deliberate pauses/cancellations, existing user edits, historical records and owner notes.
- While coding, Tasks/Agents expose Pause and Cancel with requested versus confirmed cleanup state. Pause promptly interrupts to a retained checkpoint; Cancel immediately signals owned execution. Retain partial work for review/resume. Manager pause and append-only instruction bundling resume the same unfinished assignment without competing workers. Contract: docs/AGENT_CONTROLS.md.
- Secure single-owner authentication, private hosting, CSRF/session protections and verified backup/restore remain.
- Native workers have broad same-user access; do not claim no-host-home isolation, transactional shell replay, source-preserving clones or deterministic prevention of arbitrary destructive code.
- No new orchestration framework, supervision AI calls, speculative policy engines or manager-created subagent hierarchy.

Implementation uses existing FastAPI, SQLite, plain JavaScript and Codex App Server. Model settings contain only manager and worker roles. Old requirements are retained in Git history and historical stage reports.

## Project memory

- Manager planning/brainstorming uses one derived persistent compact project brief (purpose, goals, constraints, established state, active decisions/reasons, unresolved work, three recent meaningful deliveries and retrieval references). Target 1500 tokens, maximum 2000 excluding shared global instructions; overflow must preserve active requirements and surface a gap, never silently truncate.
- Authoritative specs/docs, complete historical evidence and archived transcripts remain retrievable; fresh chats exclude archived transcripts. Recent deliveries include date/change/effect/verification/deployment/report, archive oldest on the fourth and retain current lasting effects/unresolved blockers. Related fixes share one delivery ID.
- Worker assignments include objective/acceptance/relevant constraints/dependencies/selected context, with necessary source inspection and repository/security instructions; no automatic bulk brief/history injection. Completion preserves detailed evidence and updates relevant authoritative docs/current facts/recent entry with truthful proposed/implemented/verified/deployed status. Deployment needs durable success evidence.
- Full reconciliation is only an explicit project-scoped owner workflow: explain scope/unknown cost, Start, progress/actual reported usage, retained old/candidate versions, fact diff/references/unresolved conflicts, explicit Apply, retrievable prior version/rollback. Failed/cancelled review leaves active memory intact. Missing code/ambiguous conflicts cannot silently erase goals. No recurring/background reviews. Global owner communication preference persists across fresh/cleared chats. Conversation compaction is separate future scope.

Implementation contract and evidence navigation: docs/PROJECT_MEMORY.md.

# Linko Bot Manager requirements

Current architecture, superseding prior supervision requirements as authorized on 2026-10-11:

**User → AI Manager → Codex worker → AI Manager → User**

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
- Secure single-owner authentication, private hosting, CSRF/session protections and verified backup/restore remain.
- Native workers have broad same-user access; do not claim no-host-home isolation, transactional shell replay, source-preserving clones or deterministic prevention of arbitrary destructive code.
- No new orchestration framework, supervision AI calls, speculative policy engines or manager-created subagent hierarchy.

Implementation uses existing FastAPI, SQLite, plain JavaScript and Codex App Server. Model settings contain only manager and worker roles. Old requirements are retained in Git history and historical stage reports.

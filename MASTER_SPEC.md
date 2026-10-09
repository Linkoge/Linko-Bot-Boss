# AI Manager — Master Implementation Specification

Permanent owner requirements, received 2026-10-09. Repository: `git@github.com:Linkoge/Linko-Bot-Manager.git`.

## 1. Mission

Build a persistent AI manager that allows one human owner to manage multiple software and business projects through a single conversational interface.

The user should be able to communicate naturally with the manager, just as they communicate with ChatGPT.

The manager must understand project context, remember decisions, plan work, launch and supervise Codex agents, track progress, enforce configurable autonomy policies, and report results.

Primary objective: reduce the owner's cognitive workload and project-management overhead.

The owner should not need to manually copy instructions between agents, remember which session contains which project, or reconstruct previous work after interruptions.

Prioritize usability, autonomy, persistence, and reliability over architectural complexity.

## 2. Existing environment

- Linux virtual machine running on a Linux Mint host.
- Existing Codex CLI and Codex App Server capabilities.
- ChatGPT Plus subscription, approximately $20/month.
- Multiple existing GitHub repositories and Codex projects.
- Existing tmux sessions, SSH access, and Tailscale networking.
- Existing projects may include Linko, Linko Gym Coach, AI Manager, infrastructure tools, and experimental applications.
- An existing AI Manager repository or implementation may already exist.

Discover the actual environment before making assumptions.

Reuse existing working components wherever practical.

Do not disrupt unrelated processes, repositories, remote access, or running Codex sessions.

## 3. Core architecture

Build a Python-based backend with:

- SQLite database, using WAL mode where appropriate.
- Persistent background service.
- Codex App Server integration.
- Task scheduler and execution queue.
- Project registry and memory system.
- Agent lifecycle supervisor.
- Event and audit logging.
- Configurable model routing.
- Configurable approval and permission policies.
- Mobile-friendly web dashboard.
- Streaming conversational interface.

Use a modular architecture so components can be improved independently.

Avoid Kubernetes, distributed databases, or other unnecessary infrastructure.

Use an appropriate modern Python web framework and document the choice.

## 4. Project registry

Automatically discover existing projects and repositories.

For every project, track:

- Name and description.
- Repository and working directory.
- Objectives and success criteria.
- Priority and lifecycle status.
- Active, completed, and pending tasks.
- Dependencies and blockers.
- Assigned agent identity.
- Recent activity and Git history.
- Relevant documentation.
- Last successful checkpoint.
- Pending decisions requiring owner input.

Support manually adding, editing, archiving, and removing projects from the registry without deleting their source files.

Projects must be independently manageable.

Support project-specific and global instructions.

## 5. Persistent project memory

Every project must maintain durable, human-readable documentation:

- "PROJECT.md": objectives, scope, architecture, and important context.
- "STATE.md": current verified status and recent accomplishments.
- "TASKS.md": outstanding work and priorities.
- "DECISIONS.md": decisions and reasoning.
- "HANDOFF.md": instructions for the next agent session.

Adapt to existing documentation rather than overwriting it.

Keep an authoritative structured task and execution database.

Persist:

- Codex thread and turn identifiers.
- Task ownership and execution state.
- Worktree and branch information.
- Git commit references.
- Agent outputs and errors.
- Test results and verification evidence.
- Checkpoints and timestamps.

Agents must update state after meaningful milestones, not merely at session termination.

Before resuming a project, reconcile stored state with actual files, Git history, and running processes.

Never treat an agent's unsupported claim of completion as verified completion.

Prevent concurrent agents from overwriting one another's state updates.

## 6. Conversational interface

Create a modern, responsive web interface optimized for Android phones and desktop browsers.

The main interface should contain:

- Chat panel.
- Project selector/sidebar.
- Project dashboard.
- Active agent status.
- Task queue and progress.
- Approval notifications.
- Settings.
- Searchable conversation history.

### Project-aware conversations

When the user selects a project:

1. Set that project as the active context.
2. Retrieve its current summary, goals, state, decisions, and tasks.
3. Preserve relevant conversation history.
4. Retrieve additional information only when needed.
5. Send the selected project identity and relevant context with each message.

Do not repeatedly inject entire repositories or Markdown files into prompts.

Support a global "All Projects" context for portfolio-wide management.

Maintain separate project conversations while allowing the manager to reference information from other projects when authorized.

Project switching must be fast and must not destroy conversation history.

### Chat behavior

- Stream responses.
- Prefer concise conversational replies.
- Allow long technical explanations when requested.
- Support follow-up questions and brainstorming.
- Understand natural-language task delegation.
- Display agent actions and task references.
- Keep chatting responsive while background agents execute.
- Support cancellation and interruption.
- Distinguish proposed actions from successfully executed actions.

## 7. AI model configuration

Implement configurable model routing.

Separate the following roles:

1. Interactive conversational manager.
2. Planning and orchestration agent.
3. Project execution agents.
4. Verification and review agents.
5. Summarization and memory maintenance.

Default strategy:

- Interactive chat: strong model, low reasoning effort.
- Routine planning: strong model, medium effort.
- Complex architecture: higher reasoning effort.
- Coding: suitable Codex coding model.
- Routine status checks: deterministic Python without LLM calls.

Verify which models and reasoning effort levels are actually available through the installed Codex version and authenticated subscription.

Do not assume API access or that all ChatGPT models are available through Codex.

Avoid introducing separately billed APIs without explicit approval.

Expose available model and reasoning settings in the dashboard.

Support per-role overrides and future model changes.

Record model usage, failures, latency, and available usage information.

Implement context compaction and retrieval to prevent conversations from becoming unnecessarily slow.

## 8. Codex worker orchestration

Use Codex App Server where supported and appropriate.

Implement:

- Launching workers.
- Resuming existing threads.
- Sending instructions.
- Receiving streaming events.
- Tracking execution status.
- Detecting errors.
- Pausing or interrupting tasks.
- Retrying recoverable failures.
- Recording results.
- Recovering after service restart.

Start with a configurable maximum of two concurrent execution workers.

Separate logical project-agent identity from physical Codex process lifetime.

Workers should not need to run continuously.

Use isolated Git worktrees or equivalent workspace isolation when concurrent edits could conflict.

Prevent duplicate task execution using durable task ownership, leases, and recovery logic.

Never infer that a worker stopped merely because a network connection disappeared.

Ensure that worker processes and subprocesses can be identified and supervised.

## 9. Autonomous planning

The manager must convert natural-language goals into actionable tasks.

Each task should have:

- Unique identifier.
- Project association.
- Description.
- Priority.
- Dependencies.
- Acceptance criteria.
- Assigned agent.
- Execution status.
- Verification requirements.
- Relevant permissions.

The manager should:

- Identify work that can proceed independently.
- Schedule tasks according to priorities and dependencies.
- Avoid unnecessary parallelism.
- Detect blockers.
- Delegate tasks to suitable agents.
- Evaluate completed work.
- Create follow-up tasks when justified.
- Avoid repeatedly reopening completed work without reason.

Support user instructions such as:

"Prioritize Linko."

"Pause Gym Coach after the current safe checkpoint."

"Continue the unfinished infrastructure tasks."

"Show everything waiting for my approval."

"Finish the three most important outstanding tasks."

"Don't start any new projects this week."

The manager should also recognize when too many projects are active and recommend concentrating on fewer priorities.

## 10. Configurable autonomy and permissions

Implement a policy engine with three principal action settings:

- ALLOW: execute without asking.
- ASK: require owner approval.
- DENY: prohibit execution.

Policies must be configurable through the dashboard.

Support global defaults and project-specific overrides.

Configurable action categories should include:

- Reading files.
- Editing project files.
- Running tests.
- Installing dependencies.
- Creating Git commits.
- Merging branches.
- Pushing to remote repositories.
- Deploying applications.
- Deleting files.
- Modifying infrastructure.
- Changing network configuration.
- Accessing credentials.
- Starting external services.
- Performing external communications.
- Spending money or using paid services.

The user must be able to change policies without editing application source code.

Show the effective policy before consequential actions.

Persist an audit trail of approvals and policy changes.

IMPORTANT: UI policies and AI instructions are not sufficient security boundaries.

Enforce sensitive restrictions using OS permissions, workspace isolation, scoped credentials, and controlled execution capabilities wherever feasible.

Do not give unrestricted workers access to the manager's administrative credentials or policy database.

Support explicit risk acceptance when the owner chooses less restrictive policies, but never silently claim restrictions are enforced when they are not.

## 11. Dashboard and notifications

Create dashboard views for:

### Portfolio

- All projects.
- Priority and status.
- Active workers.
- Recent accomplishments.
- Blocked tasks.
- Pending approvals.
- Next recommended actions.

### Project detail

- Current objective.
- Project memory.
- Task list.
- Recent commits.
- Active agent.
- Execution history.
- Chat conversation.
- Project-specific settings.

### Agent monitor

- Agent identity.
- Assigned project and task.
- Current execution state.
- Start time and duration.
- Recent activity.
- Logs and errors.
- Pause, resume, and stop controls.

### Approval center

- Requested operation.
- Reason for approval.
- Affected files or resources.
- Expected consequences.
- Approve or reject controls.

Provide optional daily summaries and event notifications.

Do not spam the owner with routine updates.

## 12. Authentication and access

The interface must support authenticated access.

Requirements:

- Secure initial owner account setup.
- Password hashing using a suitable password hashing algorithm.
- Secure session management.
- Login/logout.
- Session expiration and revocation.
- CSRF protection where applicable.
- Appropriate protection against brute-force login attempts.
- No credentials exposed in frontend code or logs.

Initially deploy the interface privately through Tailscale.

Do not expose it to the public internet without explicit approval.

Preserve existing SSH, Tailscale, RustDesk, and remote-access functionality.

Design the authentication system so stronger authentication can be added later.

## 13. Reliability and recovery

The system must survive:

- Browser disconnection.
- SSH disconnection.
- Codex session termination.
- Manager service restart.
- VM reboot.
- Individual worker failure.
- Interrupted task execution.
- Temporary model unavailability.

Use durable checkpoints and an explicit task state machine.

Distinguish pending, running, paused, blocked, awaiting approval, failed, and completed states.

Implement database backups and recovery procedures.

Test recovery using controlled failures.

Do not automatically repeat destructive or non-idempotent operations after uncertain execution outcomes.

Provide logs sufficient to diagnose failures.

## 14. Implementation stages

Execute development in the following order.

### Stage 1 — Inventory and foundation

- Inspect the existing environment.
- Locate existing AI Manager components.
- Discover projects and repositories.
- Design the database.
- Implement project registry.
- Implement persistent project state.
- Establish tests.

Deliver a working project overview.

### Stage 2 — Web interface and chat

- Implement authentication.
- Build responsive dashboard.
- Add project selector.
- Add project-aware conversations.
- Add streaming chat.
- Add conversation persistence.
- Implement model configuration.

Deliver a usable conversational manager.

### Stage 3 — Codex integration

- Integrate Codex App Server.
- Launch and resume workers.
- Track execution events.
- Implement task assignment.
- Persist execution history.
- Add agent monitoring.

Deliver a manager capable of supervising real Codex tasks.

### Stage 4 — Autonomous orchestration

- Implement planning.
- Implement scheduler.
- Add task dependencies.
- Add automatic delegation.
- Add completion verification.
- Add safe interruption and recovery.

Deliver autonomous multi-project execution.

### Stage 5 — Policies and controls

- Implement configurable permission engine.
- Implement approval queue.
- Add project-specific overrides.
- Add operational security boundaries.
- Add notifications.
- Add comprehensive audit logging.

Deliver configurable autonomy.

### Stage 6 — Production hardening

- Test recovery after reboot.
- Test worker crashes.
- Test concurrent execution.
- Test state consistency.
- Test authentication and authorization.
- Test backup restoration.
- Optimize performance and responsiveness.
- Complete documentation.

Deliver a stable daily-use system.

## 15. Development operating rules

Maintain a master implementation plan with explicit stage status.

After each meaningful milestone:

1. Run relevant tests.
2. Record verified accomplishments.
3. Update state documentation.
4. Record outstanding problems.
5. Identify the next task.
6. Continue automatically when safe and authorized.

Do not require the owner to manually copy prompts between implementation stages.

If interrupted, the next session must be able to resume using persisted state.

Avoid rewriting working components unnecessarily.

Prefer small, verifiable changes.

Do not claim a stage is complete without testing its acceptance criteria.

If an action requires unavailable permissions, authentication, or owner input, record the blocker and continue independent work where possible.

Do not create new projects or unrelated features without justification.

## 16. First-run instructions

Begin by inspecting the environment and identifying reusable existing code.

Create the master implementation plan and persistent project state.

Then implement Stage 1 and proceed toward a usable Stage 2 interface.

Continue through later stages automatically when the preceding acceptance tests pass and required permissions are available.

Request owner input only for genuine architectural decisions, missing credentials, risky operations, or actions requiring approval.

Do not disrupt existing infrastructure.

At every stage, preserve enough context for a fresh Codex session to continue independently.

The final objective is a system in which the owner can open one web interface, select any project, talk naturally with the AI manager, and have it coordinate persistent autonomous Codex agents on the owner's behalf.

Treat this specification as the permanent project requirements.

Save it in the repository as MASTER_SPEC.md. Maintain IMPLEMENTATION_PLAN.md and STATE.md.

Implement the system incrementally, following the six stages. Do not attempt to implement everything in one enormous change.

After completing each stage, run tests, update the documentation, create a Git checkpoint, and continue to the next stage automatically when safe.

Do not ask me to resend instructions or manually initiate the next stage. If interrupted, resume from the last verified checkpoint.

Prioritize delivering a usable system quickly, without sacrificing reliability.

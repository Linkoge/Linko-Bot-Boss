# High autonomy upgrade — implementation and activation contract

## Baseline inspection (2026-10-10)

Reuse FastAPI/plain JavaScript, SQLite WAL schema 4, authenticated CSRF-protected owner routes, scoped registry and revision-locked Markdown memory, persistent plans/tasks/dependencies, leases, scheduler, App Server catalog/model routes, isolated local clones, exact single-use approvals, bubblewrap tests, and independent file-inspecting reviews. Preserve every existing record and the compact two-row navigation.

Current policies allow reads/tests, ask for edits/deletes/commits, and technically disable unimplemented infrastructure/network/credential/spending operations. Policies are explicit project > global > code defaults. Scheduler defaults paused. Launch/resume/branch creation are currently implicit permitted operations. Commands are manually registered argv arrays with no config identity or discovery. All task types require commands. Verification retry is manual; uncertain executions correctly require reconciliation. Existing 48 tests and disposable real Codex acceptance scripts are reusable. Prior production blockers concern missing approved commands, isolated dependencies and acceptance beyond available host capabilities; this upgrade must not certify those tasks by changing their criteria.

## Proposed policy (requires authenticated owner activation)

Keep a legacy profile until owner activation, preserving all existing effective modes and scheduler settings. Recommended High Autonomy permits scoped reads, edits, creation/deletion of ordinary files, approved tests, local dependency provisioning, worker launch/resume, isolated branches/commits and verified merges where a project explicitly permits integration. Production deployment, external pushes, host services/networking, secrets, destructive data operations, cross-project workspace access, external APIs and spending require approval or remain unavailable at the technical boundary. Maximum Autonomy still has those boundaries and explicit project infrastructure authorization; it does not grant host access.

Conservative permits reading and isolated verification and requires approval for development mutations, launching mutable workers, branches, commits and merges. It does not remove technical boundaries.

Precedence: non-bypassable technical capability/workspace restrictions > owner-authored project policy > owner-authored global policy > selected project/global profile > legacy defaults. Project ALLOW is itself an explicit authenticated owner decision, audited with the previous effective value. A profile switch preserves existing explicit policies. One consolidated preview lists actual effective changes, scope, restrictions, scheduler behavior and unavailable capabilities; activation binds the preview hash and policy epoch. No profile is activated by discovery, worker output, migration or this implementation task.

## Verification and recovery contract

Discover argv candidates from bounded, non-symlink project configuration, CI/docs and prior successful commands without executing repository scripts. Approval records bind argv, project-relative working directory, immutable sandbox identity and configuration hashes. Persist decisions/revocations; materially changed configuration needs renewed approval. Unchanged approved commands do not prompt per task. Repository code remains untrusted even when commands are approved.

Task kinds: code, documentation, research, infrastructure, investigation. All retain explicit acceptance and independent review. Non-code strategies inspect deliverables and structured evidence; infrastructure completion needs actual observed state/health/rollback evidence, never a narrative alone. Missing capabilities remain blockers. Verified/partial/failed/blocked/unverified results are recorded separately from worker claims.

Automatic recovery is limited to known pre-dispatch blockers or missing command configuration after confirmed worker completion. Never replay a thread, uncertain process or started operation. Preserve blocker history in events. Existing terminal tasks remain terminal.

## Delivery evidence

Implementation and test results will be appended here. This contract is defined before permission changes. Production activation and production rollout are separate owner decisions.

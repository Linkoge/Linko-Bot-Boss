# Enforcement and limits

The authenticated owner controls registry, task scheduling and policies. Policies apply to worker capabilities, including independent verification. ALLOW/ASK/DENY is resolved from project overrides and global defaults. Every consequential operation is checked by trusted Python code; prompts are not the permission boundary.

## Available capabilities

- Read/list text files in the assigned isolated clone only. Absolute paths, traversal, escaping symlinks, Git internals and known credential filenames/directories are rejected.
- Write/delete individual files with a current content hash. The owner reviews the exact path, contents/hash and isolated resource when ASK applies. Stale files or approvals cannot execute.
- Run only owner-registered argument arrays. Workers cannot submit arbitrary shell commands. Bubblewrap isolates mount/PID/network namespaces, clears inherited environment and mounts only system runtimes and the isolated workspace. Host home, manager database, session/password stores, SSH agents, credential directories and network are unavailable. Git admin storage is read only. Time and output limits bound tests.
- Create commits in the isolated clone only, with hooks and global Git configuration disabled. Approval binds the workspace state. No source checkout, remote push or merge follows automatically.

Every execution has a standalone clone and branch; workers do not share source working trees or Git admin state. The manager's runtime directory is private (0700), database and bootstrap token 0600. Test processes receive no inherited credential environment.

## Approval lifecycle

Approvals bind the execution, tool-call identity, action, arguments, file hashes and policy epoch. They expire in 15 minutes and are consumed once. A policy change invalidates outstanding grants. The durable operation ledger blocks replay of uncertain outcomes. Restart cancels outstanding grants and requires reconciliation. Browser loss does not grant approval or cancel background execution.

## Unavailable actions

Dependency installation, merge, push, deployment, infrastructure/network changes, credential access, external services, communications and paid APIs have no executable worker capability. Choosing ALLOW requires explicit risk acceptance where appropriate, but does not enable an absent capability. The dashboard identifies these limits. Future capabilities must add scoped execution and their own acceptance tests before activation.

The existing Codex ChatGPT login supplies inference; no API-key provider or separate billed API is introduced. Standard service tier is requested. Subscription limits and any account-level credit arrangements remain governed by the owner's existing Codex account.

## Trust boundaries

The Python service and App Server transport are trusted and run as the host owner. The transport needs the existing Codex authentication store. Model-controlled execution is limited to brokered tools; built-in shell/exec/patch, plugins, connectors and subagents are disabled and unsupported requests are refused. This is not a separate Unix-user boundary around the trusted App Server itself. If that process or the service is compromised, same-user host access is possible. Do not describe it as protecting against compromise of the trusted backend.

Owner-registered tests can read project content inside their clone, including content already committed by the owner. Known filename checks are not a universal secret scanner. Do not commit credentials; use scoped project fixtures. Tests have no host administrative credentials or network.

Initial hosting is loopback with a separate private Tailscale HTTPS listener. Passwords use Argon2id; cookie sessions are hashed, expiring and revocable. CSRF/origin/host checks, CSP and login throttling apply. There is no public signup after one-time local-token setup. This is a single-owner application, not a multi-user permission system.

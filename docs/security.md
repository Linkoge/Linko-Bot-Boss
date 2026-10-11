# Enforcement and limits

The authenticated owner controls registry, task scheduling and policies. Policies apply to worker capabilities, including independent verification. Routine development always resolves to ALLOW globally, overriding legacy project/global profiles without deleting them. Catastrophic operations always resolve to ASK, even if a legacy override says ALLOW. See [AUTONOMY_POLICY.md](AUTONOMY_POLICY.md). Every consequential operation is checked by trusted Python code; prompts are not the permission boundary.

## Available capabilities

- Read/list text files in the assigned isolated clone only. Absolute paths, traversal, escaping symlinks, Git internals and known credential filenames/directories are rejected.
- Write/delete individual files with a current content hash. Writes and deletions execute autonomously, retain preimage backups, and reject stale hashes.
- Run worker-selected argv, including shell scripts, in the assigned workspace. Tests and independent checks use disposable copies with input hashes checked. Ordinary commands can edit/build/use Git; dependency installation can use the network. Bubblewrap clears inherited environment and mounts system runtimes plus the isolated workspace, without host home, manager data, credential directories or service sockets. Test networking and Git administration remain read only/disabled. Ordinary commands have writable Git administration, validated before controller access; unsafe metadata is automatically restored from the recovery copy.
- Create isolated commits without approvals. Independently verified required commits/merges execute automatically; a merge defaults to the source current branch, requires a clean unchanged source and uses fast-forward only. There is no automatic push or host deployment.


Every execution has a standalone clone and branch; workers do not share source working trees or Git admin state. The manager's runtime directory is private (0700), database and bootstrap token 0600. Test processes receive no inherited credential environment.

## Approval lifecycle

Approvals bind the execution, tool-call identity, action, arguments, file hashes and policy epoch. They expire in 15 minutes and are consumed once. A policy change invalidates outstanding grants. The durable operation ledger blocks replay of uncertain outcomes. Restart cancels outstanding grants and requires reconciliation. Browser loss does not grant approval or cancel background execution.

## Unavailable actions

Host deployment, infrastructure/network administration, credential access and credential-backed remote integrations have no executable worker capability. Project-local commands, services, dependency installation and local Git operations are available. Choosing ALLOW requires explicit risk acceptance where appropriate, but does not enable an absent capability. The dashboard identifies these limits. Future capabilities must add scoped execution and their own acceptance tests before activation.

The existing Codex ChatGPT login supplies inference; no API-key provider or separate billed API is introduced. Standard service tier is requested. Subscription limits and any account-level credit arrangements remain governed by the owner's existing Codex account.

## Trust boundaries

The Python service and App Server transport are trusted and run as the host owner. The transport needs the existing Codex authentication store. Model-controlled execution is limited to brokered tools; built-in shell/exec/patch, plugins, connectors and subagents are disabled and unsupported requests are refused. This is not a separate Unix-user boundary around the trusted App Server itself. If that process or the service is compromised, same-user host access is possible. Do not describe it as protecting against compromise of the trusted backend.

Commands can read project content inside their clone, including content already committed by the owner. Known filename checks are not a universal secret scanner. Do not commit credentials; use scoped project fixtures. Tests have no host administrative credentials or network. Dependency installation shares host networking for downloads, without inherited credentials or mounted host service sockets. The catastrophic classifier is an explicit-command confirmation trigger, not a complete analysis of arbitrary programs. Network-enabled provisioning does not isolate unauthenticated network services; filesystem isolation and recovery protect local repositories, while network services still need their own authentication.

Initial hosting is loopback with a separate private Tailscale HTTPS listener. Passwords use Argon2id; cookie sessions are hashed, expiring and revocable. CSRF/origin/host checks, CSP and login throttling apply. There is no public signup after one-time local-token setup. This is a single-owner application, not a multi-user permission system.

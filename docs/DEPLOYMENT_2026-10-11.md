# Full manager deployment — 2026-10-11

The owner explicitly requested deployment of everything and an immediate restart after the global autonomy implementation.

The complete tested working tree, including the existing chat-history controls, navigation changes and global scheduling defaults, was committed as **`0e6ee27a48c73f8afbfe9578a866d6675463b2e1`**. It includes autonomy commit `87c09c4`. The already-installed service executes this repository directly; dependencies and its systemd unit did not require changes.

The dedicated `linko-bot-manager.service` restarted at **2026-10-11 10:21:47 +04**, with PID changing from 1521798 to 1585576. It is active/running with exit status 0. Loopback and existing private HTTPS `/api/auth-status` returned HTTP 200. Served JavaScript, HTML and CSS matched the release files exactly. No Tailscale listeners, other services or network configuration were changed.

Before restart, the production database received an online integrity/foreign-key-checked backup at:

```text
~/.local/share/linko-bot-manager/backups/deployment-20261011-102053/manager.sqlite3
```

Private `before.json` and `after.json` receipts alongside it preserve deployment comparison evidence. There were no unfinished executions before restart. Post-restart SQLite integrity and foreign keys passed; schema remains 6. All 42 original tasks, 58 messages, 73 executions, 108050 historical events, 4 checkpoints, 6 verification-result rows and the existing approval row remain. Historical messages/events/checkpoints/verification results are byte-for-byte unchanged as SQLite row values. Existing terminal/paused task dispositions and enabled global continuous scheduler settings were preserved.

A read-only evaluation confirmed routine ALLOW and catastrophic ASK across all 11 registered projects. Existing task `6d1fca78` was already cancelled in the pre-restart snapshot and remains cancelled. Task `6aa6574b` retains its original ID/workspace and confirmed finished worker. Automatic recovery currently skips it because two earlier operation-ledger rows remain `started` with uncertain outcomes. No task was recreated, old operation receipt rewritten, or uncertain operation replayed. This is a historical execution-reconciliation issue, not a new requirement to register verification commands.

Validation before deployment: **136 working-tree regression tests passed**, and Ruff, JavaScript syntax and Git whitespace checks passed. The autonomy-only staged checkout separately passed 127 tests; the real Codex Hello World/coding smoke completed with independent verification and zero approvals. No production test goal, chat archive/clear, password/session change, source merge, task reset or replacement task was initiated during deployment.

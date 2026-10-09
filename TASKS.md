# Outstanding work

Stages 1–5 have verified checkpoints for supported capabilities. Stage 6 is partially verified; see STATE.md and docs/verification.md.

1. Resolve the actual VM storage gate before large-project execution. About 445MB was free on a 40GB disk; do not delete unrelated repositories, Docker volumes/images, backups or runtime files automatically. Expansion/cleanup needs an owner-selected resource plan.
2. Coordinate a VM reboot acceptance window that preserves active sessions. After reboot, verify user service, private HTTPS, registry/conversation/checkpoint persistence, backup timer and uncertain-worker reconciliation. Linger and enabled units alone do not prove reboot acceptance.
3. Owner chooses initial username/password using private CLI setup or one-use local bootstrap token. No credentials may be committed or printed by agents.
4. Configure project objectives, trusted verification argv and desired policies through the UI. Test dependencies must be available in the isolated runtime; missing dependencies block verification.
5. Design and verify additional controlled capabilities for dependency environments, branch integration, remote push and deployment before enabling them. Current unavailable actions remain visibly unavailable; never replace them with arbitrary shell access.
6. Add owner-managed off-VM backup copies and capacity/retention choices. Current timer creates local verified backups without deleting older copies.

No implementation stage should be reopened merely to regenerate already passing evidence. Continue from the first unresolved acceptance gate when its actual external conditions change.

# Inventory — 2026-10-09

- Target: empty `/home/codex/projects/Linko-Bot-Manager`, origin `git@github.com:Linkoge/Linko-Bot-Manager.git`.
- Ten repositories discovered and registered by the actual CLI: AI-Manager, Linko, Linko-Bot-Manager, Linko-SSH, Okashi-Frontliner, linko-dev-panel, linko-gym-coach, linko-screenshotter, linkoge-profile, my.gov.ge-Tracker.
- Other folders are not automatically assumed to be Git projects; manual registration supports them.
- Existing AI-Manager is a Chatwoot customer-service deployment with email/Messenger. No source or configuration copied; no secret files read. Its outstanding unrelated changes are preserved.
- Available tooling: Python 3.14.4 system; uv-selected CPython 3.12.15 for application; uv 0.12.23; Git 2.53.0; Codex 0.161.0 with App Server stdio and JSON schema generation.
- Host service and session sockets inaccessible in sandbox. `systemctl --user` and `tmux list-sessions` report permission failures. Tailscale daemon cannot be reached here. These do not establish host service/VPN health.
- Managed-cloud environment-status tool and `/etc/codex/network-policy.json` are not provided in this executor. Dependency installation required approved network escalation, succeeded through ordinary uv. No network configuration changed.

## Foundation evidence

`uv run --offline pytest -q`: 4 tests passed covering real temporary Git discovery, source preservation, registry deletion, stale revisions, path scope, dependency order, concurrent claim ownership, explicit completion verification, uncertain-outcome restart and integrity-checked backup restoration.

`uv run --offline python -m ai_manager.cli discover` and `overview`: ten real repositories displayed. No agents assigned work and no unrelated process started or stopped.

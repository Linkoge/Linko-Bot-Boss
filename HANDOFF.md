# Session handoff

Work inside `/home/codex/projects/Linko-Bot-Manager`. Read MASTER_SPEC.md, IMPLEMENTATION_PLAN.md, STATE.md, TASKS.md and git status/log. Permanent instructions remain in the repository; never ask the owner to copy prompts between stages.

Stages 1–5 have local verified checkpoints for supported capabilities. Stage 6 hardening/private deployment is implemented and partially verified, with storage and full reboot acceptance still open. Use the final local Git checkpoint as the baseline. Do not claim all permanent requirements are finished.

Live private URL: https://codex-server.tail5ebb9d.ts.net:8787/. Dedicated user service `linko-bot-manager.service` and `linko-bot-manager-backup.timer` are enabled/active; linger is yes. Runtime: `~/.local/share/linko-bot-manager`. Secure owner setup remains pending; never read/print the setup token or choose a password for the owner. CLI setup prompts privately.

The VM disk is 99% full (~445MB free); large clone/backup guards fail closed. Do not clean unrelated data or reboot while unrelated sessions are active. Existing `../AI-Manager` is a separate Chatwoot deployment with an unrelated dirty docs/whatsapp-coexistence.md. Preserve its private Tailscale Serve 443/3000 listeners, all tmux/Codex sessions, SSH/RustDesk/networking. Only manager port8787 was added, privately; no Funnel.

Approved native execution provides actual systemd/Tailscale access. Web tests need native execution because sandbox TestClient's thread portal stalls. Run `uv run --offline pytest -q`, Ruff and JS syntax checks. Real disposable subscription smoke scripts and browser checks are documented in docs/verification.md. Last tests: 31 backend checks, desktop/mobile browser, actual Codex coding/approval/review under service security settings, physical process crash and actual manager service crash/restore checks.

Read docs/security.md for exact boundaries: supported brokered file/test/isolated-commit capabilities, no arbitrary shell or unsupported sensitive actions; trusted App Server runs as owner. Code results remain in isolated clones. No push or merge was performed. Absolute Codex path must be persisted in the systemd unit because default systemd PATH excludes ~/.local/bin.

Next independent work is governed by TASKS.md; coordinated reboot and storage changes require concrete owner context. For future code milestones: test, record verified evidence and unresolved issues, update these documents, create a local Git checkpoint, and continue safely. Reconcile DB/thread/process/workspace before resuming uncertain execution; never replay destructive effects automatically.

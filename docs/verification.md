# Verification record — 2026-10-09

The checked-in scripts/tests reproduce individual checks; actual model tests use the existing ChatGPT-authenticated Codex subscription, not mocked inference. No other catalog model is claimed as inference-verified.

| Check | Evidence and result |
| --- | --- |
| Backend acceptance | 31 pytest checks: registry/source preservation; SQLite revisions, leases and state machine; auth/CSRF/throttling; stream/restart persistence; policy invalidation/expiry; scoped document retrieval; concurrency; unknown-outcome recovery; backup restore; reviewed patch applicability |
| Static checks | Ruff passed; frontend JavaScript syntax passed |
| Browser | Playwright with installed Chrome: isolated setup/login, ten-project overview, project switching/memory; desktop1440×1000 and Android393×851; no page errors or horizontal overflow |
| Real streamed chat | GPT-6.1 Sol/low returned MANAGER_READY; new App Server process resumed saved thread and returned RESUME_READY; persisted turn/output/latency/usage |
| Real worker | GPT-6.1 Sol/medium read and checkpointed disposable project through brokered tools; fresh process resumed thread/workspace; result required verification |
| Real orchestration | Planning → scheduler → worker → isolated unittest → independent reviewer with actual file inspection → verified completion/checkpoint |
| Real coding/approval | Calculator fixture a-b→a+b: source unchanged before/after; exact ASK hash-bound edit waited for simulated fixture-owner approval; registered tests and independent review passed. Repeated under systemd NoNewPrivileges with absolute Codex path |
| Worker crash | Physical subprocess with fixture JSONRPC was killed; task blocked, stored IDs/workspace preserved, restart did not replay |
| Concurrent execution | Two fixture project workers used distinct clones/admin storage; third claim rejected by durable global cap |
| Actual manager crash | Dedicated service MainPID killed; systemd restarted it; private HTTPS recovered and all ten registry identities persisted; SQLite integrity/foreign keys passed |
| Actual backup restore | Daily local service created integrity-checked backup; restored into fresh disposable path; integrity/foreign keys and ten-project count passed |
| Private deployment | HTTPS8787 responds through Tailscale; correct-Origin invalid login returned401; service/backup timer enabled/active; existing443/3000 untouched; no Funnel configured |

## Remaining gates

- Actual full VM reboot acceptance is **not performed**: unrelated sessions are active. User linger=yes and enabled services are verified configuration, not evidence of reboot survival.
- Storage is **not production-ready for large projects**: 99% full, approximately445MB free. Clone footprint/128MB reserve guards and backup reserve fail closed. No unrelated cleanup performed.
- Initial real owner account remains deliberately unset pending privately chosen credentials.
- Dependency installation, merge/push/deploy and other unsupported sensitive actions remain unavailable. Their acceptance cannot be inferred from UI policy controls.

First real orchestration review blocked because milestone audit evidence was absent from its input. The input was fixed to include persisted evidence; the verification gate was not weakened. The first disposable systemd coding check blocked because Codex was absent from default PATH; installation now persists the detected absolute binary path.

## 2026-10-10 checkpoint rerun (no feature/service changes)

Existing31tests passed in7.56s, Ruff and JavaScript syntax passed. Live read-only integrity/foreign-key checks passed; 50/50memory exports matched, tenprojects/twoblockedtasks persisted, production history/thread/worker/owner stores confirmed empty. All four earlier backups passed integrity/foreign-key checks. Fresh278528byte draft checkpoint snapshot restored into an unused offline path with all20 application tables row-for-row equal; canonical final snapshot follows Git receipt. No live manager restart, reboot, new model/browser turn or deletion performed. Existing TestClient/httpx deprecation warning is the only test warning. Actual new-boot acceptance remains unperformed.

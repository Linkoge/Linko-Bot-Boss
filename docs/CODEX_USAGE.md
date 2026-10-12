# Codex account allowance display — 2026-10-12

Implemented in the isolated standalone checkout `/home/codex/checkouts/linko-codex-usage-20261012`.
Activation is pending the deployment helper's first external bootstrap. Full evidence and exact handoff:
`/home/codex/.local/share/linko-bot-manager/deployments/codex-usage-20261012/REPORT.md`.
Prepared release ID: `codex-account-usage-v1-20261012`. Do not overwrite the original helper release.

The candidate descends from the reviewed helper candidate e120c4d and includes the live combined
startup/To-Do/Markdown release 7999d1a. It preserves both deliveries. The original helper assignment
89a0b3a118004443936df2dea272ed5a had finished at inspection; the helper is independently running,
but its cooperative manager integration is not live. This worker is inside the manager cgroup;
no safe self-activation is possible. No live source, service or release request was changed.

Two 44px square controls align at the right of the existing two navigation rows. The lower control
shows rounded remaining five-hour and weekly allowances in horizontal halves. Each half maps by
actual window duration (300/10080 minutes), not primary/secondary position. Unavailable, ambiguous
or absent expected windows show a dash. The account dashboard shows exact reported used and derived
remaining percentages, actual window duration/label, reset UTC timestamp and Tbilisi time, time remaining,
last reading/refresh attempt and stale/error status. Other reported buckets/windows remain visible there.
The square summarizes only the Codex account bucket, never a model-specific quota substituted as Codex.

Existing ChatGPT-authenticated App Server supplies metadata via account/rateLimits/read with
excludeResetCreditDetails. No credentials are copied/read, no API key/provider is added, and no thread
or inference turn is started to obtain usage. A coalesced metadata request is allowed at most once per
60 seconds per process; hidden pages do not poll. Existing rate-limit notifications update the cache.
Account changes invalidate old readings, including initialization notifications. Cached readings become
stale after 180 seconds or a passed reset; an expired reset never invents restored allowance. Failed
refresh retains marked-stale previous readings. No prior reading or invalid numbers remain unavailable.
Remaining is max(0, 100-used), with exact used preserved even above 100; percentage precision is preserved
in details, while the compact face rounds. A reset without data is unavailable, not a fabricated countdown.

Authenticated API/CSP and existing API client patterns are retained. Native modal semantics, explicit
accessible label, Tab/Shift+Tab containment, Escape, touch, outside dismissal and focus restoration are
validated. Project selection/drafts, scrolling, zero badges, To-Do and Markdown are preserved. Navigation
is 98px before safe-area padding at tested widths. Physical Android/iOS keyboard behavior is unverified.
No dependency/schema change, remote push, unrelated work or production inference acceptance is claimed.

Primary protocol reference: [official OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server),
account/rateLimits/read and account/rateLimits/updated. The installed codex-cli 0.162.1 JSON schema and
an authenticated live read verify the actual usedPercent/windowDurationMins/resetsAt fields. The returned
account reported 300-minute and 10080-minute windows; these are observations, not assumed universal plans.

See docs/DEPLOYMENT_HELPER.md for the external idle/bootstrap/backup/health/rollback procedure.
Use the exact combined usage release ID above in place of the original helper release ID only while
live HEAD remains 7999d1a and the final manifest matches. If the original helper is activated first,
reprepare this candidate against the new live base under a new release ID; never bypass baseline checks.

# Integrated activation status — 2026-10-13

Current integrated activation target: `integrated-recovery-append-popup-v1-20261013`; see [STATE.md](../STATE.md).
The owner has authorized combined deployment. Manager review and controlled `deploy_release` remain;
the worker does not deploy or restart. Exact manifest establishes preparation; only a matching later
succeeded receipt establishes activation. Original source reports and candidate IDs below retain their
historical scope and evidence; their prior requirement for a new owner request has been satisfied.
Full integration report: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md`.

# Codex account usage and project popup contract — 2026-10-13

Current candidate: `popup-cleanup-v1-20261013`, isolated checkout
`/home/codex/checkouts/linko-popup-cleanup-20261013`. Implemented and browser-verified;
preparation is established only by its exact helper `manifest.json`. **Do not deploy.**
Full report and evidence: `/home/codex/.local/share/linko-bot-manager/deployments/popup-cleanup-20261013/REPORT.md`.
The existing live navigation/audio baseline `nav-audio-upload-v2-20261013`, commit
`08f0ba712f194ea0f985740ef408c9e033511b98`, has succeeded receipt
`deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json` under the private data directory.
Older usage implementation/deployment narrative remains in Git history.

The popup displays exactly two stacked sections: **5 hour**, then **Weekly**. Each shows
exact percentage remaining and hours/minutes until reset below it, in the same font styling.
Missing, ambiguous or unsupported windows show `Unavailable`. There is no allowance label,
plan/bucket description, used percentage, absolute reset date or reset/remaining prose.
Close/Refresh remain accessible. The bottom **Last refresh** footer retains the prior
system font, muted color, 12px size and 12px vertical margins.

`last_successful_refresh_at` is separate from `last_attempt_at`. An accepted metadata read
or notification containing a usable Codex 300/10080-minute percentage window establishes
successful freshness at observation time. Cache reads, failed attempts, invalid/no-window
responses and updates to unrelated buckets do not advance it. Account invalidation resets it.
Before any usable successful data, the footer says `Last refresh Never`; sections remain
unavailable. Backend or browser failures retain the last successful time and mark retained
percentages stale with subdued warning color/dotted underline, title and accessible text.
Age >=180 seconds or a passed reset also marks stale; no restored quota is fabricated.
An accepted empty response may clear readings while retaining the truthful historical success time.

The account button and details both map only the Codex account bucket by actual window duration,
never slot order or a model-specific substitute. Remaining is max(0,100-used); the square
rounds while details preserve precision. Fetching, account scope, metadata-only RPC,
excludeResetCreditDetails, notifications, request coalescing, 60-second cache throttle,
hidden-page behavior, session fences and account invalidation remain intact. Authentication,
CSRF/CSP, modal semantics, live announcements, focus trap, Escape/backdrop dismissal and
focus restoration remain intact. No inference or credential inspection is used for quota data.

## Project/repository picker layering

The correct menu is `#project-menu` opened by **Choose project**, including selection and
**Rename selected project…**. Its panel background was already fully opaque `#171d29`.
The apparent transparency came from the later positioned `.workspace-row` painting above
the absolutely positioned menu with automatic z-index. Underlying tabs/buttons could also
receive input. The menu now has explicit local z-index 2; while open its sticky navigation
context uses z-index 65 (matching the chat utilities) above global notices. The opaque
surface, existing anchored positioning, scroll height/overflow, rounded border/shadow,
selection/rename handlers, keyboard controls and outside dismissal are retained. The
menu is outside the independently scrolling tab strips; it is not clipped by them.
The 2×2 navigation controls and dark red theme are preserved.

## Validation and activation limits

Backend tests cover first failure, success, cache hits, later failure and recovery, usable
window validation, notifications, account invalidation and generation rejection. Responsive
Chromium checks cover 1440×1000, 393×851, 320×420 and 393×420: lean exact content,
modal keyboard/touch, success/failure freshness, opaque surface and actual hit testing,
menu selection/rename/outside dismissal, menu scrolling and sticky document scrolling.
Screenshots include the reproduced old stacking defect, corrected open/scrolled menus,
lean usage, stale failure and never-refreshed placeholders. Physical-device native UI and
screen-reader sessions are not claimed. The fixed preparation CLI runs the existing full
regression gate; its hashed log and exact source/assets bundle establish readiness.

This separate delivery does not pause or append to assignments
`aa7832895c764317b1e87517e13d3090` or `0fda950d113946a794d9afe71daf8194`.
No main checkout edit, merge, restart, deployment request or production task/data mutation.
If another delivery advances live HEAD, coordinate/rebase and prepare a fresh ID against
that exact baseline; never deploy this old-base candidate over unrelated changes.

## Reset duration revision — 2026-10-13

Candidate `usage-countdown-manual-scroll-v1-20261013` preserves section labels and successful-refresh
semantics. Durations use the server-adjusted clock and ceil positive seconds to whole minutes
(never show zero before expiry), split into total hours and minutes with singular/plural units.
Expired timestamps show `0 hours 0 minutes` and the percentage stays stale; missing/nonfinite
timestamps show `Unavailable`. The existing 15-second display tick and visibility refresh keep
the countdown current without changing the successful-refresh footer. No quota reset is inferred.
This revision supersedes the absolute-reset presentation above and is prepared, not deployed.

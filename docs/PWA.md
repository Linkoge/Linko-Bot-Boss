# Installable Linko Bot Manager

Live release: `pwa-standalone-v1-20261013`. Deployment succeeded; receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/09f882af21fb28ab5870da74fee1c3b6.json`,
commit `3bb554b7efa26d8fae695c5340e8a12366d75339`. All 20 served loopback aliases matched.
The later bundled To-Do/chat candidate preserves this cache/launch/update contract and is not deployed.
Physical Android install/update acceptance remains pending.
Candidate: `/home/codex/checkouts/linko-pwa-20261013`.
Full evidence and manager facts:
`/home/codex/.local/share/linko-bot-manager/deployments/pwa-20261013/REPORT.md`
and sibling `manager-summary.json`. Exact immutable preparation manifest is
`deployment-helper/releases/pwa-standalone-v1-20261013/manifest.json` under that runtime root.

## Launch and hosting contract

The existing HTTPS origin is `https://codex-server.tail5ebb9d.ts.net:8787/`.
Manifest identity, start URL and scope are `/`, matching this root deployment.
Display is `standalone`; no fullscreen override, immersive API, orientation lock,
or native Android build is introduced. Theme/background stay #10141e. Supplied
koi/binary PNGs are 192/512px, plus a separate padded 512px maskable icon; provenance
and exact geometry are in the static icons README. Manifest and mobile metadata
are public shell resources; every existing API/authentication boundary remains.
Android Chrome may install via its menu. Chrome's menu installation does not
require a fetch service worker: https://developer.chrome.com/blog/update-install-criteria .
Automatic promotion varies by browser/version/engagement and is not guaranteed.

## Update contract

There was no service worker or registration in the verified base, and none is
added. Offline operation is not provided. `/` returns the existing HTML with only
JS/CSS resource references replaced by `/static/assets/<full-SHA256>/<original-name>`.
Each process pins exact HTML and asset bytes at startup. Only valid matching
versioned JS/CSS URLs receive `public, max-age=31536000, immutable`. Unknown hashes
return 404, never different bytes under a previously issued immutable URL.
All mutable aliases, HTML (including `/static/index.html`), manifest, icons and
API responses retain `no-store`. Existing asset URLs remain available.

Next online navigation/launch/reload retrieves current HTML and changed content
gets a new URL. Installed users do not reinstall for code updates. A still-open
or OS-resumed existing page must refresh; there is no forced reload that could
interrupt recording, drafts or work. An old page never requests lazily imported
assets: existing scripts load together on navigation. Browser installation icon/
manifest metadata update schedules remain browser-controlled.

Only application response configuration changes. No Tailscale/TLS/proxy/service
unit/helper installation/network configuration, authentication, schema, dependencies,
CSS/JS behavior, storage keys or API request paths change. The existing deployment
helper and exact-release workflow remain. Preparation now also runs the PWA browser
check via xvfb-run, a validation-host utility already present, not a runtime dependency.

## Acceptance and remaining device checks

Tests cover manifest/MIME/PNG dimensions, hash-to-byte matching, immutable versus
mutable cache boundaries, wrong versions, unchanged auth, and next-release URLs.
The browser check uses disposable loopback data, Chromium manifest/installability
inspection, actual image decoding and mask-safe pixels, authentication/project-draft restoration after reselecting the project/local
storage reload and real desktop Chromium app mode. Existing fixed release checks
exercise composer, audio, files, projects, recovery, models, controls and deployment.
See the full report for actual outcomes. Localhost is a browser-trusted secure
context; it is not candidate HTTPS/device validation.

After a separately requested deployment succeeds, verify the served candidate
manifest/asset headers through the existing private HTTPS listener, then on an
Android device with trusted Tailscale HTTPS access install from Chrome, launch from
its icon, confirm no Chrome address bar and visible normal Android status/navigation
bars, and exercise login, keyboard/composer, recording/upload, navigation and draft
state. Confirm a later prepared update appears on online refresh without reinstall.
Android/WebAPK packaging, system bars, physical keyboard/safe areas, OS suspension,
and device install/update behavior are pending physical-device acceptance. Never
claim those passed from desktop app mode or an implementation-only result.

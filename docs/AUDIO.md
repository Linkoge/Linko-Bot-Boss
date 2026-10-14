# Approved recording row and parallel Send — 2026-10-14

[Composer contract](chat-history.md) supersedes previous layout descriptions: + / Record / Send right
stack; recording timer–Upload–Discard bottom-left; ⇈ immediately left of ordinary Send when relevant.
All retained capture/transcription/error phases keep necessary controls visible independently of focus.
Both sends finish/transcribe through the established whole-file flow; ⇈ adds one per-request permission
only to the accepted original recording submission. Context/auth/history changes, visibility interruption,
late discarded results, editing during transcription, replacement/picker recovery and pending retries retain
existing safe behavior. A failed or interrupted transcription leaves a draft/recoverable recording, never
persistent permission. Upload replacement remains edit-only and provider-decided arbitrary audio intake.
Full evidence/status: [STATE.md](../STATE.md). Historical release headings below retain earlier scope.

# Recording composer / direct Send — bundled candidate, 2026-10-13

Release `todo-handoff-chat-trash-recording-agents-v1-20261013`; full evidence: `/home/codex/.local/share/linko-bot-manager/deployments/todo-chat-20261013/REPORT.md`. Not deployed.

Starting/active/stopping/transcribing recording expands the composer and exposes Send regardless of
textarea focus or mobile keyboard detection. Existing bounded geometry/overflow, typed caret detection,
Record toggle, general attachments, audio Upload replacement, Download/Discard and recovery remain.
Send is active only during capture in its own visible project/context without pending submission/retry
or attachment upload. First click changes phase synchronously; later clicks cannot stop/submit twice.

Reuse the established whole-Blob STT pipeline: explicit recording Send finishes capture, transcribes,
checks original conversation, appends once to the current typed draft, and directly submits that merged
text with current draft attachments and the recording's original mode through ordinary request replay/FIFO.
Incoming manager work permits queued Send; recording never interrupts the manager. Concurrent typing
while capturing is included; editing during stopping/transcription cancels automatic Send and leaves text
for review. Project/view/auth/visibility changes, changed history, capture interruption, limits, Discard,
transcription failure, pending retry and uploads suppress automatic submission. Retry after STT failure
is edit-only, not an implicit Send. Late discarded results/checkpoints cannot resurrect or send a session.
Transcript/typed draft/attachments remain available on rejected/ambiguous submission under existing rules.
Existing synthetic duration and Chromium capture checks do not establish paid STT, physical permission UI,
Android keyboard/suspension or installed-device acceptance.

# Microphone icon and general attachment separation — 2026-10-13

See [chat attachment contract](chat-history.md) for integrated prepared release,
unrestricted + uploads during recording and retained original microphone asset.
Audio-specific Upload remains provider-decided whole-file transcription; it is distinct
from general attachments. No transcription format/routing or capture lifecycle changes.

# Audio format pass-through — candidate, 2026-10-13

Exact integrated candidate `audio-format-pass-through-usage-v1-20261013` in
`/home/codex/checkouts/linko-audio-pass-through-20261013`. Preparation is established
only by `deployment-helper/releases/audio-format-pass-through-usage-v1-20261013/manifest.json` under private data.
**Deployment pending a new owner request; no activation or restart performed.**
Full report and manager routine brief/recent-delivery facts: [/home/codex/.local/share/linko-bot-manager/deployments/audio-pass-through-20261013/REPORT.md](/home/codex/.local/share/linko-bot-manager/deployments/audio-pass-through-20261013/REPORT.md)
and sibling `manager-summary.json`. This section supersedes historical format
restrictions and older pending/live statements below.

The audio picker has no accept filter. Any nonempty file within 32 MiB replaces the
originating microphone session and goes through whole-file transcription to its draft.
Extension/MIME allowlists, MIME-from-extension normalization and signature matching
are removed. Keep the original File/bytes, original basename/extension and supplied
MIME including parameters. Absent MIME uses neutral application/octet-stream; neither
this fallback nor forwarding claims that the provider supports a format. Filename is
URI-encoded in X-Audio-Filename, decoded and made safe for multipart headers on the
server. No transcoding or invented known audio extension for uploads. Retry, Download
and IndexedDB checkpoints retain the selected filename.

Duration probing remains bounded and content-based. Readable clips above 1200 seconds
plus the existing one-second encoder tolerance are rejected for duration; unreadable,
missing-audio/unknown metadata and probe timeout proceed to the transcription API with
null duration. Decoder limitations must not act as format gating. Browser recording
20-minute timer and 32 MiB limits, auth/CSRF/origin, empty-file checks, upload deadline,
single provider admission, cancellation and provider timeout remain. API mime_types
is retained solely as a browser encoder-selection hint, never an upload allowlist.

xAI HTTP 400/415/422 yields a sanitized provider rejection with status and a cautious
format-or-contents explanation. Provider response bodies/credentials are never echoed.
Other existing safe credential/quota/network/invalid-transcript handling remains.
Errors retain the file for Retry/Download and never auto-send. Picker cancellation,
context fencing, replacement invalidation/stopped tracks, stale callback suppression,
sequential draft append, attachments and explicit Send remain intact.

This integrated candidate descends from usage task `7e2b8eb083164eb293103cf9f39a57c9`
prepared commit `1d0b7cec236782115579345c8d470dba81b29dc5`; its lowercase countdown
and tests/docs are retained. No edits to its checkout, task state or live source.
Live base remains succeeded `usage-countdown-manual-scroll-v1-20261013`, commit
`c9e8c9854ce90de7b945062a64cebe9b3d0d17b4`, proven by receipt
`deployment-helper/results/37bea66eea3274f2dfaddb58a993ff74.json` and matching served
assets. Reprepare if that live base advances before owner-requested activation.
Validation uses real Chromium capture and disposable backend/provider fixtures;
physical-device picker and paid-provider acceptance are not claimed. Exact verification
and preparation outcomes belong to the report/manifest/log.

---

# Integrated activation status — 2026-10-13

Current integrated activation target: `integrated-recovery-append-popup-v1-20261013`; see [STATE.md](../STATE.md).
The owner has authorized combined deployment. Manager review and controlled `deploy_release` remain;
the worker does not deploy or restart. Exact manifest establishes preparation; only a matching later
succeeded receipt establishes activation. Original source reports and candidate IDs below retain their
historical scope and evidence; their prior requirement for a new owner request has been satisfied.
Full integration report: `/home/codex/.local/share/linko-bot-manager/deployments/integrated-deliveries-20261013/REPORT.md`.

# Recording toggle candidate — 2026-10-13

Exact candidate `unfinished-recovery-family-ui-v2-20261013` removes **only the separate recording Stop button**.
Record remains the stop/transcribe toggle; Discard, Upload, Download/Retry, explicit Send and the
existing recording replacement/cancellation/checkpoint/selection/focus/20-minute behavior remain.
No recording/picker action sends an owner message, interrupts the manager or queues a manager wake.
Chromium uses real microphone capture and fixture STT to verify toggle/removal, focus, picker cancellation,
replacement bytes, stopped tracks, stale callbacks, sequential append, retained attachments and narrow
navigation geometry. It tracks zero manager-interrupt requests during all audio flows. No physical device
or paid STT acceptance is claimed. Historical interruption evidence and its limits:
[automatic recovery](AUTOMATIC_RECOVERY.md). Full report: `/home/codex/.local/share/linko-bot-manager/deployments/unfinished-recovery-20261013/REPORT.md`.

**Not deployed; wait for a new owner deployment request.** Preparation requires the exact helper manifest.
Actual live is `nav-audio-upload-v2-20261013`, `08f0ba712f194ea0f985740ef408c9e033511b98`,
proven by succeeded receipt `/home/codex/.local/share/linko-bot-manager/deployment-helper/results/0b09a6f825a34572dd8bcf5773f149b1.json` and matching served assets. Thus the navigation/upload release
below did activate; its old pending statements are historical. Its behavior is retained except for this
candidate's redundant Stop removal. Fully manual scrolling and 2×2 navigation remain.

---

# Navigation symmetry and recording Upload — 2026-10-13

Candidate release `nav-audio-upload-v2-20261013` in `/home/codex/checkouts/linko-nav-audio-upload-20261013`.
Implemented and Chromium-verified; preparation is established only by the exact manifest at
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/nav-audio-upload-v2-20261013/manifest.json`.
**Do not deploy:** this objective authorizes preparation only. Full evidence: [/home/codex/.local/share/linko-bot-manager/deployments/nav-audio-upload-20261013/REPORT.md](/home/codex/.local/share/linko-bot-manager/deployments/nav-audio-upload-20261013/REPORT.md).

Recording displays Stop, Discard and Upload together. Upload synchronously opens a single-file
native picker for WebM, Ogg, MP4/M4A or WAV through the existing whole-file `/api/audio/transcribe`
workflow. This is transcription into the original draft, not a chat attachment or automatic Send.
32 MiB/20-minute server validation, recoverable errors, Download/Retry and sequential append remain.
MIME is normalized from a supported browser type or supported filename extension; the server
still validates the actual container, audio stream and duration before calling the provider.

Opening/cancelling the picker or an empty change leaves capture intact. A nonempty selection
invalidates the exact originating microphone session before stopping its recorder/tracks and
clearing its serialized checkpoint. Late chunks/Stop/track events and STT replies cannot affect
its replacement. Selected files get a fresh session ID and never inherit automatic Send.
Unsupported/empty/oversized selected files discard the prior recording and show a notice;
a supported but unreadable file retains existing recoverable transcription-error behavior.
Context/session changes while the picker is open reject the selection rather than discarding
another recording or writing into a replacement chat. Normal browser suspension/interruption
recovery still applies while any native picker is open; continuous OS capture is not guaranteed.

Upload uses the existing pointer focus protection and context-bound picker-return restoration.
Draft text, caret selection on cancellation, attachments and manual chat scrolling are preserved.
The 44px Settings gear is now beside the project dropdown in the upper row; chat utilities and
usage occupy matching columns below. Project/view tabs remain independently scrollable.
Chromium covers real microphone capture, native picker activation, cancellation, raw-file
replacement, stopped tracks, queued stale callbacks, delayed transcript fencing, attachments,
sequential append and 1440/393/320px layouts. Existing browser/backend gates cover model settings,
manual scroll, recovery and security. STT/native turns use fixtures; physical device picker/
keyboard/OS suspension and paid transcription are not claimed. Detailed outcomes/logs live in
the full report and fixed preparation validation log.

Current live baseline is `model-settings-manual-scroll-v2-20261013`, commit
`0745c1a6af91f09d62f414c0c63326d2afe82260`, established by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/c9a21304d22c36785e25b66f914ec90b.json`.
Older candidate/live assertions below are historical and superseded by that receipt.

---

# Mobile focus and recording controls — 2026-10-12, candidate only

Exact candidate release: `chat-ui-mobile-composer-v4-20261012`; isolated checkout
`/home/codex/checkouts/linko-mobile-composer-20261012`. Implemented and browser-verified;
prepared for later owner-requested activation. **No deployment is requested or performed.**
The deployed baseline is `chat-ui-composer-v3-20261012`, commit
`5b31f3fc554a16a21ba6f138d975e0af4d8ce748`, established by succeeded receipt
`/home/codex/.local/share/linko-bot-manager/deployment-helper/results/ae463def00a557e8cebafbf1c7536fa0.json`.
Earlier prepared-only statements for that baseline below are historical and superseded.
Detailed evidence and limitations: [full private report](/home/codex/.local/share/linko-bot-manager/deployments/mobile-composer-20261012/REPORT.md).


Record/Stop/Discard pointer presses preserve an already focused draft by preventing default
button focus transfer before click. Send remains explicit and may collapse the keyboard.
No new focus call, recording lifecycle or STT invocation occurs on Record pointerdown. The
click handler retains full capture/transcript/queue semantics; keyboard buttons remain native.
Stop/Discard are now right aligned beside the existing vertical column, 44px tall and at
least 64px wide, with accessible labels. Timer/status, Download and interruption recovery
remain inside the 154px composer. Stop reply sits at its upper corner so busy inference does
not cover recording controls. Upload focus/picker return behavior is documented in
[chat-history.md](chat-history.md). Physical Android open-keyboard behavior remains pending
owner/device verification; browser focus preservation is verified without claiming OS proof.

Sequential append, distinct session IDs, stale-result protection, checkpoint serialization,
recovered draft/audio, Stop versus Send and the 20-minute/32 MiB limits are preserved.
Expanded real Chromium mouse/touch checks plus the existing audio/queue/backend suites are
retained in the exact release validation log. Capture is real; STT/native inference use
fixtures. No paid provider call or production mutation/restart was performed.

---

# Compact recording lifecycle candidate — prepared only, 2026-10-12

Combined release: `chat-ui-composer-v3-20261012`, preserving original chat cleanup and
succeeded queue composer. Full [report](/home/codex/.local/share/linko-bot-manager/deployments/compact-composer-20261012/REPORT.md) and current chat-history section
are authoritative for this continuation; historical candidate statuses below are superseded.

Each recording has a distinct ID. Stop adds its full-file transcript to the latest originating
draft, separated by a paragraph (or existing final newline). Existing typed/transcribed text
is retained. Only one STT attempt per session is active; retries reuse a returned transcript
on text-limit errors. A session identity check after every asynchronous response suppresses
discarded/stale results; originating conversation verification rejects replaced chats. A durable
per-tab applied-ID set is saved atomically with drafts to avoid duplicate insertion after recovery.
Context switches stop into the original draft without sending. Edits after recording Send
cancel implicit submission. Explicit Send still captures original mode and queues while busy.

Browser visibility alone no longer stops recording. It requests a checkpoint and allows
MediaRecorder to continue; background status states capture may pause. Track mute/end,
recorder errors/unexpected Stop, lifecycle freeze/pagehide, and a >5-second scheduling gap
produce interruption/recovery state rather than claiming continuous capture. Recovery never
automatically sends. Retry transcribes available captured audio; Download/Discard stay available.
The 20-minute/32 MiB limits and full final Blob remain. Native Enter/newline behavior is unchanged.

Unfinished audio checkpoints use IndexedDB (per tab key retained in sessionStorage); drafts
and applied transcript IDs use sessionStorage. Checkpoints are serialized/coalesced every two
seconds while data arrives, and finalized on Stop. Discard/success remove saved audio in the
same serialized queue. Reload in the same tab can recover checkpoints and drafts. Storage
failure shows a notice; browser eviction, closing the tab/session, OS kill before a checkpoint,
private-mode policy or missing encoder data can still lose audio/drafts. Previously captured
bytes can also be undecodable after abrupt termination; server validation reports this honestly.
Other newly opened tabs do not automatically claim the original tab's recovery key. Audio
remains local to the browser until explicit Stop/Send/Retry submits to STT.

For long mobile capture, keep the page open and the device awake when possible; stop/save
before leaving. No background service or guaranteed background recording is added. Android
screen lock may delay `dataavailable`, and browsers can suspend microphone capture:
[MDN MediaRecorder dataavailable](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event).
Native photos use the locally generated Codex protocol's `localImage` variant; contract reference:
[OpenAI App Server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md).

Real Chromium capture plus controlled provider/network/lifecycle fixtures verify safe sequential
append, recovery after reload, visibility continuing, suspension recovery, stale result suppression,
attachments and compact layouts. Physical Android/iOS, paid provider/native image inference and
an actual OS suspension were not run. Exact verification belongs to the release log/full report.
Deployment is not requested and remains pending an owner activation request.

---

# Composer audio — 20-minute candidate prepared; activation pending, 2026-10-12

Current deployed state (unchanged by this task): `audio-v2-livebase-20261012` and the same-code key-loading release
`audio-key-load-v1-20261012` both have durable **succeeded** helper receipts. Live code is
`d87be8124ab9d7c2de325982061dcac3467994e6`. The private key was installed in the existing server
EnvironmentFile; no key value is included here. The earlier absent-key/pending-activation handoffs
are historical. One real public 11-second whole-file xAI sample passed; this does not establish long
clip performance or physical device acceptance. No additional provider call was made for this investigation.

Authoritative deployment receipts:
- `/home/codex/.local/share/linko-bot-manager/deployment-helper/results/683598e8554c73dca96c263840e5ca20.json`
  — audio activation succeeded, commit above, authentication/data/assets health verified.
- `/home/codex/.local/share/linko-bot-manager/deployment-helper/results/a7b93842f9ba53999c81260fde185568.json`
  — key-loading release succeeded, same commit, service PID 84907, start 2026-10-12 14:38:29 +04.

Retained preparation/provider evidence:
- `/home/codex/.local/share/linko-bot-manager/deployments/audio-20261012/REPORT.md`
- `/home/codex/.local/share/linko-bot-manager/deployments/audio-reprepare-20261012/REPORT.md`
- `/home/codex/.local/share/linko-bot-manager/deployments/audio-key-20261012/REPORT.md`
- `/home/codex/.local/share/linko-bot-manager/deployments/audio-key-20261012/provider-check.json`

These reports describe their earlier observed state; the succeeded receipts supersede their pending
activation text. The failed `audio-v1-20261012` attempt was a stale-live-base rejection before cutover.

## Behavior

The microphone sits immediately left of Send. During recording it becomes **Stop**, and Send ends
capture and requests transcription followed by the existing text submission flow. Stop inserts the
transcript for editing. Typed text is retained, with a separating space where needed. Recording,
permission, finishing, transcription and recoverable errors occupy one temporary compact row.
Discard cancels capture/upload and suppresses late responses; Download explicitly saves retained audio.
Enter does not send during any audio operation. Context switches, leaving Chat, hiding the tab,
changed conversations, editing during transcription, pending requests suppress
automatic sending. Project switches stop recording and save the transcript to the original project's
session draft; they cannot write to another project's field. Send uses the captured project, conversation
and mode, with the existing durable request ID and ambiguity/reconciliation behavior. Retries after
transcription failure insert for editing rather than silently retrying the original automatic Send.

Audio and an already recognized transcript remain in this tab after recoverable failures. The transcript
is cached when draft length prevents insertion, allowing editing then retry without another provider call.
An expired login stops recording/suppresses Send and retains a stopped recording for retry after login.
Recordings are not persisted on the server or in browser storage. A page reload/close loses retained audio;
a before-unload prompt helps on supporting browsers, and Download permits explicit recovery. Browser
mobile lifecycle/OS termination can still lose in-memory audio. Existing project drafts remain session-only;
ambiguous text submissions continue using existing sessionStorage. Nothing sends untranscribed audio as chat.

## Actual provider contract and server settings

Official documentation checked 2026-10-12:

- [Speech-to-text guide](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text)
- [REST reference](https://docs.x.ai/developers/rest-api-reference/inference/speech-to-text)
- [Model information](https://docs.x.ai/developers/models/speech-to-text)

xAI supports file transcription with authenticated `POST https://api.x.ai/v1/stt`, multipart `model`
then `file` (last). Its documented default is `grok-voice-transcribe-2.0`; WebM/Opus browser files use
batch upload, not raw-packet streaming. MP4/M4A, Ogg and WAV are supported by this application too.
This is an audio-only integration. Codex model routing, inference transport and authentication are untouched.
No substitute provider or invented OpenAI-compatible transcription path is used.

Use the existing private systemd `EnvironmentFile` at `/home/codex/.config/linko-bot-manager.env` for
`XAI_API_KEY`. Keep this file readable only by its owner; enter the key there outside chat, Git, SQLite,
logs and browser settings. Do not paste a key into an owner conversation. The key is read server-side
at process startup, excluded from Settings repr, and never returned by either audio API. Reloading an
updated key requires a controlled deployment/restart at the normal safe release boundary.
No secret file contents were read or changed by this implementation.

Optional environment settings:

- `AI_MANAGER_AUDIO_PROVIDER=grok` (other names fail explicitly; no fallback).
- `AI_MANAGER_AUDIO_MODEL=grok-voice-transcribe-2.0`.
- `AI_MANAGER_AUDIO_PROBE=ffprobe` (or absolute executable path).

`ffprobe` is required to verify real container/packet duration before transmitting audio. The host already
has `/usr/bin/ffprobe`; no package installation was performed. The small `transcribe(data, mime)` adapter
can be replaced later with an explicitly configured provider. No runtime Python dependency/schema change.
`GET /api/audio` reports readiness and limits without a key; it is authenticated. Missing provider/key/probe
configuration fails compactly on microphone activation before requesting device permission.

## Integrated nonblocking recording candidate

The current integrated release is `queue-composer-v1-20261012`, prepared only, not activated.
It preserves the 20-minute/composer candidate below and internal-result fix. Recording and explicit
whole-file audio Send are available during manager inference; recorder/transcription/upload/Send,
HTTP admission and ambiguous-retry locks remain. See [chat-history.md](chat-history.md) for FIFO,
context/mode binding, validation and private evidence references. Older statements that active
inference suppresses recording/Send describe the deployed historical behavior.

## Prepared 20-minute recording change

Owner request: “Limit should be 20 minutes. And don't show that limit in a chat ui, just make it
20 minutes.” This supersedes the investigated 180-second app policy. Candidate checkout:
`/home/codex/checkouts/linko-audio-20m-20261012`. Exact controlled release ID:
`audio-composer-v3-20261012`. **Not activated; no deployment request or succeeded receipt for this release.**
Manager must only request this exact ID through its controlled `deploy_release` capability after owner
requests activation. Do not activate with shell or restart the manager from a worker.

The browser uses authenticated API `max_seconds=1200`, stops automatically at the boundary,
assembles all chunks including the final encoder chunk, uploads the entire file once and inserts
editable text. Boundary Stop never invokes chat Send. Existing explicit Stop/Send, drafts, cached
transcript retry, durable chat request retries and project/context guards remain unchanged.
The normal microphone tooltip is “Record microphone”; recording status shows elapsed `MM:SS` only, beginning immediately at `00:00` and incrementing each second.
The textarea prompt, bottom-left project/context label and saved-conversation composer note are removed.
Normal recording has no project prefix or “Recording” wording; Stop, Send and Discard remain visible.
Conversation messages, project selection and actionable errors remain intact.
There is no duration denominator, countdown or normal limit disclosure. Errors remain actionable.

The integrated v3 candidate reuses completed assignment `4c8eab2e6b554fb7b757d5bb84cf2bb0`
and its existing 20-minute implementation; no second worker or duplicated implementation was launched.
Read-only inspection found lifecycle completed with no lease before refinement. The previous v2
prepared receipt remains immutable historical evidence and is superseded for activation by v3.

### Candidate boundaries and supporting resource choices

- Real server duration must be at most 1201 seconds: retain the existing one-second final encoder
  packet tolerance; forged browser timing cannot bypass actual ffprobe validation.
- Upload limit 32 MiB, one upload/probe/provider operation per process. At requested 64 kbps,
  1200 seconds is 9.6 MB before overhead, exceeding the former 8 MiB. The new cap also has room
  for approximately 192 kbps (28.8 MB) if a compressed browser encoder ignores the bitrate hint.
  Browser capture keeps WebM/Opus, Ogg/Opus and MP4/AAC; removes uncompressed WAV fallback because
  48 kHz mono PCM would exceed 115 MB at 20 minutes. Backend WAV validation remains available
  within the same bounds. Bitrate requests are hints, not browser guarantees; excessive final size
  retains the full blob for Download/Discard instead of truncating it.
- ffprobe metadata 8 MiB (formerly 2): a 20-minute Opus file has about 60,000 20 ms packets.
  Real long Opus fixtures pass this cap. Keep pipe-only protocols, 16 MiB individual allocation,
  MIME/magic/audio checks. Probe deadline 30 s (formerly 10).
- Upload deadline 120 s (formerly 30): allows a roughly 9.6 MB clip at about 0.65 Mbps, plus
  transport overhead. Very slow connections can still fail recoverably; no unlimited upload.
- Provider socket timeout 300 s (formerly 60), response cap remains 1 MiB. Browser whole
  upload/transcription abort 480 s (formerly 90): 120 upload + 30 probe + 300 provider + 30 slack.
  Socket timeout is an I/O bound, not a strict total provider wall-clock guarantee. Cancellation
  still holds admission until the provider thread exits, avoiding overlapping billable work.
  These are finite operational choices, not a provider long-clip latency guarantee.
- Transcript, textarea and chat MessageBody intake now consistently allow 64,000 characters
  (formerly 24,000). A 20-minute fast dictation at 300 words/minute with six characters/word
  is about 36,000 characters; the old limit was insufficient. Existing merged-draft overflow
  error/cached transcript remains; shortening and retrying never duplicates STT. No schema,
  Codex route, task-details limit or inference transport change.
- Live proxy inspected read-only: Tailscale Serve private HTTPS :8787 proxies directly to
  loopback Uvicorn :8787. No nginx or configured Serve upload/request duration cap on this path.
  Uvicorn entrypoint has no request-duration timeout; keep-alive timeout concerns idle connections.
  No proxy/systemd/listener change is necessary based on observed configuration. Exact installed
  Tailscale 1.102.4 source was unavailable at its reported commit; older official Serve source has
  no response-header timeout, but that is supporting context, not proof of installed behavior.
  A long candidate upload through production HTTPS was not performed before activation.

Official [xAI STT guide](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text)
rechecked 2026-10-12: 500 MB whole-file allowance; options precede final file part. It provides no
20-minute latency SLA. No paid STT call or secret read was made for this change. Prior 11-second real
provider success remains the only real provider evidence; physical Android/iOS and long provider
latency remain unverified. Browser/OS memory/lifecycle behavior can still lose tab-held audio.

### Validation and durable handoff

26 targeted backend checks passed: real synthetic duration 1200 and 1201 accepted; 1201.01 and
1202 rejected with 413. Generated 1200-second WebM/Opus, Ogg/Opus and fragmented MP4/AAC files,
each larger than 8 MiB, passed real probing and authenticated whole-file intake. Fixture provider
received exact original bytes; no chat messages or retained server audio were created. Upload
deadline failure releases admission; provider errors/keys remain sanitized. Exact 64,000-character
provider text passes chat intake; oversized provider output fails safely.

Actual Chromium capture with fixture-only STT passed at 1440/393/320 px. Accelerated 19:59 remains
recording with no upload, then 20:00 stops, sends exactly the final Blob, appends editable text and
makes no extra chat submission. Tooltip/elapsed status have no normal limit text. Existing Stop,
explicit Send, project switching, retry, discard, draft overflow/cache, permission handling and
idempotent chat recovery checks pass. This exercises the real timer and recorder, not a 20-minute
physical recording or paid provider call.

Sanitized detailed report and targeted logs:
`/home/codex/.local/share/linko-bot-manager/deployments/audio-20m-20261012/REPORT.md`.
Prepared manifest/bundle/fixed-check log:
`/home/codex/.local/share/linko-bot-manager/deployment-helper/releases/audio-composer-v3-20261012/`.
The preparation manifest records exact live base, candidate commit, asset and validation hashes.
Preparation runs all repository backend/lint/syntax/browser release checks including deployed
memory/fetch behavior. Only a future **succeeded** helper result establishes activation.

## Historical investigation: exact limit, origin and adjustment options

The section below preserves task `2b32890ed14e42b6aea43091c195b3a7` evidence about the deployed
180-second baseline. Its options are historical proposals; the candidate above implements the
owner's subsequent 20-minute request. Its “no code change” statements apply to that investigation.

Scope: investigation only. No audio code, limit, credential, service unit, production database or
release request was changed. Applicable repository/ancestor AGENTS.md: none found.

### Frontend and backend behavior

- `ai_manager/audio.py:13-14` defines `MAX_BYTES = 8 * 1024 * 1024` and `MAX_SECONDS = 180`.
  `Audio.status()` (lines 139-150), exposed through authenticated `GET /api/audio`
  (`ai_manager/web.py:358-364`), sends both limits to the browser. They are constants, not settings.
- `ai_manager/static/audio.js:14,63` hardcodes the tooltip “Record up to three minutes” and timer
  denominator `/ 3:00`. Actual stopping uses API `config.max_seconds`, not that displayed string.
- Capture starts with requested 64,000 bits/s and 250 ms chunk delivery (lines 49-59). Every 250 ms,
  elapsed time is floored to seconds; at `elapsed >= 180`, it calls `finishAudio(false)` (60-65).
  This is a browser timer, not sample-exact trimming: scheduling/encoder tail may run slightly over.
- `finishAudio(false)` (73-77) clears the send flag, stops MediaRecorder and releases tracks.
  `onstop` (52-57) assembles the entire Blob and automatically transcribes it. On success,
  `appendTranscript` and the send guard (78-106) append editable text to the originating draft;
  no chat submission occurs. This is **automatic Stop + transcription**, not cancel/discard or Send.
  An expired authentication context instead retains the recording for login/retry.
- Manual Stop uses the same path (113-117). Clicking Send during capture calls `finishAudio(true)`
  (119), then sends recognized text only if the original context/admission guards still hold (100-105).
  Discard (29-33) aborts capture/upload and ignores late results. Error/retry (108-110) clears Send;
  retained recording can be retried/downloaded/discarded. Reload/OS termination can lose that Blob.
- Size events stop at `bytes >= max_bytes` (50); final encoded chunk can overshoot. Blob larger than
  8 MiB is rejected locally before uploading (93), retained for Download/Discard, not truncated.
  Exactly 8 MiB passes the size check, subject to other validation.
- Server streaming intake (audio.py:152-179) rejects bytes exceeding 8 MiB with HTTP 413 before xAI.
  It validates MIME, file magic, audio-stream presence and actual ffprobe duration (26-81), taking
  the larger container or packet-span duration. Duration **greater than 181 seconds** returns 413;
  the extra second is explicitly commented as allowance for a final browser encoder packet.
  A forged client duration/config or removed browser timer cannot bypass server enforcement.

### Why 180 seconds?

Git introduces this constant in `9d8478bbb946f53e38134fc008fac0c4f5ce39ca`,
“Add bounded Grok microphone transcription with draft-safe Stop and Send” (2026-10-12).
The module describes bounded, transient transcription; the original report records the paired
8 MiB/three-minute bounds and resource deadlines. The original task's description/acceptance was
inspected selectively for limit/duration/timeout terms: no numerical duration requirement or rationale
was found. Neither report, commit message nor relevant source explains why exactly 180 rather than
300/600 seconds. A conservative initial resource/interaction bound is an **inference from the design**,
not a demonstrated provider requirement, owner request, performance benchmark or cost threshold.

### Provider limits versus local constraints

Official xAI STT guide checked 2026-10-12:
https://docs.x.ai/developers/model-capabilities/audio/speech-to-text
Request Body and Limits document **500 MB maximum file size** for whole-file REST STT. The examined
STT guide, REST reference and model page do not document a three-minute maximum recording duration
or a REST request-duration timeout. Do not interpret this as a guarantee of unlimited duration or
borrow a timeout from xAI text-to-speech/custom voices. REST endpoint is `POST /v1/stt`; model is
`grok-voice-transcribe-2.0`; multipart options precede the final file. Model page lists WebM support:
https://docs.x.ai/developers/models/speech-to-text
REST reference: https://docs.x.ai/developers/rest-api-reference/inference/speech-to-text
Streaming STT exists, but requires a separate integration with raw frames/backend proxy; the current
MediaRecorder WebM/Ogg container workflow uploads only after capture ends.

Local practical bounds are verified in source, not provider limits:
- Upload 30 s total (`audio.py:164`), probe 10 s (`:58`), metadata 2 MiB (`:53-54`),
  individual ffprobe allocation 16 MiB (`:36`), provider socket timeout 60 s (`:110`),
  provider response 1 MiB (`:111-113`), nonempty transcript at most 24,000 characters (`:117`).
- Browser transcription abort after 90 s (`audio.js:89`); this covers the HTTP upload/transcription
  wait after recording ends. Provider 60 s is a socket timeout, not a 60-second audio-duration cap
  or guaranteed total wall-clock deadline. No frontend recording-time network transcription happens.
- One upload/probe/provider operation per app process (`audio.py:159-161`). A cancelled request
  retains the admission slot until the non-cancellable provider HTTP thread ends (`:89-96`).
- Whole-file capture and multiple in-memory server/multipart copies mean larger clips consume more
  memory and postpone first transcript. No measured long-clip latency, memory budget or reverse-proxy
  size/timeout guarantee was established in this investigation.
- Requested 64 kbps implies about 1.44 MB audio payload at 3 min, 2.4 MB at 5 min, 4.8 MB at 10 min,
  excluding overhead. This is arithmetic, not measured output or a bitrate guarantee; codecs/browsers
  may differ. At that rate 8 MiB is roughly 17.5 min before overhead. Other formats can reach it sooner.
- Existing composer maxLength (24,000) also applies to typed draft plus transcript (`audio.js:82`);
  cached recognized text is retained when insertion would exceed it.

### Feasible options (proposed, not implemented)

1. Keep current safeguards and dictate in successive clips, appending each transcript before sending.
2. Raise cap to 5 or 10 minutes in a reviewed release: change server MAX_SECONDS and make tooltip,
   timer and error wording reflect the same limit. Current API already drives actual browser stopping.
   Keep/reassess 8 MiB, deadlines, probe metadata and text length; verify target browser/container,
   boundary behavior, upload/transcription latency and memory with longer recordings. Provider sample
   acceptance at 11 s does not prove 5/10-minute readiness.
3. Add a validated server duration configuration setting and render it dynamically. No existing
   environment variable, UI setting or provider/model/probe override changes this constant.
4. Remove the duration cap while retaining a byte cap: feasible code work, but uploads can still stop
   at 8 MiB and fail existing probe/time/response/text constraints. Truly long dictation merits chunked
   or streaming transcription, timeout/cancellation/backpressure work and draft recovery. It is a
   larger change; “unlimited” would not accurately describe the current or proposed bounded design.

### Investigation verification and routine manager handoff

Verified 2026-10-12: live service active/running, PID 84907, configured working directory this repo;
HEAD matches both succeeded receipts. Served loopback `/static/audio.js` SHA256 exactly matches
source and receipt: `734a0de64f6e41f1beb9d5159cd7c2f2bdd50edc851947175b2bdd2fd8086ee2`.
17 existing audio backend tests passed (one existing Starlette/httpx deprecation warning).
Fresh real ffprobe synthetic WAV checks: 180 and 181 s accepted; 181.01 and 182 s rejected, HTTP 413.
Fresh `scripts/browser_audio.py` passed with real Chromium capture and **fixture-only** STT:
11 uploads, 3 chat HTTP submissions, 1440/393/320 px viewports. Its accelerated three-minute-boundary
check adjusts session start time and asserts no additional Send; it is not a physical 180-second run.
No paid provider call, credential read or live API upload was needed. Served-asset GET is read-only.

Manager routine brief/recent-delivery facts: investigation completed; deployed default is 180 s,
server allowance up to 181 s, 8 MiB, automatic Stop/transcribe/edit without Send; hardcoded app policy,
no duration override or documented exact numerical rationale; xAI file allowance 500 MB. Docs corrected
historical pending-audio/key claims using the exact succeeded receipts above. Proposed increases remain
unimplemented/unverified/undeployed; no new activation is requested. Preserve existing working service.
Detailed authoritative report is this section; secret-free check artifacts retained in
`/home/codex/.local/share/linko-bot-manager/deployments/audio-limit-investigation-20261012/`.

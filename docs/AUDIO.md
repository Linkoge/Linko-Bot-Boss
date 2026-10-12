# Composer audio — prepared, activation pending

The audio candidate is isolated in `/home/codex/checkouts/linko-audio-20261012`, based on the tested
memory/fetch-warning combined candidate `7f79fc0`. Assignment `fd654ea170084822942f84b1adc1a59a`.
Full evidence: `/home/codex/.local/share/linko-bot-manager/deployments/audio-20261012/REPORT.md`.
Owner authorization now includes audio activation. The earlier audio-v1-20261012 request failed
before cutover with `Live release changed; reprepare` after the memory/fetch release succeeded.
The audio candidate already descends directly from the exact current live memory/fetch baseline
`7f79fc082eb3d4e01964bc50aefbc67d41a146f3`; no source rebase or merge is required.
Fresh release preparation uses that live baseline and preserves both deployed changes.

Replacement release: `audio-v2-livebase-20261012`. Repreparation evidence:
`/home/codex/.local/share/linko-bot-manager/deployments/audio-reprepare-20261012/REPORT.md`.
The manager may deploy the prepared replacement through its existing `deploy_release` path under
the owner's continuing authorization. This worker must not restart the manager itself.

### Required credential setup

The running service and this worker have no nonempty `XAI_API_KEY`. The configured optional systemd
EnvironmentFile `/home/codex/.config/linko-bot-manager.env` does not exist. These are presence-only
checks: no value was output, copied, logged or placed in source. There is no browser/SQLite API-key
setting. Use the existing server environment mechanism, as the same `codex` account, in a private
SSH terminal. These commands create the file only if absent and preserve existing content:

```bash
umask 077
touch /home/codex/.config/linko-bot-manager.env
chmod 600 /home/codex/.config/linko-bot-manager.env
nano /home/codex/.config/linko-bot-manager.env
```

In the editor add one line `XAI_API_KEY=YOUR_XAI_KEY`, replacing the placeholder with the actual
STT-enabled xAI key. Enter it only inside the editor, never as a shell command or in this chat.
If that variable already exists, edit its line rather than adding duplicate definitions. Preserve
any other environment settings. No `daemon-reload` is needed for an existing EnvironmentFile;
the helper's approved deployment starts the process and loads the file. Prefer setting the key
before the manager requests this replacement release. If adding it after activation, use a later
controlled idle restart/release so the running process loads it; do not stop an active worker.

The UI/backend can activate before the key is configured. The microphone is then visible, but clicking
it shows the compact missing-key message before requesting microphone access. Typed chat and Codex
continue normally. This is explicitly not live transcription readiness. After deployment with a key,
check authenticated `/api/audio` readiness and a short spoken recording for Stop/edit and Send.
No live xAI transcription has been verified by this worker.

## Behavior

The microphone sits immediately left of Send. During recording it becomes **Stop**, and Send ends
capture and requests transcription followed by the existing text submission flow. Stop inserts the
transcript for editing. Typed text is retained, with a separating space where needed. Recording,
permission, finishing, transcription and recoverable errors occupy one temporary compact row.
Discard cancels capture/upload and suppresses late responses; Download explicitly saves retained audio.
Enter does not send during any audio operation. Context switches, leaving Chat, hiding the tab,
changed conversations, editing during transcription, pending requests or active inference suppress
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
updated key requires a separately authorized audio activation/restart at the normal safe release boundary.
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

## Boundaries and limits

Authenticated, CSRF/origin/host-protected `POST /api/audio/transcribe` accepts a raw bounded binary request
from the browser and constructs the provider multipart body on the server. Limit: 8 MiB and three minutes
(with one second of encoder-tail tolerance), one upload/probe/transcription in flight per application process.
Upload deadline 30 seconds; probe deadline 10 seconds, metadata bounded to 2 MiB, pipe-only protocols and
16 MiB maximum individual probe allocation; provider socket timeout 60 seconds and response capped at 1 MiB;
browser aborts at 90 seconds and retains the blob. Network cancellation does not release provider admission
until its HTTP thread finishes. Provider errors/response bodies are sanitized, never logged or returned.
Transcript must be nonempty text of at most 24,000 characters. Multipart redirects are refused so a redirect
cannot forward the bearer key. No arbitrary audio URL, filename, provider URL or browser key is accepted.

MIME, magic bytes, actual audio-stream presence and packet/container duration are checked before the provider.
Browser chunks are encoded at a requested 64 kbps, with size/duration automatic Stop, never automatic Send.
Unsupported media APIs and insecure origins give a dismissible notice on activation, not a permanent warning.
Mobile browsers require private HTTPS; loopback HTTP is a browser-allowed secure context for local tests.
Chrome recording, server probe and layout are checked with real Chromium fake-device MediaRecorder input.
Generated MP4/AAC, Ogg/Opus and WebM/Opus exercise the real server validator. Physical Android/iOS/Safari,
OS permission sheets, mobile screen lock and hardware microphone quality remain unverified.

No app-owned persistent audio retention; xAI receives the file on explicit Stop/Send/retry. This does not
assert a third-party provider retention policy. No live xAI transcription was performed: this worker has
no `XAI_API_KEY`, and a production credential was not read. Server-key configuration, a real spoken-audio
provider acceptance check and activation through the already authorized manager helper remain pending.

# Composer microphone assets

Owner-selected source: https://www.svgrepo.com/svg/438679/microphone .
Retrieved 2026-10-13 from https://www.svgrepo.com/show/438679/microphone.svg
(the download endpoint returned HTTP 504). Original bytes are retained in
microphone-svgrepo-438679.original.svg; SHA-256 d6d765774dea12d1956f01e66e7af93d0488950a26ae993b03c41c840f352a9b.
SVG Repo lists uploader SVG Repo, collection SVG Vector, license CC0.
License reference: https://www.svgrepo.com/page/licensing/#PD and
https://creativecommons.org/publicdomain/zero/1.0/ . No upstream author beyond
that listing is asserted. Source contains one path, no scripts or external references.

microphone.svg preserves the exact viewBox and path, adapts fill to currentColor,
removes fixed 800px dimensions and adds decorative accessibility attributes.
Its trusted markup is embedded in index.html and cloned by audio.js; CSS gives
it a centered 24px square within the shared 44px control. Recording square/Retry
and accessible button names remain state-specific.

Original realistic icon was the platform-rendered U+1F399 emoji, not a bitmap/SVG
asset. Its exact reusable markup is retained in microphone-realistic.html.
To restore, replace the normal index.html SVG with that span and keep the existing
clone behavior in audio.js; remove/adapt microphone-icon sizing if desired.
Platform emoji rendering remains platform-dependent, just as before.
These authored assets are unrelated to untrusted user attachment rendering.


# PWA koi/binary artwork

Owner-supplied PNG (decoded as image data only), confirmed by owner/manager:
`/home/codex/.local/share/linko-bot-manager/chat-workspace/attachments/ca10952521084c92b838edf8c44eb754.png`.
Source SHA256: `ab2f52d61920f6425e6e34c13409fbb5b580a7d44ea755ea86ac8ab580448a58`;
512×512. No upstream license or creator is inferred.

Chromium Canvas2D decodes the original, fills an opaque #10141e square and draws
it with high-quality smoothing. pwa-192.png and pwa-512.png contain the full
artwork at their stated dimensions. pwa-maskable-512.png centers the full artwork
in a 280×280 square (116px padding); even its corners fit within the maskable
safe circle of radius 204.8px. No cropping/redesign/generative alteration.
All three are opaque PNGs. Browser checks decode pixels and ensure the maskable
safe-circle exterior is entirely the theme background. Private generation recipe
and validation evidence: deployments/pwa-20261013 under the existing runtime data root.


# Usage provider marks

The expanded usage cards embed trusted SVG markup directly from the repository.
OpenAI reuses the knot mark already bundled as `PROVIDER_LOGOS.codex` in
`model-settings.js`. Claude uses a small radial burst drawn as an inline path in
`usage.js`, in muted terracotta. These decorative marks sit next to visible provider
names, inherit local styling, and contain no scripts, image URLs or external references.

# Fresh-machine installation and recovery

Run these steps in a terminal **as the Linux user that will run the service**, not
as root. Each user has separate provider logins, systemd units and application data.
Commands below use placeholder paths and hostnames: replace `/home/USER`,
`NODE_VERSION`, `HOSTNAME` and other uppercase placeholders before running them.
Never put API keys or login files in the checkout. Keep repository and home paths
free of spaces (the unit installers require this).

## 1. Install prerequisites and clone

On Linux Mint / Ubuntu / Debian, install Git, host Python and audio tools:

```bash
sudo apt update
sudo apt install git python3 python3-venv ffmpeg
```

Install `uv` using your trusted package source or the upstream installer at
[docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/).
`uv sync` supplies the Python >=3.12 environment required by `pyproject.toml`;
the independent repair launcher also needs the host's `python3`.
Install the Codex CLI and, to use Claude, Claude Code using their provider's
installation instructions. If Codex is installed through npm/nvm, install and
select a Node version first. Keep that version installed: systemd does not source
`.bashrc`, initialize nvm, or follow changes to your shell's default Node version.

Verify in this user's terminal:

```bash
command -v git python3 uv ffmpeg ffprobe node codex claude
codex --version
claude --version
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/Linkoge/Linko-Bot-Boss.git
cd Linko-Bot-Boss
uv sync --locked
uv run playwright install chromium
```

Chromium is used for manager screenshots and browser tests. If Playwright reports
missing OS libraries, install the packages it lists using your normal administrator
account. Authenticate Git access first if the repository is private.

## 2. Create the service environment

The main unit reads `~/.config/linko-bot-manager.env`. Its `EnvironmentFile=-…`
prefix makes a missing file **silently optional** to systemd, so create it yourself.
Use `command -v codex`, `command -v node` and `command -v claude` to find absolute
paths. A working shell PATH does not prove the service can find these programs.

```bash
mkdir -p ~/.config
(umask 077; touch ~/.config/linko-bot-manager.env)
chmod 600 ~/.config/linko-bot-manager.env
${EDITOR:-nano} ~/.config/linko-bot-manager.env
```

Example contents (placeholders only; replace them, including the API key):

```ini
AI_MANAGER_CLAUDE=/home/USER/.local/bin/claude
AI_MANAGER_CODEX=/home/USER/.nvm/versions/node/NODE_VERSION/bin/codex
PATH=/home/USER/.nvm/versions/node/NODE_VERSION/bin:/home/USER/.local/bin:/usr/local/bin:/usr/bin:/bin
XAI_API_KEY=REPLACE_WITH_YOUR_XAI_KEY
AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,HOSTNAME
AI_MANAGER_DATA=/home/USER/.local/share/linko-bot-manager
AI_MANAGER_PORT=8787
AI_MANAGER_PROJECT_ROOTS=/home/USER/projects
AI_MANAGER_SECURE_COOKIE=1
```

For a standalone Codex installation, replace its path and omit the nvm directory
if it needs no Node runtime. For npm/nvm Codex, **both codex and node must be
reachable with this PATH**. Keep `~/.local/bin` as an absolute path in PATH so
Claude and uv are available. After a Node upgrade, update the versioned paths here
and restart the services.

Use plain `KEY=VALUE`, one assignment per line, absolute paths, no `export`, `$HOME`,
`~`, shell substitutions or references to other variables: systemd does not expand
them. Quote a whole value if it contains spaces. Doctor accepts this format and
backslash line continuations; it reports unsupported multiline quoting as an env
file error. The file should belong to the service user, mode 600. Never paste it
into logs, issue reports or chat.

Existing optional configuration (no need to set these unless used):

| Variable | Purpose / default |
| --- | --- |
| `AI_MANAGER_CLAUDE_MODELS` | Comma-separated Claude picker IDs; default `claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5` |
| `AI_MANAGER_PUBLIC_HOSTS` | Additional accepted public hostnames; empty by default |
| `AI_MANAGER_TRUSTED_PROXIES` | Trusted direct proxy IPs/CIDRs; default `127.0.0.1,::1`; scope to your proxy |
| `AI_MANAGER_SECURE_COOKIE` | `1` for HTTPS; `0` only for local HTTP development |
| `AI_MANAGER_SESSION_SECONDS` | Owner session lifetime; default `2592000` |
| `AI_MANAGER_AUDIO_PROVIDER` | Audio transcription provider; default `grok` |
| `AI_MANAGER_AUDIO_MODEL` | Default `grok-voice-transcribe-2.0` |
| `AI_MANAGER_AUDIO_PROBE` | ffprobe executable; default `ffprobe` |
| `AI_MANAGER_GITHUB_TOKEN_FILE` | Optional Personal GitHub token file, outside repositories |
| `AI_MANAGER_GITHUB_COMPANY_TOKEN_FILE` | Optional Company GitHub token file, outside repositories |
| `AI_MANAGER_GITHUB_SYNC_SECONDS` | GitHub update polling interval; default `300` |
| `AI_MANAGER_RESTRICTED` | `1` for a restricted extra instance; default `0` |
| `AI_MANAGER_PROJECT` | Existing fixed project directory, required in restricted mode |
| `AI_MANAGER_REPO` | Live-checkout override used by tests; leave unset on an ordinary install |

`AI_MANAGER_DATA` defaults to `~/.local/share/linko-bot-manager`, port to `8787`,
project roots to `~/projects` (multiple roots separated by `:`). Allowed hosts must
include the actual hostname used in the browser, without scheme or port. Retain
`localhost` for deploy and doctor health probes. For external access configure
HTTPS, DNS and a proxy/tunnel separately; see [public access](../README.md#public-access-through-a-tunnel).
A phone microphone requires HTTPS; localhost HTTP works for local development.
Audio needs a real `XAI_API_KEY` and ffmpeg/ffprobe on the server.

## 3. Log in to providers and create the owner

Log in as this same Linux service user, with its normal HOME. Provider accounts
belong to that user; root's or another user's login will not authenticate the service.
Load only the env file you just wrote and trust (sourcing a shell file executes it):

```bash
set -a
. ~/.config/linko-bot-manager.env
set +a
codex login
claude auth login
uv run python -m ai_manager.cli setup-owner
```

Use the installed provider's interactive login command if its CLI version differs.
`setup-owner` prompts for the private application's username and a password of at
least 12 characters; web signup is disabled. Load the same `AI_MANAGER_DATA` for
setup and service startup, or you will create a login in the wrong database.
Provider login is separate from this owner login. Doctor checks executable startup,
not provider authorization, billing or a full model request.

## 4. Install and start the user service

From the checkout, with that environment loaded:

```bash
python3 scripts/install_service.py --hostname HOSTNAME
loginctl enable-linger "$USER"
systemctl --user enable --now linko-bot-manager.service
systemctl --user status linko-bot-manager.service
```

The installer writes `~/.config/systemd/user/linko-bot-manager.service` from
`deploy/linko-bot-manager.service.in`, using the checkout's `.venv/bin/python`,
working directory and detected Codex path. It also enables the daily backup timer.
Supplying `--hostname` avoids requiring Tailscale auto-detection. If a unit already
differs, review it before using the installer's `--replace-manager-unit` option.
Linger keeps the user manager alive after logout and starts it at boot; if local
policy requires admin authorization, have the administrator enable it for this user.
No system-wide root application service is required.

The app listens on `127.0.0.1:8787` by default. Open it through your configured
HTTPS proxy/tunnel. For local HTTP only, set `AI_MANAGER_SECURE_COOKIE=0` and restart.
If you edit a unit, run `systemctl --user daemon-reload` before restart. Editing
an env file only needs a service restart. Inspect failures using:

```bash
journalctl --user -u linko-bot-manager.service -n 80 --no-pager
```

Review journal output for sensitive information before sharing it.

## 5. Install the independent host repair agent

Sign in to the app and create/select a chat for repair results. Open
`/api/chat-history` on the app's origin while signed in; its `conversations` array
contains chat `title` and `id`. For a project chat use
`/api/chat-history?project_id=PROJECT_ID`. Copy the chosen existing chat's exact `id`;
it is not shown in the normal chat URL.

From the checkout, as the same user, with the main env loaded:

```bash
python3 scripts/install_repair.py install --conversation CHAT_ID --auto-detect
python3 scripts/install_repair.py status
```

Omit `--auto-detect` for owner requests only; with it the independent timer can
request repair after a prolonged outage. The installer snapshots the launcher
outside the checkout into `DATA/repair/launcher.py`, installs the repair user timer,
and tries enabling linger. Confirm installed/enabled/active and the conversation
in `status`. Re-run `install` after updates to refresh the snapshot; subsequent
runs can omit `--conversation`. Use `--provider claude` for Claude repair.

This host agent runs as the user and **has no root privileges**. Passwordless sudo
is intentionally off; do not enable it as an installation workaround. Administrator
operations need an administrator. In particular, VM access should use the libvirt
and kvm groups, not unrestricted sudo. It cannot repair a dead host, broken storage,
systemd itself or unavailable provider authorization. See
[Emergency host repair](../README.md#emergency-host-repair) for runtime behavior and
manual recovery commands. Restricted instances must not install their own repair agent.

## 6. Optional extra instances

For another instance of the same checkout, each instance needs a separate data
directory, port, env file and owner account. For another Linux user, perform
provider login, linger and service setup as that user too.

1. Copy `deploy/instance.env.example` to `~/.config/linko-bot-manager-NAME.env`,
   chmod 600 it, and set its project, data directory, free port and hostnames.
   Add the provider paths, PATH and audio settings from step 2. Restricted mode
   requires an existing project outside the app's code and data directories.
2. Load that env file using `set -a`, dot-source, `set +a`, then run
   `uv run python -m ai_manager.cli setup-owner` for its own database.
3. Run `python3 scripts/install_service.py --instance NAME --start`.
4. Return to a fresh terminal, load the **main** env file, then run
   `python3 scripts/install_deploy.py --instance NAME` (repeat for every instance).
   It registers each instance for coordinated restart and health verification.

Back up each instance database with its environment loaded using
`scripts/backup_daily.py`. Restore only to a fresh path with the service stopped;
provider native thread storage and project files need separate backups.
[Multiple instances](../README.md#multiple-instances-and-the-restricted-work-instance)
describes restrictions and cross-user deploy control. Cross-user service commands
require explicit host setup; doctor never executes those command arrays. It checks
registered HTTP releases and warns to run doctor as the other service user for
that user's unit/environment checks.

## 7. Optional KVM/libvirt access

If this machine will host VMs, install your distribution's QEMU/KVM/libvirt tools.
Have an administrator grant this service user the narrowly scoped device/service
groups, for example on Mint/Ubuntu:

```bash
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients
sudo usermod -aG libvirt,kvm USER
```

Log out and back in to refresh group membership. A lingering systemd user manager
may retain its old groups until reboot; reboot before testing VM access from the
service. Check `id`, `/dev/kvm` access and `virsh -c qemu:///system list` as the
service user. Group membership is not root access and does not require passwordless sudo.

## 8. Verify and update

From the installed checkout, as the service user:

```bash
uv run python -m ai_manager.cli doctor
```

**After a reboot, run doctor.** It prints PASS/WARN/FAIL and a one-line fix for each
failed check, never key values. Any FAIL returns nonzero. It makes no configuration,
database, service or host changes; subprocess and HTTP probes have three-second
timeouts. It checks the service environment rather than trusting your interactive
shell, including differences between the saved env file and the running process.
Codex `--version` checks executable/runtime startup without making a model request.
The service's `/api/auth-status` checks scheduler health and its startup
`release_commit` against this checkout's HEAD. Instances are discovered from local
env files, systemd template units and the main deploy registry. Doctor does not
prove provider login or that your browser hostname/DNS/proxy works; test those by
signing in and sending a request through each provider you intend to use.

For coordinated updates, install the deploy mechanism once from the main environment:

```bash
python3 scripts/install_deploy.py
```

Register all extra instances using `--instance NAME`. The installed deploy path
runs tests, backs up, restarts and verifies every registered instance, and rolls
back failures. See [Deploy](../README.md#deploy). An owner-triggered manual deploy
uses `python3 scripts/deploy.py --now`.

For a manual Git update, wait for work to finish first, then:

```bash
git pull --ff-only
uv sync --locked
systemctl --user restart linko-bot-manager.service
# Repeat for EVERY extra instance of this checkout:
systemctl --user restart linko-bot-manager@NAME.service
python3 scripts/install_repair.py install
uv run python -m ai_manager.cli doctor
```

Pulling code alone does not update a running Python process. A second instance
(such as `kaxa`) can keep serving the old commit until restarted. Repeat provider
path checks when nvm versions change. The deploy path is preferred for coordinated
checks/backups/rollback; a manual pull and restart does not provide those safeguards.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| No env file, unset provider/audio settings | Main unit's optional `EnvironmentFile=-…` silently ignored a missing file | Create `~/.config/linko-bot-manager.env`, owner-only mode 600; fill it and restart |
| Claude missing in model picker | Service cannot resolve Claude executable | Set absolute `AI_MANAGER_CLAUDE`, include `~/.local/bin` as an absolute path in PATH, log in as service user; restart |
| Codex “App Server transport lost” under systemd | npm/nvm Codex starts without node on the service PATH | Include installed nvm version's bin directory in PATH; verify node and codex paths; restart |
| Provider executable works but requests fail | Wrong Linux user's login, expired authorization or provider limit | Authenticate provider as service user; inspect provider's actual error; doctor startup is not a login check |
| Audio unavailable | Missing `XAI_API_KEY` or ffprobe/ffmpeg | Put key in protected service env, install ffmpeg, restart; use HTTPS on phones |
| Invalid host / request rejected | Browser hostname absent from allowed hosts | Add it to `AI_MANAGER_ALLOWED_HOSTS` without scheme/port; retain localhost; restart |
| Login doesn't stick over local HTTP | Secure cookies require HTTPS | Use HTTPS, or `AI_MANAGER_SECURE_COOKIE=0` for local development only |
| Changes after pull do not appear | Running process still has old imported code | Sync dependencies and restart; use doctor to compare startup release with HEAD |
| Another instance (e.g. kaxa) stays old | Instance omitted from restart/deploy registry | Register it with `install_deploy.py --instance NAME`, deploy or restart it; rerun doctor |
| Service/repair stops after logout or reboot | Linger not enabled, or unit/timer not enabled | `loginctl enable-linger "$USER"`; enable main service and repair timer |
| Repair missing / inactive | Independent launcher/timer not installed | Load main env and run `install_repair.py install --conversation CHAT_ID`; confirm status |
| Repair cannot sudo / VM access denied | Agent has no root; user's groups not refreshed | Use administrator for privileged setup; grant libvirt,kvm membership for VMs and reboot; keep passwordless sudo off |
| Doctor reports env permissions or stale environment | Env file is exposed or edits were not loaded | Set owner and chmod 600, restart affected service, rerun doctor |

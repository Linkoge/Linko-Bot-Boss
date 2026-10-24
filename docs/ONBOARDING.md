# Onboarding a restricted user

The goal: fill three fields in the owner UI, paste one line on the new person's Windows PC, click Apply. This page is the
complete guide: where everything lives, the one-time setup, the daily flow, the command-line fallback, troubleshooting and
removal. The instance it creates is the existing restricted work instance
([INSTALL.md section 6](INSTALL.md#6-optional-extra-instances),
[README](../README.md#multiple-instances-and-the-restricted-work-instance)).

```
owner UI "Add user"  ──plan/apply──▶  spool  ──▶  user path unit ──sudo──▶  root-owned launcher (copy of provision.py)
        ▲                                                                       │ Linux user, instance, ACLs,
        │ device ID, hostname, IP  ◀── one report, one-time token ◀── Windows ◀─┘ Cloudflare, Syncthing
        └──── receipt, initial login (shown once)                      PC (one pasted line, over the tailnet)
```

## 1. Where everything lives

| What | Where | Owner / mode |
| --- | --- | --- |
| Add User screen, onboarding records (name, email, state, receipt) | project menu → **Add user**; table `onboarding` in the manager's `manager.sqlite3` | manager user |
| Credentials (all API keys) | `~/.config/linko-onboarding.env` of the manager user (`AI_MANAGER_ONBOARDING_CONFIG` overrides) | manager user, `chmod 600`, never in git |
| Management SSH key (public part is embedded in the PC command) | `~/.ssh/id_ed25519.pub` of the manager user (`LINKO_MANAGEMENT_PUBKEY_FILE` overrides) | manager user |
| Spool between the web app and the root helper | `<data>/onboarding/requests/`, `results/`, `installed.json` | manager user, `0700` |
| Root helper (copy of `ai_manager/provision.py`) | `/usr/local/lib/linko/onboarding-launcher.py` | root, `0755` |
| Root helper configuration | `/etc/linko/onboarding.json` (service user, data dir, repo, env file) | root, `0644` |
| Sudoers rule | `/etc/sudoers.d/linko-onboarding` | root, `0440` |
| Helper units | `~/.config/systemd/user/linko-bot-manager-onboarding.{path,service}` | manager user |
| Windows script (single source; also embedded in the PC command) | `deploy/windows/Linko-Setup.ps1` | repository |
| New user's instance | Linux user `<name>`: `/home/<name>/Documents/<name>` (the shared folder), `~/.config/linko-bot-manager-<name>.env`, `~/.local/share/linko-bot-manager-<name>/` (database, `initial-login.txt` until read), system unit `/etc/systemd/system/linko-bot-manager-<name>.service` | `<name>` |
| Deploy registration | `instances` list in `<data>/deploy/installed.json` | manager user |
| CLI fallback | `python -m ai_manager.provision add-user` | root |

## 2. One-time setup on the management server

### 2.1 Packages and prerequisites

- The manager already runs, deploys are installed (`python3 scripts/install_deploy.py`), and the root-owned launcher
  mechanism is available (`sudo`, `visudo`). Install ACL tools: `sudo apt install acl`.
- The manager is reachable **on the tailnet** (this is how the new PC reports in; the public Cloudflare hostname is
  deliberately refused for that route). With Tailscale Serve:
  `tailscale serve --bg --https=443 http://127.0.0.1:8787`, and make sure the tailnet name is in
  `AI_MANAGER_ALLOWED_HOSTS` (the manager's own env file).
- The Syncthing instance on the management server is running (GUI/API on `http://127.0.0.1:8384`) and is the same
  one that shares the manager's other folders.
- A management SSH key without passphrase: `ssh-keygen -t ed25519` as the manager user. Only the **public** key is ever
  copied to the PC.

### 2.2 Credentials file

Create `~/.config/linko-onboarding.env` (`chmod 600`, outside any git work tree, plain `KEY=VALUE`; environment variables
win over the file). The helper root process reads the same file for the Cloudflare and Syncthing keys.

| Key | Purpose |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Edit tunnel configuration, DNS and Access applications (below) |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_TUNNEL_ID` | The existing account, zone and tunnel |
| `LINKO_DOMAIN` | The zone's domain; each hostname becomes `<name>.<domain>` (the screen's *Domain* field defaults to it) |
| `SYNCTHING_API_KEY` | Local Syncthing API key |
| `SYNCTHING_URL` | Optional, default `http://127.0.0.1:8384` |
| `SYNCTHING_USER` | Optional: the Linux user the Syncthing daemon runs as (default: the manager user) |
| `TAILSCALE_API_KEY`, `TAILSCALE_TAILNET` | Mint single-use PC auth keys on demand (below). Without them the screen shows a field to paste one key |
| `TAILSCALE_TAG` | Optional, default `tag:linko-pc` |
| `LINKO_MANAGER_URL` | The manager's tailnet URL, e.g. `https://manager.tail1234.ts.net` (default: from `tailscale status --json`) |
| `LINKO_SYNCTHING_DEVICE_ID` | Optional: the management Syncthing's device ID (default: read through the API) |
| `LINKO_MANAGEMENT_PUBKEY_FILE` | Optional: another public key file |

**Cloudflare API token.** The tunnel must be *remotely managed* (its ingress rules live in the dashboard), because the helper
edits them through the API. In the Cloudflare dashboard: *My Profile → API Tokens → Create Token → Create Custom Token*.
Permissions: `Account · Cloudflare Tunnel · Edit`, `Account · Access: Apps and Policies · Edit`, `Zone · DNS · Edit`.
Resources: the one account and the one zone only. No expiry you cannot live with; no IP filter unless the server's IP is
fixed. Account ID and zone ID are on the zone's *Overview* page (right column); the tunnel ID is under *Zero Trust →
Networks → Tunnels* (or `cloudflared tunnel list`).

**Tailscale API key.** Admin console → *Settings → Keys → Generate access token* (valid up to 90 days; calendar the renewal).
`TAILSCALE_TAILNET` is the tailnet name shown under *Settings → General* (`-` means the key's own tailnet). Allow the tag
in the tailnet policy file, so keys can be created with it:

```json
"tagOwners": {"tag:linko-pc": ["autogroup:admin"]},
"grants": [
  {"src": ["tag:linko-pc"], "dst": ["<manager tailnet name or tag>"], "ip": ["443"]},
  {"src": ["<manager>"],    "dst": ["tag:linko-pc"], "ip": ["22", "22000"]}
]
```

The key written into the PC command is created per click: single use (`reusable: false`), preauthorized, tagged, expiring
after two hours. The API key itself never leaves the server.

**Syncthing API key.** Syncthing GUI → *Actions → Settings → GUI → API Key*.

### 2.3 Install the root helper (once)

The web service runs as the normal manager user with `NoNewPrivileges`, so it can never run `sudo`. Like the emergency
repair path, it only writes a request file; a small user unit starts a root-owned launcher through a sudoers rule that
allows exactly three commands (`tick`, `service stop <name>`, `service start <name>` with this data directory).

```bash
sudo python3 scripts/install_onboarding.py install     # root part (add --dry-run to look first)
python3 scripts/install_onboarding.py activate         # as the manager user: spool + path unit
python3 scripts/install_onboarding.py status           # check
```

Re-run `install` after updating `ai_manager/provision.py` (the root copy is a copy; `status` shows `launcher_current`).
The first request is the proof: the Add User screen's plan preview only appears when the helper answers.

## 3. Adding a user (the daily flow)

1. Project menu → **Add user** (main instance only; restricted instances do not have the screen, and their API refuses it).
2. Fill **Name** (`anna` → hostname `anna.<domain>`), **Email** (their Cloudflare Access login and app login) and
   **Windows folder**. *Advanced*: domain, memory limit. Click **Preview plan**. Nothing has been changed yet: the
   host helper only checks and lists every step as `planned`, `skipped` (already there) or `refused` (conflict).
3. Click **Generate PC command**. A single-use Tailscale auth key is minted (or paste one if no API key is configured), and
   one PowerShell line appears. Copy it. It expires in two hours and is shown only now.
4. On the new PC: open **PowerShell as Administrator**, paste, Enter. It installs Tailscale, OpenSSH (key only, reachable
   only from the tailnet), and Syncthing; trusts the manager's Syncthing device and pre-creates the share with the right
   folder ID, so no Syncthing click is needed; then reports hostname, Tailscale IP and Syncthing device ID **once**.
5. The screen fills in the device ID by itself. Click **Apply**, or tick *Apply automatically when the PC reports in*
   before that and it continues alone.
6. Progress shows step by step. At the end **Show initial login (once)** reveals email and the generated password. Hand
   them over; the screen cannot show them again. `initial-login.txt` is already deleted from the server. The person logs
   in to their AI provider (Codex/Claude) themselves; nothing is copied from the owner.
7. If a step fails, the receipt names it and the record is `failed`. Fix the cause and click **Retry**: it re-runs the same
   idempotent command, everything done is skipped.

States of a record: `pending` → `enrolling` (command generated) → `provisioning` → `done` or `failed` (Retry →
`provisioning`). The record, its latest plan or receipt and the PC's report survive reloads and restarts.

### What Apply does (each step checks first, then reconciles; existing things are never overwritten)

Linux user (no sudo, no password), project and data directories, `git init`, free localhost port, instance env file
(`AI_MANAGER_RESTRICTED=1`, no secrets), system unit with `MemoryMax`, the app login (random password), service enabled;
**ACLs**: the Syncthing daemon user gets read/write on the new folder only (plus search-only `--x` on its parent
directories), the new user gets default read/write on files Syncthing creates there, and the manager user gets access to the
instance's data directory (deploy snapshots and health checks); Cloudflare tunnel route, DNS CNAME and Access application
limited to the email; Syncthing device and folder (staggered versioning, `.git` ignored); and finally **deploy
registration**: the instance is added to `instances` in `<data>/deploy/installed.json` with `service_cmds` that stop and
start it through the launcher, so every deploy restarts and health-checks it. All checks run before any change: a hostname
that routes elsewhere, a non-restricted env file at the target or a Syncthing folder already using the path **refuses the
whole run** with no change.

### Security model

- The web app never runs as root and never starts a process for onboarding. The root part is a root-owned file; its
  request files are re-validated (name, email, folder, domain, memory, device ID) before anything runs, symlinks are not
  followed, and the sudoers rule names three exact commands.
- The only route reachable without a session is `POST /api/enroll/report`. It accepts one JSON object of four fields, only
  from tailnet addresses (never through the Cloudflare tunnel; it is `404` on restricted instances), only with a valid
  token, and only once. The token is random, single-use, valid two hours, stored as a hash, and replaced when you generate a
  new command. Wrong, expired and reused tokens look identical and are rate limited per source.
- The pasted line contains the script, a single-use preauthorized Tailscale auth key, the manager's public SSH key, the
  folder, the manager's tailnet URL and Syncthing device ID, and the one-time token. It never contains the Cloudflare,
  Syncthing or Tailscale API keys. It is not downloaded from anywhere.

## 4. Command-line fallback

Everything the screen does can be done by hand as root (a dry run unless `--apply`):

```bash
# on the PC, Administrator PowerShell (or paste the screen's line instead)
.\Linko-Setup.ps1 -TailscaleAuthKey <key> -ManagementPublicKey "<ssh-ed25519 AAAA... manager>"
# prints hostname, Tailscale IP and Syncthing device ID

# on the server
sudo -E .venv/bin/python -m ai_manager.provision add-user --name anna --email anna@example.com \
  --windows-folder 'C:\Users\Anna\Documents\Work' --domain example.com --syncthing-device-id <ID> \
  --service-user <manager user>          # adds the scoped ACL steps; review the plan, then repeat with --apply
```

`--skip-cloudflare` / `--skip-syncthing` omit those parts. The CLI leaves `<data>/initial-login.txt` (mode 0600) for you
to hand over and delete. Afterwards register deploys with `python3 scripts/install_deploy.py --instance anna` as the
manager user (after copying the env file as in INSTALL section 6) and set `service_cmds` as described in the README.

## 5. Troubleshooting

- **"The host helper is not installed"** — section 2.3. Check `python3 scripts/install_onboarding.py status`; the path unit
  must be `active` (`systemctl --user status linko-bot-manager-onboarding.path`).
- **Plan or apply never answers (record fails after 5 minutes / 3 hours)** — `journalctl --user -u linko-bot-manager-onboarding`;
  typical: the sudoers rule is missing (re-run `install`), `/etc/linko/onboarding.json` names another data directory, or
  `sudo: a password is required` (the unit must not have `NoNewPrivileges`).
- **Refused: hostname already routes to ...** — another service owns the name; use another name or fix the tunnel by hand.
- **Missing CLOUDFLARE_* / SYNCTHING_API_KEY** — section 2.2; the plan shows it before anything changes.
- **"Tailscale refused to create an auth key"** — the API key expired, the tailnet name is wrong, or `tag:linko-pc` is not in
  `tagOwners`.
- **The PC cannot report in** — it must reach `LINKO_MANAGER_URL` over Tailscale (policy `grants`, Tailscale Serve running).
  A `403` means the request did not arrive from a tailnet address (check the proxy forwards the client address), the token
  was used already or it expired. Generate a new command; the PC script is safe to re-run. Meanwhile you can type the PC's
  Syncthing device ID under *Enter a device ID by hand*.
- **Syncthing cannot write the folder** — `getfacl /home/<name>/Documents/<name>` must list the Syncthing user; Retry re-applies.
- **No Tailscale IP on Windows** — the auth key expired or was used; generate a new command.
- **SSH refused** — the connection must come from the Tailscale range; check the `Linko SSH (Tailscale only)` rule and `sshd`.
- **Service down** — `journalctl -u linko-bot-manager-<name>`; check the port is not taken.
- **The initial password was not copied** — stop the unit, set a new login as that user:
  `sudo -u <name> sqlite3 ~<name>/.local/share/linko-bot-manager-<name>/manager.sqlite3 'DELETE FROM owner'`, then in a shell
  with that instance's env file loaded (`set -a; . ~<name>/.config/linko-bot-manager-<name>.env; set +a`) run
  `python -m ai_manager.cli setup-owner` and start the unit.

## 6. Removal by hand

There is deliberately no automatic removal. In order: delete the Access application, DNS CNAME and tunnel ingress rule
in Cloudflare; remove the Syncthing folder `linko-<name>` and the device (Syncthing GUI); remove the `<name>` entry from
`instances` in `<data>/deploy/installed.json`; then `systemctl disable --now linko-bot-manager-<name>`, delete
`/etc/systemd/system/linko-bot-manager-<name>.service`, `systemctl daemon-reload`, and finally `userdel -r <name>` after
backing up what you need. Delete the onboarding record's row if you want the name free again
(`sqlite3 <data>/manager.sqlite3 "DELETE FROM onboarding WHERE name='<name>'"` while the manager is idle). On Windows, remove the
management key from `%ProgramData%\ssh\administrators_authorized_keys`, and uninstall Tailscale/Syncthing if wanted; in the
Tailscale admin console remove the machine. To remove the helper itself:
`sudo python3 scripts/install_onboarding.py uninstall` and `systemctl --user disable --now linko-bot-manager-onboarding.path`.

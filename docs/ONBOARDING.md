# Onboarding a restricted user

Two pieces: `deploy/windows/Linko-Setup.ps1` enrolls the person's Windows PC, and
`python -m ai_manager.provision add-user` creates their restricted instance on the management server. The instance
itself is the existing restricted work instance ([INSTALL.md section 6](INSTALL.md#6-optional-extra-instances),
[README](../README.md#multiple-instances-and-the-restricted-work-instance)); this runbook only automates its setup.

## 1. Credentials (once, on the management server)

Put tokens in the environment or in `~/.config/linko-onboarding.env` (`chmod 600`, outside any git work tree, plain
`KEY=VALUE`). Environment variables win over the file. Never commit them.

| Key | Used for |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Edit tunnel config, DNS and Access apps (Account: Cloudflare Tunnel Edit, Access Apps Edit; Zone: DNS Edit) |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_TUNNEL_ID` | The existing account, zone and tunnel |
| `SYNCTHING_API_KEY` | Local Syncthing GUI API key (`http://127.0.0.1:8384`) |
| `LINKO_DOMAIN` | Optional default for `--domain`; the hostname becomes `<name>.<domain>` |

You also need a Tailscale auth key (reusable-off, short expiry) and the management server's SSH **public** key for the
Windows script. AI provider login (Codex/Claude) is never copied from the owner: the new user logs in themselves (or
you decide an explicit policy) after provisioning.

## 2. Windows enrollment

On the user's PC, in an Administrator PowerShell:

```powershell
.\Linko-Setup.ps1 -TailscaleAuthKey <key> -ManagementPublicKey "<ssh-ed25519 AAAA... manager>"
```

(or set `TAILSCALE_AUTHKEY` / `LINKO_MANAGEMENT_PUBKEY`). It installs Tailscale, OpenSSH Server (key-only for admins,
firewalled to `100.64.0.0/10`) and Syncthing (scheduled task at logon). It is re-runnable and prints the hostname,
Tailscale IP and Syncthing device ID. In the Syncthing GUI on Windows, accept the folder share when it arrives.

## 3. Provisioning

Run as root on the management server. The default is a dry run that prints the plan and changes nothing:

```bash
sudo -E .venv/bin/python -m ai_manager.provision add-user --name anna --email anna@example.com \
  --windows-folder 'C:\Users\Anna\Documents\Work' --domain example.com --syncthing-device-id <ID>
# review, then repeat with --apply
```

Steps (each checked first; existing things are skipped, never overwritten): Linux user, project/data dirs, `git init`,
free localhost port, `~/.config/linko-bot-manager-<name>.env` (`AI_MANAGER_RESTRICTED=1`, no secrets), system unit
`linko-bot-manager-<name>.service` with `MemoryMax` (`--memory-max`), app login, Cloudflare tunnel route + DNS CNAME +
Access application limited to the email, Syncthing device and folder (staggered versioning, `.git` ignored). All checks
run before any change; a hostname that already routes elsewhere, an existing non-restricted env file, or a Syncthing
folder already using the path **refuses the whole run**. Use `--skip-cloudflare` / `--skip-syncthing` to omit those parts.
The app login is the email with a random password saved in `<data>/initial-login.txt` (mode 0600); an existing owner is
never replaced. Hand it over once, then delete the file. A second `--apply` reports everything as skipped.

Afterwards register the instance for deploys (`scripts/install_deploy.py --instance NAME`, see INSTALL section 6) and
let the user log in to their AI provider.

## 4. Troubleshooting

- **Refused: hostname already routes to ...** — another service owns the name; pick another `--name` or fix the tunnel by hand.
- **Missing CLOUDFLARE_* / SYNCTHING_API_KEY** — see section 1; the check fails before any change.
- **Syncthing cannot write the folder** — the Syncthing daemon's user needs access to `/home/<name>/Documents/<name>` (e.g. ACL).
- **No Tailscale IP on Windows** — the auth key expired; re-run the script with a fresh key.
- **SSH refused** — the connection must come from the Tailscale range; check `Linko SSH (Tailscale only)` rule and `sshd`.
- **Service down** — `journalctl -u linko-bot-manager-<name>`; check the port is not taken.

## 5. Removal by hand

There is deliberately no automatic removal. In order: delete the Access application, DNS CNAME and tunnel ingress rule
in Cloudflare; remove the Syncthing folder `linko-<name>` and the device (Syncthing GUI); then
`systemctl disable --now linko-bot-manager-<name>`, delete `/etc/systemd/system/linko-bot-manager-<name>.service`,
`systemctl daemon-reload`, and finally `userdel -r <name>` after backing up what you need. On Windows, remove the
management key from `%ProgramData%\ssh\administrators_authorized_keys` and uninstall Tailscale/Syncthing if wanted.

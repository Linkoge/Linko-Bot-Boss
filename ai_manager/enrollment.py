"""Windows enrollment: the single PowerShell line an owner pastes on the new PC.

The line carries the repository's own deploy/windows/Linko-Setup.ps1 (gzip + base64, so there is no download and no
public endpoint) and exactly these values: a single-use Tailscale auth key, the management server's SSH *public* key,
the Windows folder, the manager's tailnet URL, its Syncthing device ID (public) and a one-time enrollment token.
Cloudflare, Syncthing and Tailscale API keys never appear in it. Runbook: docs/ONBOARDING.md.
"""
import base64
import gzip
import hashlib
import ipaddress
import json
import re
import secrets
import subprocess
import time
import urllib.parse
from pathlib import Path

from .provision import DEVICE_RE, FOLDER_RE, ProvisionError, http_json

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "windows" / "Linko-Setup.ps1"
TOKEN_SECONDS = 2 * 3600
TAILSCALE_API = "https://api.tailscale.com/api/v2"
DEFAULT_TAG = "tag:linko-pc"
AUTH_KEY_RE = re.compile(r"^tskey-[A-Za-z0-9-]{8,200}$")
PUBKEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]{20,2000}( [^\x00-\x1f\x7f'\"`$‘’“”]{0,100})?$")
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(now=None):
    """(token, stored hash, expiry). Only the hash is kept; the token exists once, inside the pasted line."""
    token = secrets.token_urlsafe(32)
    return token, digest(token), (now if now is not None else time.time()) + TOKEN_SECONDS


def in_tailnet(address):
    try:
        return ipaddress.ip_address(address) in TAILNET
    except ValueError:
        return False


def ps_quote(value):
    """A PowerShell single-quoted literal. Callers validate first; doubling quotes is the second line of defence."""
    value = str(value)
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ProvisionError("control characters cannot go into the command")
    return "'" + re.sub("['‘’]", lambda m: m.group(0) * 2, value) + "'"


def mint_auth_key(config, name, http=http_json):
    """A single-use, preauthorized, tagged Tailscale auth key that expires in two hours, via the Tailscale API."""
    key, tailnet = config.get("TAILSCALE_API_KEY", ""), config.get("TAILSCALE_TAILNET", "")
    if not key or not tailnet:
        raise ProvisionError("TAILSCALE_API_KEY and TAILSCALE_TAILNET are not configured")
    body = {"capabilities": {"devices": {"create": {"reusable": False, "ephemeral": False, "preauthorized": True,
                                                    "tags": [config.get("TAILSCALE_TAG") or DEFAULT_TAG]}}},
            "expirySeconds": TOKEN_SECONDS, "description": f"linko onboarding {name}"}
    status, data = http("POST", f"{TAILSCALE_API}/tailnet/{urllib.parse.quote(tailnet, safe='')}/keys",
                        {"Authorization": f"Bearer {key}"}, body)
    auth = (data or {}).get("key", "")
    if status >= 300 or not AUTH_KEY_RE.match(auth):
        raise ProvisionError(f"Tailscale refused to create an auth key (HTTP {status}); check the API key, tailnet and tag")
    return auth


def management_public_key(config, home):
    """The management server's SSH public key, read from the service user's ~/.ssh (or LINKO_MANAGEMENT_PUBKEY_FILE)."""
    candidates = [Path(config["LINKO_MANAGEMENT_PUBKEY_FILE"]).expanduser()] if config.get("LINKO_MANAGEMENT_PUBKEY_FILE") else [
        Path(home) / ".ssh" / name for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")]
    for path in candidates:
        try:
            line = path.read_text().strip().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        if PUBKEY_RE.match(line):
            return line
    raise ProvisionError("no management SSH public key found; create one with `ssh-keygen -t ed25519` as the manager's user")


def manager_url(config, run=None):
    """The manager's own tailnet URL (Tailscale Serve), from LINKO_MANAGER_URL or `tailscale status --json`."""
    url = (config.get("LINKO_MANAGER_URL") or "").rstrip("/")
    if not url:
        try:
            done = (run or subprocess.run)(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=15)
            name = json.loads(done.stdout)["Self"]["DNSName"].rstrip(".")
            url = f"https://{name}"
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            raise ProvisionError("set LINKO_MANAGER_URL (https://<manager>.<tailnet>.ts.net) in the onboarding config") from None
    if not URL_RE.match(url):
        raise ProvisionError("LINKO_MANAGER_URL must look like https://host.tailnet.ts.net")
    return url


def syncthing_device_id(config, http=http_json):
    """The management Syncthing's own (public) device ID: LINKO_SYNCTHING_DEVICE_ID, else its local API."""
    device = config.get("LINKO_SYNCTHING_DEVICE_ID", "")
    if not device:
        if not config.get("SYNCTHING_API_KEY"):
            raise ProvisionError("SYNCTHING_API_KEY (or LINKO_SYNCTHING_DEVICE_ID) is not configured")
        status, data = http("GET", config.get("SYNCTHING_URL", "http://127.0.0.1:8384") + "/rest/system/status",
                            {"X-API-Key": config["SYNCTHING_API_KEY"]})
        device = (data or {}).get("myID", "") if status < 300 else ""
    if not DEVICE_RE.match(device):
        raise ProvisionError("cannot determine the management Syncthing device ID")
    return device


def build_command(*, auth_key, public_key, windows_folder, url, manager_device, token, folder_id, script=None):
    """The one-liner. Everything is validated here, whatever the caller already checked."""
    if not AUTH_KEY_RE.match(auth_key or ""):
        raise ProvisionError("the Tailscale auth key must look like tskey-auth-...")
    if not PUBKEY_RE.match(public_key or "") or not URL_RE.match(url or "") or not DEVICE_RE.match(manager_device or ""):
        raise ProvisionError("invalid enrollment parameters")
    if not FOLDER_RE.match(windows_folder or "") or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token) \
            or not re.fullmatch(r"linko-[a-z0-9-]{2,31}", folder_id):
        raise ProvisionError("invalid enrollment parameters")
    text = script if script is not None else SCRIPT.read_text(encoding="utf-8")
    packed = base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode()
    unpack = ("[scriptblock]::Create((New-Object IO.StreamReader((New-Object IO.Compression.GZipStream("
              f"(New-Object IO.MemoryStream(,[Convert]::FromBase64String('{packed}'))),"
              "[IO.Compression.CompressionMode]::Decompress)),[Text.Encoding]::UTF8)).ReadToEnd())")
    arguments = {"TailscaleAuthKey": auth_key, "ManagementPublicKey": public_key, "WindowsFolder": windows_folder,
                 "ManagerUrl": url, "ManagerDeviceId": manager_device, "FolderId": folder_id, "EnrollmentToken": token}
    return f"& ({unpack}) " + " ".join(f"-{name} {ps_quote(value)}" for name, value in arguments.items())

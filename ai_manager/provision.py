"""Onboard one restricted user/instance: `python -m ai_manager.provision add-user ...` (dry run unless --apply).

Every step is check-then-reconcile: `check()` only reads and says exists / todo / refuse, `apply()` changes things only
when the check said todo. All checks run before any change, so a refusal (a hostname that already routes elsewhere, a
Syncthing folder path in use) leaves the host untouched. API tokens come from the environment or an ignored config file
outside git (default ~/.config/linko-onboarding.env); nothing secret is written to generated files or the receipt.
Runbook: docs/ONBOARDING.md.
"""
import argparse
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

EXISTS, TODO, REFUSE = "exists", "todo", "refuse"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
EMAIL_RE = re.compile(r"^[^@\s,\"']+@[^@\s,\"']+\.[^@\s,\"']+$")
FIRST_PORT, LAST_PORT = 8788, 8899
DEFAULT_CONFIG = Path("~/.config/linko-onboarding.env")
CF_API = "https://api.cloudflare.com/client/v4"
CREDENTIAL_NOTE = "AI provider login (Codex/Claude) is a manual step done as the new user; nothing is copied from the owner."


class ProvisionError(Exception):
    pass


def load_config(path, environ):
    """Env vars win over the ignored KEY=VALUE config file (never inside a git work tree by convention)."""
    values = {}
    path = Path(path).expanduser()
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    values.update({k: v for k, v in environ.items() if k.startswith(("CLOUDFLARE_", "SYNCTHING_", "LINKO_")) and v})
    return values


def run_command(argv, **kwargs):
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120, **kwargs)
    return done.returncode, (done.stdout + done.stderr).strip()


def http_json(method, url, headers=None, body=None):
    """(status, parsed JSON or None). Network errors raise ProvisionError."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, None
    except OSError as error:
        raise ProvisionError(f"cannot reach {url.split('?')[0]}: {error}") from error


def port_is_free(port):
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


@dataclass
class Plan:
    name: str
    email: str
    windows_folder: str
    domain: str
    repo: Path
    python: str
    home_base: Path
    config: dict
    apply: bool
    memory_max: str = "1G"
    syncthing_device: str = ""
    syncthing_url: str = "http://127.0.0.1:8384"
    skip_cloudflare: bool = False
    skip_syncthing: bool = False
    systemd_dir: Path = Path("/etc/systemd/system")
    run: object = run_command
    http: object = http_json
    port_free: object = port_is_free
    port: int = 0

    @property
    def home(self):
        return self.home_base / self.name

    @property
    def project(self):
        return self.home / "Documents" / self.name

    @property
    def data(self):
        return self.home / ".local/share" / f"linko-bot-manager-{self.name}"

    @property
    def env_file(self):
        return self.home / ".config" / f"linko-bot-manager-{self.name}.env"

    @property
    def unit_name(self):
        return f"linko-bot-manager-{self.name}.service"

    @property
    def hostname(self):
        return f"{self.name}.{self.domain}"

    @property
    def folder_id(self):
        return f"linko-{self.name}"


class Step:
    title = ""
    state = ""
    detail = ""

    def __init__(self, plan):
        self.plan = plan

    def check(self):
        raise NotImplementedError

    def apply(self):
        raise NotImplementedError


def sh(plan, argv, **kwargs):
    code, output = plan.run(argv, **kwargs)
    if code:
        raise ProvisionError(f"{' '.join(argv[:3])} failed: {output[-300:]}")
    return output


class LinuxUser(Step):
    title = "linux user"

    def check(self):
        code, uid = self.plan.run(["id", "-u", self.plan.name])
        if code == 0:
            if uid == "0":
                return REFUSE, "refusing to use root"
            return EXISTS, f"user {self.plan.name} present"
        return TODO, f"useradd {self.plan.name} (unprivileged, no sudo, no password)"

    def apply(self):
        sh(self.plan, ["useradd", "--create-home", "--home-dir", str(self.plan.home), "--shell", "/bin/bash", self.plan.name])
        return "created"


class Directories(Step):
    title = "project and data dirs"

    def _wanted(self):
        p = self.plan
        return [p.project, p.data, p.env_file.parent]

    def check(self):
        missing = [d for d in self._wanted() if not d.is_dir()]
        return (TODO, "create " + ", ".join(map(str, missing))) if missing else (EXISTS, "all present")

    def apply(self):
        for folder in self._wanted():
            folder.mkdir(parents=True, exist_ok=True)
        sh(self.plan, ["chown", "-R", f"{self.plan.name}:{self.plan.name}", str(self.plan.home)])
        self.plan.data.chmod(0o700)
        return "created"


class GitInit(Step):
    title = "git repository"

    def check(self):
        return (EXISTS, "already a git repository") if (self.plan.project / ".git").exists() else (TODO, f"git init {self.plan.project}")

    def apply(self):
        sh(self.plan, ["runuser", "-u", self.plan.name, "--", "git", "init", str(self.plan.project)])
        return "created"


class Port(Step):
    title = "local port"

    def check(self):
        p = self.plan
        if p.env_file.is_file():
            match = re.search(r"^AI_MANAGER_PORT=(\d+)$", p.env_file.read_text(), re.M)
            if match:
                p.port = int(match.group(1))
                return EXISTS, f"port {p.port} from existing env file"
        taken = set()
        for env in list(p.home_base.glob("*/.config/linko-bot-manager*.env")) + list(Path.home().glob(".config/linko-bot-manager*.env")):
            taken.update(int(m) for m in re.findall(r"^AI_MANAGER_PORT=(\d+)$", env.read_text(errors="ignore"), re.M))
        for port in range(FIRST_PORT, LAST_PORT + 1):
            if port not in taken and p.port_free(port):
                p.port = port
                return TODO, f"allocate 127.0.0.1:{port}"
        return REFUSE, f"no free port between {FIRST_PORT} and {LAST_PORT}"

    def apply(self):
        return "allocated"  # persisted by the env file step


class EnvFile(Step):
    title = "instance env file"

    def content(self):
        p = self.plan
        return (
            "AI_MANAGER_RESTRICTED=1\n"
            f"AI_MANAGER_PROJECT={p.project}\n"
            f"AI_MANAGER_DATA={p.data}\n"
            f"AI_MANAGER_PORT={p.port}\n"
            f"AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,{p.hostname}\n"
            "AI_MANAGER_SECURE_COOKIE=1\n"
        )

    def check(self):
        path = self.plan.env_file
        if path.is_file():
            if "AI_MANAGER_RESTRICTED=1" not in path.read_text().splitlines():
                return REFUSE, f"{path} exists but is not restricted; not overwriting"
            return EXISTS, f"{path} present"
        return TODO, f"write {path} (restricted, no secrets)"

    def apply(self):
        path = self.plan.env_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.content())
        path.chmod(0o600)
        sh(self.plan, ["chown", f"{self.plan.name}:{self.plan.name}", str(path)])
        return "created"


class SystemdUnit(Step):
    title = "systemd unit"

    def content(self):
        p = self.plan
        return (
            f"[Unit]\nDescription=Linko Bot Boss restricted instance {p.name}\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nType=simple\nUser={p.name}\nWorkingDirectory={p.repo}\n"
            f"ExecStart={p.python} -m ai_manager.cli serve\nEnvironment=PYTHONUNBUFFERED=1\n"
            f"EnvironmentFile={p.env_file}\nUMask=0077\nNoNewPrivileges=yes\nMemoryMax={p.memory_max}\n"
            "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=30\nKillMode=control-group\n\n[Install]\nWantedBy=multi-user.target\n"
        )

    def check(self):
        path = self.plan.systemd_dir / self.plan.unit_name
        if path.is_file():
            same = path.read_text() == self.content()
            return EXISTS, f"{path} present" + ("" if same else " (differs from the template; left untouched)")
        return TODO, f"write {path} with MemoryMax={self.plan.memory_max}"

    def apply(self):
        (self.plan.systemd_dir / self.plan.unit_name).write_text(self.content())
        sh(self.plan, ["systemctl", "daemon-reload"])
        return "created"


class OwnerLogin(Step):
    title = "app login"

    def password_file(self):
        return self.plan.data / "initial-login.txt"

    def check(self):
        db = self.plan.data / "manager.sqlite3"
        if db.is_file():
            try:
                with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
                    row = conn.execute("SELECT username FROM owner").fetchone()
            except sqlite3.Error:
                row = None
            if row:
                return EXISTS, "an owner login already exists; not replaced"
        return TODO, f"create login {self.plan.email} (random password saved 0600 in {self.password_file()})"

    def apply(self):
        p = self.plan
        password = secrets.token_urlsafe(18)
        file = self.password_file()
        file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(f"{p.email}\n{password}\n")
        sh(p, ["chown", f"{p.name}:{p.name}", str(file)])
        sh(p, ["runuser", "-u", p.name, "--", "env", f"AI_MANAGER_DATA={p.data}", f"AI_MANAGER_PROJECT={p.project}",
               "AI_MANAGER_RESTRICTED=1", p.python, "-m", "ai_manager.provision", "create-owner", "--password-file", str(file),
               "--email", p.email])
        return "created"


class StartService(Step):
    title = "service enabled"

    def check(self):
        code, _ = self.plan.run(["systemctl", "is-enabled", self.plan.unit_name])
        return (EXISTS, "already enabled") if code == 0 else (TODO, f"systemctl enable --now {self.plan.unit_name}")

    def apply(self):
        sh(self.plan, ["systemctl", "enable", "--now", self.plan.unit_name])
        return "started"


class Cloudflare(Step):
    def api(self, method, path, body=None):
        token = self.plan.config.get("CLOUDFLARE_API_TOKEN", "")
        status, data = self.plan.http(method, CF_API + path, {"Authorization": f"Bearer {token}"}, body)
        if status >= 300 or not data or not data.get("success", True):
            raise ProvisionError(f"Cloudflare {method} {path.split('?')[0]} -> HTTP {status}")
        return data.get("result")

    def need(self, *keys):
        missing = [k for k in keys if not self.plan.config.get(k)]
        if missing:
            raise ProvisionError("missing " + ", ".join(missing) + " (environment or ignored config file)")
        return [self.plan.config[k] for k in keys]


class TunnelRoute(Cloudflare):
    title = "cloudflare tunnel route"

    def _path(self):
        account, tunnel = self.need("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_TUNNEL_ID")
        return f"/accounts/{account}/cfd_tunnel/{tunnel}/configurations"

    def check(self):
        self.need("CLOUDFLARE_API_TOKEN")
        result = self.api("GET", self._path()) or {}
        self.config = (result.get("config") or {})
        service = f"http://127.0.0.1:{self.plan.port}"
        for rule in self.config.get("ingress", []):
            if rule.get("hostname") == self.plan.hostname:
                if rule.get("service") == service:
                    return EXISTS, f"{self.plan.hostname} already routes to {service}"
                return REFUSE, f"{self.plan.hostname} already routes to {rule.get('service')}, not {service}"
        return TODO, f"add ingress {self.plan.hostname} -> {service}"

    def apply(self):
        ingress = [r for r in self.config.get("ingress", [])]
        rule = {"hostname": self.plan.hostname, "service": f"http://127.0.0.1:{self.plan.port}"}
        catch_all = [r for r in ingress if "hostname" not in r] or [{"service": "http_status:404"}]
        updated = {**self.config, "ingress": [r for r in ingress if "hostname" in r] + [rule] + catch_all[-1:]}
        self.api("PUT", self._path(), {"config": updated})
        return "created"


class DnsRecord(Cloudflare):
    title = "cloudflare dns cname"

    def check(self):
        self.need("CLOUDFLARE_API_TOKEN")
        zone, tunnel = self.need("CLOUDFLARE_ZONE_ID", "CLOUDFLARE_TUNNEL_ID")
        target = f"{tunnel}.cfargotunnel.com"
        records = self.api("GET", f"/zones/{zone}/dns_records?name={self.plan.hostname}") or []
        for record in records:
            if record.get("type") == "CNAME" and record.get("content") == target:
                return EXISTS, f"{self.plan.hostname} -> {target}"
            return REFUSE, f"{self.plan.hostname} already has a {record.get('type')} record to {record.get('content')}"
        return TODO, f"add CNAME {self.plan.hostname} -> {target}"

    def apply(self):
        zone, tunnel = self.need("CLOUDFLARE_ZONE_ID", "CLOUDFLARE_TUNNEL_ID")
        self.api("POST", f"/zones/{zone}/dns_records",
                 {"type": "CNAME", "name": self.plan.hostname, "content": f"{tunnel}.cfargotunnel.com", "proxied": True})
        return "created"


class AccessApp(Cloudflare):
    title = "cloudflare access application"

    def check(self):
        self.need("CLOUDFLARE_API_TOKEN")
        (account,) = self.need("CLOUDFLARE_ACCOUNT_ID")
        for app in self.api("GET", f"/accounts/{account}/access/apps") or []:
            if app.get("domain") == self.plan.hostname:
                return EXISTS, f"access application '{app.get('name')}' already covers {self.plan.hostname}; not modified"
        return TODO, f"create access application for {self.plan.hostname} limited to {self.plan.email}"

    def apply(self):
        (account,) = self.need("CLOUDFLARE_ACCOUNT_ID")
        self.api("POST", f"/accounts/{account}/access/apps", {
            "name": f"linko-{self.plan.name}", "domain": self.plan.hostname, "type": "self_hosted", "session_duration": "24h",
            "policies": [{"name": f"{self.plan.name} owner", "decision": "allow", "include": [{"email": {"email": self.plan.email}}]}]})
        return "created"


class Syncthing(Step):
    def api(self, method, path, body=None):
        key = self.plan.config.get("SYNCTHING_API_KEY", "")
        if not key:
            raise ProvisionError("missing SYNCTHING_API_KEY (environment or ignored config file)")
        status, data = self.plan.http(method, self.plan.syncthing_url + path, {"X-API-Key": key}, body)
        if status >= 300:
            raise ProvisionError(f"Syncthing {method} {path.split('?')[0]} -> HTTP {status}")
        return data


class SyncDevice(Syncthing):
    title = "syncthing device"

    def check(self):
        if not self.plan.syncthing_device:
            raise ProvisionError("--syncthing-device-id is required (printed by Linko-Setup.ps1)")
        devices = self.api("GET", "/rest/config/devices") or []
        if any(d.get("deviceID") == self.plan.syncthing_device for d in devices):
            return EXISTS, "device already known; its settings are not changed"
        return TODO, f"add device {self.plan.syncthing_device[:7]}…"

    def apply(self):
        d = self.plan.syncthing_device
        self.api("PUT", f"/rest/config/devices/{d}", {"deviceID": d, "name": f"windows-{self.plan.name}", "autoAcceptFolders": False})
        return "created"


class SyncFolder(Syncthing):
    title = "syncthing folder"

    def check(self):
        p = self.plan
        for folder in self.api("GET", "/rest/config/folders") or []:
            if folder.get("id") == p.folder_id:
                return EXISTS, f"folder {p.folder_id} already exists; not changed"
            if Path(folder.get("path", "")).resolve() == p.project.resolve():
                return REFUSE, f"folder {folder.get('id')} already shares {p.project}"
        return TODO, f"share {p.project} as {p.folder_id} (staggered versioning, .git ignored; Windows side: {p.windows_folder})"

    def apply(self):
        p = self.plan
        mine = (self.api("GET", "/rest/system/status") or {}).get("myID")
        devices = [{"deviceID": d} for d in (mine, p.syncthing_device) if d]
        self.api("PUT", f"/rest/config/folders/{p.folder_id}", {
            "id": p.folder_id, "label": f"{p.name} ({p.windows_folder})", "path": str(p.project), "type": "sendreceive",
            "devices": devices, "fsWatcherEnabled": True,
            "versioning": {"type": "staggered", "params": {"maxAge": "2592000", "cleanInterval": "3600"}}})
        self.api("POST", f"/rest/db/ignores?folder={p.folder_id}", {"ignore": [".git", ".git/**"]})
        return "created"


def build_steps(plan):
    steps = [LinuxUser, Directories, GitInit, Port, EnvFile, SystemdUnit, OwnerLogin, StartService]
    if not plan.skip_cloudflare:
        steps += [TunnelRoute, DnsRecord, AccessApp]
    if not plan.skip_syncthing:
        steps += [SyncDevice, SyncFolder]
    return [cls(plan) for cls in steps]


def provision(plan):
    """Run all checks first, then (with plan.apply) reconcile. Returns the receipt: list of (title, status, detail)."""
    steps = build_steps(plan)
    for step in steps:
        try:
            step.state, step.detail = step.check()
        except ProvisionError as error:
            step.state, step.detail = REFUSE, str(error)
    if any(s.state == REFUSE for s in steps):
        return [(s.title, "refused" if s.state == REFUSE else "not run", s.detail) for s in steps]
    receipt = []
    for step in steps:
        if step.state == EXISTS:
            receipt.append((step.title, "skipped", step.detail))
        elif not plan.apply:
            receipt.append((step.title, "planned", step.detail))
        else:
            try:
                receipt.append((step.title, step.apply(), step.detail))
            except ProvisionError as error:
                receipt.append((step.title, "failed", str(error)))
                break
    return receipt


def print_receipt(receipt, plan, out=print):
    out(("RECEIPT" if plan.apply else "DRY RUN PLAN (nothing changed; add --apply)") + f" for {plan.name} <{plan.email}>")
    for title, status, detail in receipt:
        out(f"  [{status:8}] {title}: {detail}")
    out(f"  hostname https://{plan.hostname}  port {plan.port or '-'}")
    out(f"  note: {CREDENTIAL_NOTE}")


def create_owner(email, password_file):
    """Runs as the new Linux user inside its restricted environment (see OwnerLogin.apply)."""
    from .auth import Auth
    from .config import Settings
    from .db import Database

    settings = Settings()
    settings.prepare()
    password = Path(password_file).read_text().splitlines()[1]
    Auth(Database(settings.db_path), settings).setup(email, password)


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog="ai_manager.provision")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-user", help="onboard one restricted user/instance (dry run unless --apply)")
    add.add_argument("--name", required=True)
    add.add_argument("--email", required=True)
    add.add_argument("--windows-folder", required=True)
    add.add_argument("--domain", help="zone for <name>.<domain>; or LINKO_DOMAIN")
    add.add_argument("--syncthing-device-id")
    add.add_argument("--memory-max", default="1G")
    add.add_argument("--home-base", type=Path, default=Path("/home"))
    add.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="ignored KEY=VALUE credentials file outside git")
    add.add_argument("--skip-cloudflare", action="store_true")
    add.add_argument("--skip-syncthing", action="store_true")
    add.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    add.add_argument("--dry-run", action="store_true", help="explicit dry run (the default)")
    owner = commands.add_parser("create-owner")
    owner.add_argument("--email", required=True)
    owner.add_argument("--password-file", required=True)
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        create_owner(args.email, args.password_file)
        return 0
    config = load_config(args.config, os.environ if environ is None else environ)
    domain = args.domain or config.get("LINKO_DOMAIN", "")
    if not NAME_RE.match(args.name):
        parser.error("--name: lowercase letters, digits and dashes, starting with a letter (2-31 characters)")
    if not EMAIL_RE.match(args.email):
        parser.error("--email is not a valid address")
    if not domain and not args.skip_cloudflare:
        parser.error("--domain (or LINKO_DOMAIN) is required unless --skip-cloudflare")
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run exclude each other")
    plan = Plan(name=args.name, email=args.email, windows_folder=args.windows_folder, domain=domain or "local",
                repo=Path(__file__).resolve().parents[1], python=sys.executable, home_base=args.home_base, config=config,
                apply=args.apply, memory_max=args.memory_max, syncthing_device=args.syncthing_device_id or "",
                skip_cloudflare=args.skip_cloudflare, skip_syncthing=args.skip_syncthing)
    receipt = provision(plan)
    print_receipt(receipt, plan)
    return 1 if any(s in ("failed", "refused") for _, s, _ in receipt) else 0


if __name__ == "__main__":
    raise SystemExit(main())

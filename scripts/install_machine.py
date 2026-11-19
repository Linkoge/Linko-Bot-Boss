"""Idempotent first-install helper for a new machine: the non-interactive steps of docs/INSTALL.md.

Safe to re-run. `--dry-run` only reads (version probes, `tailscale status`) and prints the plan; it creates nothing.
Human-only steps (Tailscale login, provider CLI login, sudo) are never attempted: the helper stops with exit 2 and
a `HUMAN STEP` line. The generated owner password is printed once and never stored in the repository.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HUMAN = 2


class Stop(SystemExit):
    pass


def human(message):
    print(f"HUMAN STEP: {message}")
    raise Stop(HUMAN)


class Installer:
    def __init__(self, args, home, env):
        self.dry, self.home, self.env, self.port = args.dry_run, home, env, args.port
        self.username, self.hostname = args.username, args.hostname
        self.data = Path(env.get("AI_MANAGER_DATA") or home / ".local/share/linko-bot-manager")
        self.env_file = home / ".config/linko-bot-manager.env"
        self.python = REPO / ".venv/bin/python"
        self.env.update(AI_MANAGER_DATA=str(self.data), AI_MANAGER_PORT=str(self.port))

    def say(self, message):
        print(("[dry-run] " if self.dry else "") + message)

    def run(self, command, **kw):
        self.say("run: " + " ".join(map(str, command)))
        if self.dry:
            return None
        return subprocess.run(command, cwd=REPO, env=self.env, check=True, **kw)

    def probe(self, command):
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def prerequisites(self):
        missing = [name for name in ("git", "python3", "uv", "tailscale") if not shutil.which(name)]
        if missing:
            human(f"install {', '.join(missing)} first (docs/INSTALL.md step 1; apt needs the sudo password)")
        if not (shutil.which("codex") or shutil.which("claude")):
            human("install at least one provider CLI (Claude Code or Codex) and log in to it (docs/INSTALL.md step 3)")
        self.say("prerequisites present: git python3 uv tailscale and a provider CLI")

    def tailscale_host(self):
        result = self.probe(["tailscale", "status", "--json"])
        try:
            status = json.loads(result.stdout)
        except (AttributeError, ValueError):
            status = {}
        host = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
        if status.get("BackendState") != "Running" or not host:
            human("log in to Tailscale (`sudo tailscale up`, approve the machine in the admin console), then re-run")
        return host

    def dependencies(self):
        self.run(["uv", "sync", "--locked"])
        self.run(["uv", "run", "playwright", "install", "chromium"])

    def configuration(self):
        self.say(f"data dir: {self.data} (mode 700)")
        self.say(f"env file: {self.env_file} (mode 600, never overwritten)")
        if self.dry:
            return
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        if self.env_file.exists():
            return
        lines = []
        for name, key in (("claude", "AI_MANAGER_CLAUDE"), ("codex", "AI_MANAGER_CODEX")):
            if shutil.which(name):
                lines.append(f"{key}={shutil.which(name)}")
        path = [str(Path(shutil.which(n)).parent) for n in ("codex", "claude", "node") if shutil.which(n)]
        path += [str(self.home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"]
        lines += [
            "PATH=" + ":".join(dict.fromkeys(path)),
            f"AI_MANAGER_ALLOWED_HOSTS=localhost,127.0.0.1,{self.hostname}",
            f"AI_MANAGER_DATA={self.data}",
            f"AI_MANAGER_PORT={self.port}",
            f"AI_MANAGER_PROJECT_ROOTS={self.home / 'projects'}",
            "AI_MANAGER_SECURE_COOKIE=1",
        ]
        fd = os.open(self.env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")

    def owner_exists(self):
        db = self.data / "manager.sqlite3"
        if not db.is_file():
            return False
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                return bool(conn.execute("SELECT id FROM owner").fetchone())
        except sqlite3.Error:
            return False

    def owner(self):
        if self.owner_exists():
            self.say("owner already exists: skipped (password unchanged)")
            return
        command = [str(self.python), "-m", "ai_manager.cli", "setup-owner", "--username", self.username, "--generate-password"]
        self.say("run: " + " ".join(command))
        if not self.dry:
            subprocess.run(command, cwd=REPO, env=self.env, check=True)
            print("Tell the owner this INITIAL_PASSWORD in chat once; do not write it to a file or the repository.")
            print("The owner should change it after first login if it was ever pasted anywhere (chat, logs, terminal history).")

    def service(self):
        self.run(["python3", "scripts/install_service.py", "--hostname", self.hostname, "--start"])
        self.run(["loginctl", "enable-linger", os.environ.get("USER", "")])
        self.run(["systemctl", "--user", "restart", "linko-bot-manager.service"])

    def health(self):
        url = f"http://127.0.0.1:{self.port}/api/auth-status"
        self.say(f"health check: GET {url} must answer JSON")
        if self.dry:
            return
        for _ in range(30):
            try:
                request = urllib.request.Request(url, headers={"Host": "localhost"})
                with urllib.request.urlopen(request, timeout=3) as response:
                    if "release_commit" in json.load(response):
                        return
            except (OSError, ValueError):
                time.sleep(1)
        raise SystemExit("Service did not become healthy: journalctl --user -u linko-bot-manager.service -n 80 --no-pager")

    def serve(self):
        status = self.probe(["tailscale", "serve", "status", "--json"])
        try:
            funnel = json.loads(status.stdout).get("AllowFunnel") or {}
        except (AttributeError, ValueError):
            funnel = {}
        if any(funnel.values()):
            raise SystemExit(f"Funnel is enabled; turn it off: tailscale funnel --https={self.port} off")
        command = ["tailscale", "serve", "--bg", f"--https={self.port}", f"http://127.0.0.1:{self.port}"]
        text = " ".join(command)
        if self.dry:
            self.say(f"HUMAN STEP if sudo needs a password: run `sudo {text}` (tailscale serve needs root)")
            self.say("run: " + text)
            return
        result = self.probe(command)
        if result is None or result.returncode:
            human(f"tailscale serve needs root. Run this exact command, then re-run the helper: sudo {text}")
        self.say("run: " + text)

    def finish(self):
        print(f"\nBot Boss is tailnet-only at: https://{self.hostname}:{self.port}/")
        print("Give this address to the owner. In each Bot Boss: menu > Machines > Add machine, then add the others' addresses.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    parser.add_argument("--hostname", help="Tailscale DNS name (default: asked from tailscale)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--username", default="owner")
    args = parser.parse_args(argv)
    home, env = Path.home(), dict(os.environ)
    installer = Installer(args, home, env)
    try:
        installer.prerequisites()
        installer.hostname = installer.hostname or installer.tailscale_host()
        installer.dependencies()
        installer.configuration()
        installer.owner()
        installer.service()
        installer.health()
        installer.serve()
    except Stop as stop:
        return stop.code
    installer.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())

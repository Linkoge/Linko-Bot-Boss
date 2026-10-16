"""Install only this repository's user service; preserve all other services."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def install_instance(repo, home, directory, codex, name, start):
    """The shared template unit plus one enabled instance; the instance's settings live in its own env file."""
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in name):
        raise SystemExit("Instance names use lowercase letters, digits and dashes")
    env = home / f".config/linko-bot-manager-{name}.env"
    if not env.is_file():
        raise SystemExit(f"Create {env} first (copy deploy/instance.env.example)")
    unit = (
        (repo / "deploy/linko-bot-manager@.service.in")
        .read_text()
        .replace("@REPO@", str(repo))
        .replace("@PYTHON@", str(repo / ".venv/bin/python"))
        .replace("@CODEX@", codex)
    )
    (directory / "linko-bot-manager@.service").write_text(unit)
    service = f"linko-bot-manager@{name}.service"
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", *(["--now"] if start else []), service], check=True)
    print(f"Installed {service}. Add it to the deploy list: python3 scripts/install_deploy.py --instance {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hostname")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--instance", help="Install and enable linko-bot-manager@NAME (a restricted work instance)")
    parser.add_argument("--replace-manager-unit", action="store_true", help="Replace this manager's reviewed unit only")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    home = Path.home()
    codex = shutil.which(os.getenv("AI_MANAGER_CODEX", "codex"))
    if not codex or any(ch in codex for ch in '\n\r"% '):
        raise SystemExit("Install Codex or set AI_MANAGER_CODEX to its absolute executable path")
    # This known repository has no spaces. Refuse ambiguous systemd path interpolation.
    if any(ch in str(repo) + str(home) for ch in '\n\r"%') or " " in str(repo) + str(home):
        raise SystemExit("Use a repository/home path without spaces or systemd escape characters")
    directory = home / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    if args.instance:
        install_instance(repo, home, directory, codex, args.instance, args.start)
        return
    hostname = args.hostname
    if not hostname:
        status = json.loads(
            subprocess.run(["tailscale", "status", "--json"], check=True, capture_output=True, text=True).stdout
        )
        hostname = status["Self"]["DNSName"].rstrip(".")
    if not hostname or any(
        ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in hostname
    ):
        raise SystemExit("Provide a valid private hostname")
    unit = (
        (repo / "deploy/linko-bot-manager.service.in")
        .read_text()
        .replace("@REPO@", str(repo))
        .replace("@PYTHON@", str(repo / ".venv/bin/python"))
        .replace("@HOME@", str(home))
        .replace("@HOST@", hostname)
        .replace("@CODEX@", codex)
    )
    destination = directory / "linko-bot-manager.service"
    if destination.exists() and destination.read_text() != unit and not args.replace_manager_unit:
        raise SystemExit("Existing unit differs; review it before replacing")
    destination.write_text(unit)
    for name in ("linko-bot-manager-backup.service", "linko-bot-manager-backup.timer"):
        template = (
            (repo / "deploy" / (name + ".in"))
            .read_text()
            .replace("@REPO@", str(repo))
            .replace("@PYTHON@", str(repo / ".venv/bin/python"))
            .replace("@HOME@", str(home))
        )
        target = directory / name
        if target.exists() and target.read_text() != template:
            raise SystemExit("Existing backup unit differs; review before replacement")
        target.write_text(template)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "linko-bot-manager-backup.timer"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "linko-bot-manager.service"], check=True)
    if args.start:
        subprocess.run(["systemctl", "--user", "start", "linko-bot-manager.service"], check=True)
    print("Installed dedicated private manager user service")


if __name__ == "__main__":
    main()

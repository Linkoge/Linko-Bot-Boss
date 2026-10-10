"""Package already-installed runtime/dependencies for offline isolated verification.

Run only through the trusted local owner administration channel. No downloads.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from ai_manager.config import Settings
from ai_manager.db import Database
from ai_manager.verification_runtime import tree_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    boundary = settings.data_dir / "verification-runtimes" / args.project_id
    if args.destination.exists() or not args.destination.resolve().is_relative_to(boundary.resolve()):
        raise SystemExit("Use a new destination inside the assigned project runtime directory")
    base = Path(sys.base_prefix)
    site = Path(sys.prefix) / "lib" / ("python" + ".".join(map(str, sys.version_info[:2]))) / "site-packages"
    args.destination.mkdir(parents=True, mode=0o700)
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter", "playwright", "playwright-*.dist-info"
    )
    shutil.copytree(base, args.destination / "python", symlinks=True, ignore=ignore)
    runtime_site = (
        args.destination / "python" / "lib" / ("python" + ".".join(map(str, sys.version_info[:2]))) / "site-packages"
    )
    runtime_site.mkdir(exist_ok=True)
    shutil.copytree(site, runtime_site, symlinks=True, dirs_exist_ok=True, ignore=ignore)
    ruff = Path(sys.prefix) / "bin" / "ruff"
    if ruff.is_file():
        shutil.copy2(ruff, args.destination / "python" / "bin" / "ruff")
    identity = tree_identity(args.destination)
    record = {
        "path": str(args.destination),
        "sha256": identity,
        "python": "python" + ".".join(map(str, sys.version_info[:2])),
        "authorization": "Owner approved manager verification/runtime blocker resolution in Codex chat 2026-10-10",
        "offline": True,
        "network": False,
        "host_home": False,
    }
    if not args.build_only:
        db = Database(settings.db_path)
        if not db.one("SELECT id FROM projects WHERE id=?", (args.project_id,)):
            raise ValueError("Runtime must belong to a registered project")
        db.set_setting("verification_runtime:" + args.project_id, record)
        with db.transaction() as conn:
            db.event(conn, "verification.runtime_authorized", record, args.project_id)
    # Protect the trusted package; workers only receive a scoped copy.
    for path in args.destination.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o500 if path.is_dir() or path.stat().st_mode & 0o111 else 0o400)
    args.destination.chmod(0o500)
    print(json.dumps({k: v for k, v in record.items() if k != "path"}, indent=2))


if __name__ == "__main__":
    main()

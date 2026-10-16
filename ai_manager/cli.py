import argparse
import getpass
import json
from pathlib import Path

from .config import Settings
from .db import Database
from .registry import Registry


def main():
    parser = argparse.ArgumentParser(description="Linko Bot Boss local administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    commands.add_parser("overview")
    add = commands.add_parser("add")
    add.add_argument("path")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("project_id")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    commands.add_parser("setup-owner")
    commands.add_parser("revoke-sessions")
    commands.add_parser("serve")
    args = parser.parse_args()
    settings = Settings()
    settings.prepare()
    if args.command == "restore":
        Database.restore(args.source, args.destination)
        return
    db = Database(settings.db_path)
    registry = Registry(db, settings.project_roots, settings.fixed_project)
    if args.command == "discover":
        print(json.dumps(registry.discover(), indent=2))
    elif args.command == "overview":
        for project in registry.list():
            print(
                f"{project['id'][:8]}  {project['status']:8}  {project['priority']:3}  {project['name']}  {project['path']}"
            )
    elif args.command == "add":
        print(json.dumps(registry.add(args.path), indent=2))
    elif args.command == "reconcile":
        print(json.dumps(registry.reconcile(args.project_id), indent=2))
    elif args.command == "backup":
        db.backup(args.destination)
        print("Verified backup created")
    elif args.command == "setup-owner":
        from .auth import Auth

        username = input("Owner username: ").strip()
        password = getpass.getpass("Password (at least 12 characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords differ")
        Auth(db, settings).setup(username, password)
        print("Owner account created; web signup is disabled")
    elif args.command == "revoke-sessions":
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions")
            db.event(conn, "owner.sessions_revoked", {})
        print("All owner sessions revoked")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run(
            "ai_manager.web:create_app", factory=True, host="127.0.0.1", port=settings.port, workers=1, proxy_headers=False
        )


if __name__ == "__main__":
    main()

"""Dated, integrity-checked local database backup. Never removes old backups."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_manager.attachments import backup_attachments
from ai_manager.config import Settings
from ai_manager.db import Database

if __name__ == "__main__":
    settings = Settings()
    settings.prepare()
    destination = (
        settings.data_dir
        / "backups"
        / ("manager-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
    )
    Database(settings.db_path).backup(destination)
    count = backup_attachments(settings.data_dir, destination, destination.with_suffix('.attachments'))
    print(f"Integrity-checked private database backup and {count} checksum-verified attachments created")

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("AI_MANAGER_DATA", "~/.local/share/linko-bot-manager")).expanduser())
    project_roots: tuple[Path, ...] = field(default_factory=lambda: tuple(Path(p).expanduser() for p in os.getenv("AI_MANAGER_PROJECT_ROOTS", "~/projects").split(os.pathsep)))
    codex_bin: str = field(default_factory=lambda: os.getenv("AI_MANAGER_CODEX", "codex"))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("AI_MANAGER_SECURE_COOKIE", "1") == "1")
    session_seconds: int = 43200
    max_workers: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "manager.sqlite3"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)

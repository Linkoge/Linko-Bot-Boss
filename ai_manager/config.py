import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("AI_MANAGER_DATA", "~/.local/share/linko-bot-manager")).expanduser()
    )
    project_roots: tuple[Path, ...] = field(
        default_factory=lambda: tuple(
            Path(p).expanduser() for p in os.getenv("AI_MANAGER_PROJECT_ROOTS", "~/projects").split(os.pathsep)
        )
    )
    codex_bin: str = field(default_factory=lambda: os.getenv("AI_MANAGER_CODEX", "codex"))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("AI_MANAGER_SECURE_COOKIE", "1") == "1")
    # Audio-only external provider; secrets never go through SQLite/browser settings.
    xai_api_key: str = field(default_factory=lambda: os.getenv("XAI_API_KEY", ""), repr=False)
    audio_provider: str = field(default_factory=lambda: os.getenv("AI_MANAGER_AUDIO_PROVIDER", "grok"))
    audio_model: str = field(default_factory=lambda: os.getenv("AI_MANAGER_AUDIO_MODEL", "grok-voice-transcribe-2.0"))
    audio_probe: str = field(default_factory=lambda: os.getenv("AI_MANAGER_AUDIO_PROBE", "ffprobe"))
    session_seconds: int = 43200
    max_workers: int = 2
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            os.getenv("AI_MANAGER_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "manager.sqlite3"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)

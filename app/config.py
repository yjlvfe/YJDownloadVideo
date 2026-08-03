import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/yjdownload.db"
DEFAULT_BASE_URL = "http://localhost:8000"
VERCEL_RUNTIME = os.getenv("VERCEL") == "1"


class Settings(BaseSettings):
    app_name: str = "YJDownloadVideo"
    environment: str = "development"
    secret_key: str = "change-me"
    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = DEFAULT_BASE_URL
    storage_dir: str = "./storage"
    file_ttl_seconds: int = 0
    max_file_size_bytes: int = 2_000_000_000
    max_duration_seconds: int = 7200
    rate_limit_per_minute: int = 10
    telegram_bot_token: str = ""
    telegram_bot_api_base: str = ""
    telegram_webhook_url: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    sessions_dir: str = "./data/sessions"
    telegram_send_limit_bytes: int = 2_000_000_000
    max_active_jobs_per_ip: int = 50
    max_active_jobs_per_telegram_user: int = 50
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    vercel_mode: bool = False
    browser_bin: str = "chromium-browser"
    enable_browser_capture: bool = True
    request_timeout_seconds: int = 30
    cookies_dir: str = "./data/cookies"
    status_dir: str = "./data/status"
    cleanup_interval_seconds: int = 300
    secure_cookies: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8000"
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        if not VERCEL_RUNTIME:
            return
        self.vercel_mode = True
        runtime_root = Path("/tmp/yjdownload")
        if self.database_url == DEFAULT_DATABASE_URL:
            self.database_url = f"sqlite+aiosqlite:///{runtime_root / 'data' / 'yjdownload.db'}"
        for field_name, subdirectory in {
            "storage_dir": "storage",
            "sessions_dir": "sessions",
            "cookies_dir": "cookies",
            "status_dir": "status",
        }.items():
            configured = Path(getattr(self, field_name))
            if not configured.is_absolute():
                setattr(self, field_name, str(runtime_root / subdirectory))
        production_host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL")
        if production_host:
            self.base_url = f"https://{production_host.strip('/')}"
        self.enable_browser_capture = False

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        raw = self.database_url.split("///")[-1]
        path = Path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cookies_path(self) -> Path:
        path = Path(self.cookies_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def status_path(self) -> Path:
        path = Path(self.status_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sessions_path(self) -> Path:
        path = Path(self.sessions_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


def ffmpeg_location() -> str:
    import shutil
    if settings.ffmpeg_bin and shutil.which(settings.ffmpeg_bin):
        return settings.ffmpeg_bin
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return settings.ffmpeg_bin


settings = Settings()
settings.storage_path
settings.db_path

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/yjdownload.db"
VERCEL_RUNTIME = os.getenv("VERCEL") == "1"


class Settings(BaseSettings):
    app_name: str = "YJDownloadVideo"
    environment: str = "development"
    secret_key: str = "change-me"
    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = "http://localhost:8000"
    storage_dir: str = "./storage"
    file_ttl_seconds: int = 3600
    max_file_size_bytes: int = 2 * 1024 * 1024 * 1024
    max_duration_seconds: int = 7200
    rate_limit_per_minute: int = 10
    telegram_bot_token: str = ""
    telegram_bot_api_base: str = ""  # e.g. http://tba:8081 — خادم Bot API محلي (يرفع حد الإرسال إلى 2GB)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    sessions_dir: str = "./data/sessions"
    telegram_send_limit_bytes: int = 50 * 1024 * 1024
    max_active_jobs_per_ip: int = 50
    max_active_jobs_per_telegram_user: int = 50
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    vercel_mode: bool = False
    browser_bin: str = "chromium"
    enable_browser_capture: bool = True
    request_timeout_seconds: int = 30
    cookies_dir: str = "./data/cookies"
    status_dir: str = "./data/status"
    cleanup_interval_seconds: int = 300
    secure_cookies: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8000"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def model_post_init(self, __context) -> None:
        """Apply safe defaults for Vercel's read-only serverless filesystem."""
        if not VERCEL_RUNTIME:
            return

        self.vercel_mode = True
        runtime_root = Path("/tmp/yjdownload")

        # SQLite and downloaded files must live in Vercel's writable /tmp area.
        if self.database_url == DEFAULT_DATABASE_URL:
            self.database_url = f"sqlite+aiosqlite:///{runtime_root / 'data' / 'yjdownload.db'}"

        runtime_paths = {
            "storage_dir": "storage",
            "sessions_dir": "sessions",
            "cookies_dir": "cookies",
            "status_dir": "status",
        }
        for field_name, subdirectory in runtime_paths.items():
            configured = Path(getattr(self, field_name))
            if not configured.is_absolute():
                setattr(self, field_name, str(runtime_root / subdirectory))

        # Chromium is not bundled in the Vercel Python runtime.
        self.enable_browser_capture = False

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        raw = self.database_url.split("///")[-1]
        p = Path(raw)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cookies_path(self) -> Path:
        p = Path(self.cookies_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def status_path(self) -> Path:
        p = Path(self.status_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sessions_path(self) -> Path:
        p = Path(self.sessions_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


def ffmpeg_location() -> str:
    """مسار ffmpeg: الثنائي النظامي أولًا، ثم ثنائي imageio-ffmpeg المدمج (يعمل على Vercel)."""
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

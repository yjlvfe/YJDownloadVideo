import base64
import binascii
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/yjdownload.db"
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_REMOTE_WORKER_URL = "https://yjvideodownloads.onrender.com"
VERCEL_RUNTIME = os.getenv("VERCEL") == "1"


def _deployment_git_sha() -> str:
    return str(
        os.getenv("GIT_SHA")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("VERCEL_GIT_COMMIT_SHA")
        or "unknown"
    )


class Settings(BaseSettings):
    app_name: str = "YJDownloadVideo"
    environment: str = "development"
    git_sha: str = Field(default_factory=_deployment_git_sha)
    secret_key: str = Field(default="change-me", repr=False)
    worker_shared_secret: str = Field(default="", repr=False)
    admin_api_token: str = Field(default="", repr=False)
    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = DEFAULT_BASE_URL
    remote_worker_url: str = DEFAULT_REMOTE_WORKER_URL
    storage_dir: str = "./storage"
    file_ttl_seconds: int = 3600
    max_file_size_bytes: int = 2_000_000_000
    max_duration_seconds: int = 7200
    rate_limit_per_minute: int = 10
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_bot_api_base: str = ""
    telegram_webhook_url: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = Field(default="", repr=False)
    sessions_dir: str = "./data/sessions"
    telegram_send_limit_bytes: int = 2_000_000_000
    max_active_jobs_per_ip: int = 2
    max_active_jobs_per_telegram_user: int = 2
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    youtube_proxy_url: str = ""
    enable_public_youtube_fallback: bool = False
    youtube_circuit_cooldown_seconds: int = 900
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
        if self.environment.strip().lower() in {"production", "prod"} and self.secret_key == "change-me":
            raise ValueError("SECRET_KEY must be configured in production")

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
    discovered = shutil.which(settings.ffmpeg_bin)
    if discovered:
        return discovered
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return settings.ffmpeg_bin


def javascript_runtime_options() -> dict:
    """Return yt-dlp's Python API configuration for the best installed JS runtime."""
    if shutil.which("deno"):
        return {"js_runtimes": {"deno": {}}}
    if shutil.which("node"):
        return {"js_runtimes": {"node": {}}}
    if shutil.which("qjs"):
        return {"js_runtimes": {"quickjs": {}}}
    return {}


def _materialize_cookie_secret(env_name: str, filename: str) -> str | None:
    encoded = os.getenv(env_name, "").strip()
    if not encoded:
        return None
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"{env_name} is not valid base64") from exc
    if not payload.strip():
        return None

    target = settings.cookies_path / filename
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    return str(target)


def cookie_file_for_url(url: str) -> str | None:
    """Resolve an optional Netscape cookie file without committing credentials to Git."""
    host = (urlparse(url).hostname or "").lower()
    if host == "youtu.be" or host.endswith("youtube.com"):
        platform = ("YOUTUBE_COOKIES_FILE", "YOUTUBE_COOKIES_B64", "youtube.txt")
    elif host.endswith("instagram.com"):
        platform = ("INSTAGRAM_COOKIES_FILE", "INSTAGRAM_COOKIES_B64", "instagram.txt")
    else:
        platform = ("", "", "")

    explicit_name, secret_name, filename = platform
    if explicit_name:
        explicit = os.getenv(explicit_name, "").strip()
        if explicit and Path(explicit).is_file():
            return explicit
        materialized = _materialize_cookie_secret(secret_name, filename)
        if materialized:
            return materialized
        platform_file = settings.cookies_path / filename
        if platform_file.is_file():
            return str(platform_file)

    global_explicit = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if global_explicit and Path(global_explicit).is_file():
        return global_explicit
    global_materialized = _materialize_cookie_secret("YTDLP_COOKIES_B64", "cookies.txt")
    if global_materialized:
        return global_materialized
    fallback = settings.cookies_path / "cookies.txt"
    return str(fallback) if fallback.is_file() else None


settings = Settings()
settings.storage_path
settings.db_path

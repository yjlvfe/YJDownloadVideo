from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    app_name: str = "YJDownloadVideo"
    environment: str = "development"
    secret_key: str = ""  # إجباري — الإقلاع يُرفض بدونه (لا "change-me" بعد الآن)
    database_url: str = "sqlite+aiosqlite:///./data/yjdownload.db"
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = "http://localhost:8000"
    storage_dir: str = "./storage"
    ram_store_dir: str = "./ramstore"  # tmpfs RAM — للملفات ≤2GB (سريع جداً)
    ram_threshold_bytes: int = 2 * 1024 * 1024 * 1024  # 2GB = حد تيليجرام — فوقها HDD
    file_ttl_seconds: int = 86400  # 24 ساعة — يطابق README و cleanup
    max_file_size_bytes: int = 20 * 1024 * 1024 * 1024  # 20GB — الملفات فوق حد تيليجرام (2GB) تُعطى رابط تحميل بدل الإرسال
    max_duration_seconds: int = 0  # 0 = غير محدودة إطلاقًا (قرار المستخدم 2026-08)
    rate_limit_per_minute: int = 10
    telegram_bot_token: str = ""
    telegram_bot_api_base: str = ""  # e.g. http://tba:8081 — خادم Bot API محلي (يرفع حد الإرسال إلى 2GB)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    sessions_dir: str = "./data/sessions"
    telegram_send_limit_bytes: int = 2097152000  # 2GB — يطابق خادم Bot API المحلي
    max_active_jobs_per_ip: int = 50
    max_active_jobs_per_telegram_user: int = 50
    ytdlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    vercel_mode: bool = False  # يُفعَّل تلقائيًا على Vercel (VERCEL=1)
    browser_bin: str = "chromium"
    enable_browser_capture: bool = False  # معطّل افتراضيًا — بساطة + تقليل طلبات يوتيوب
    request_timeout_seconds: int = 30
    cookies_dir: str = "./data/cookies"
    status_dir: str = "./data/status"
    cleanup_interval_seconds: int = 300
    secure_cookies: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8000"
    admin_secret: str = ""  # إن ضُبط: يحرس /api/telegram/status
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def ram_store_path(self) -> Path:
        p = Path(self.ram_store_dir); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def db_path(self) -> Path:
        raw = self.database_url.split("///")[-1]
        p = Path(raw); p.parent.mkdir(parents=True, exist_ok=True); return p

    @property
    def cookies_path(self) -> Path:
        p = Path(self.cookies_dir); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def status_path(self) -> Path:
        p = Path(self.status_dir); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def sessions_path(self) -> Path:
        p = Path(self.sessions_dir); p.mkdir(parents=True, exist_ok=True); return p


def ffmpeg_location() -> str:
    """مسار ffmpeg المطلق: ثنائي النظام أولًا، ثم ثنائي imageio-ffmpeg المدمج.

    ⚠️ يجب أن يكون مسارًا مطلقًا — yt-dlp لا يحل الأسماء المجردة،
    وتمرير 'ffmpeg' يجعل كل دمج (merge) يفشل بـ "ffmpeg is not installed".
    """
    import shutil
    if settings.ffmpeg_bin:
        resolved = shutil.which(settings.ffmpeg_bin)
        if resolved:
            return resolved
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return settings.ffmpeg_bin

settings = Settings()
settings.storage_path
settings.db_path

# 🛡️ Fail-fast: ممنوع الإقلاع بسر ضعيف/مفترض — كان "change-me" يسمح بتزوير توكنات التحميل
_WEAK_KEYS = {"", "change-me", "change-this-to-a-long-random-secret",
              "change-this-to-a-long-random-string"}
if settings.secret_key.strip() in _WEAK_KEYS:
    raise RuntimeError(
        "SECRET_KEY غير مضبوط في .env — ولّد قيمة عشوائية طويلة ثم أعد التشغيل. "
        "الإقلاع مرفوض عمدًا (أمان).")

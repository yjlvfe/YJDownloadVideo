"""
app/extractor.py — محرّك تحميل الفيديو
=====================================
أي رابط → yt-dlp → ملف. مسار واحد فقط (بساطة = أمان — لا أنماط طلبات متعددة تثير شك يوتيوب).
"""

from __future__ import annotations


import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import httpx

from .config import ffmpeg_location, settings
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
class DownloadError(Exception):
    """فشل استخراج/تحميل الفيديو من الرابط المعطى."""

# أخطاء شبكة لحظية فقط (ومضة بعد تبديل الـ IP / انقطاع ثوانٍ) — تُعاد المحاولة نفسها بالضبط
_TRANSIENT_RE = re.compile(
    r"connection refused|connection reset|connection aborted"
    r"|\[errno\s*(?:111|104|32|110)\]"
    r"|timed?\s?-?out|temporary failure",
    re.IGNORECASE,
)


def _is_transient_error(err: Exception) -> bool:
    """True فقط للأخطاء اللحظية (رفض اتصال/إعادة ضبط/مهلة).
    أخطاء يوتيوب المنطقية (403/Sign-in/bot/فيديو غير متاح) ليست لحظية → فشل فوري بلا إعادة،
    حتى نبقى ضمن قاعدة «محاولة واحدة» ولا نضيف أنماط طلبات تثير شك يوتيوب."""
    return bool(_TRANSIENT_RE.search(str(err)))


def _ytdlp_headers() -> dict:
    """هيدرات المتصفح لطلبات yt-dlp — بدون Accept-Encoding:
    ترويسة br اليدوية تكسر استخراج player response في يوتيوب (yt-dlp يدير التشفير بنفسه)."""
    h = dict(BROWSER_HEADERS)
    h.pop("Accept-Encoding", None)
    return h
def _clean_youtube_url(url: str) -> str:
    """ينظف رابط يوتيوب من معاملات القوائم والتشغيل التلقائي (تخرب الفحص)."""
    import re
    # احتفظ فقط بـ v= (معرف الفيديو)
    m = re.search(r"(?:v=|shorts/|youtu\.be/|embed/|live/)([A-Za-z0-9_-]{11})", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url
class UniversalExtractor:
    """المحرك الشامل: أي رابط ← فيديو ← ملف."""

    def __init__(self, cookies_file: str | None = None, use_browser: bool | None = None,
                 progress_hook=None, max_filesize: int = 0, max_duration: int = 0,
                 browser_bin: str = "chromium", request_timeout: int = 30,
                 referer_headers: bool = True):
        self.cookies_file = cookies_file
        # None = اتبع الإعداد العام (enable_browser_capture) — المتصفح معطّل افتراضيًا
        # (أسباب: ذاكرة عالية + طلبات إضافية تسرّع رفع علم الروبوت على يوتيوب)
        self.use_browser = settings.enable_browser_capture if use_browser is None else use_browser
        self.progress_hook = progress_hook
        self.max_filesize = max_filesize
        self.max_duration = max_duration
        self.browser_bin = browser_bin
        self.request_timeout = request_timeout
        self.referer_headers = referer_headers
        self._cookies_tmp: list[str] = []

    # ------------------------------------------------------------------
    # واجهة عامة
    # ------------------------------------------------------------------
    def download(self, url: str, outdir: Path, preferred: str = "best",
                 title_hint: str = "") -> dict:
        """ينزّل الفيديو — مسار واحد: yt-dlp مع كوكيز (نفس متصفح المستخدم بالضبط).

        البساطة = أمان: مسار واحد فقط — yt-dlp مع بروكسي/كوكيز، بلا أنماط طلبات متعددة.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        return self._download_ytdlp(url, outdir, preferred)

    def inspect(self, url: str) -> dict:
        """معاينة سريعة لأي رابط: yt-dlp أولاً (قائمة جودات حقيقية)، ثم المسح/المتصفح.

        يُرجع: {source, title, thumbnail, duration, formats: [{id,label,size,height}], audio_size}
        ⚠️ الجودات دائمًا حقيقية من المقطع نفسه — ممنوع أي جودات افتراضية/ثابتة.
        الحجم = حجم الفيديو + أفضل تدفق صوتي (تقدير الحجم النهائي للملف المدمج).
        """
        try:
            info = self._ytdlp_info(url)
            raw = info.get("formats") or []
            # أفضل تدفق صوتي منفصل — يُضاف لحجم أي دقة فيديو (الملف النهائي مدمج)
            audio_size = None
            for f in raw:
                if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
                    sz = f.get("filesize") or f.get("filesize_approx")
                    if sz and (audio_size is None or sz > audio_size):
                        audio_size = sz
            # دقة واحدة لكل ارتفاع، مع تفضيل mp4/h264 (توافق أوسع) ثم الأكبر حجمًا
            best_per_height: dict[int, dict] = {}
            for f in raw:
                if f.get("vcodec") == "none" or not f.get("height"):
                    continue
                size = f.get("filesize") or f.get("filesize_approx")
                has_audio = f.get("acodec") not in (None, "none")
                est = size
                if size and not has_audio and audio_size:
                    est = size + audio_size  # فيديو منفصل + صوت = الحجم الحقيقي للملف
                if self.max_filesize and est and est > self.max_filesize:
                    continue
                h = int(f["height"])
                score = (0 if (f.get("ext") == "mp4"
                               and str(f.get("vcodec") or "").startswith("avc1"))
                         else (1 if f.get("ext") == "mp4" else 2))
                cur = best_per_height.get(h)
                if cur is None or score < cur["_score"] or (
                        score == cur["_score"] and (est or 0) > (cur.get("size") or 0)):
                    best_per_height[h] = {
                        "id": str(h), "height": h,
                        "label": f'{h}p / {f.get("ext", "mp4")}',
                        "size": est, "_score": score,
                    }
            formats = []
            for h in sorted(best_per_height, reverse=True):
                entry = dict(best_per_height[h])
                entry.pop("_score", None)
                formats.append(entry)
            return {"source": "ytdlp", "title": info.get("title"),
                    "thumbnail": info.get("thumbnail"), "duration": info.get("duration"),
                    "formats": formats, "audio_size": audio_size}
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("extractor").warning("inspect yt-dlp failed for %s: %s", url, str(e)[:200])
        # 🚀 بساطة: لا فحص HTML/متصفح إضافي (نمط طلبات يفضحنا) — نرجع فارغًا
        return {"source": "ytdlp", "title": "", "thumbnail": "", "duration": None,
                "formats": [], "audio_size": None,
                "_inspect_error": "تعذر تحديد الجودات — سيُحمَّل الأفضل المتوفر."}

    # ------------------------------------------------------------------
    # L1 — yt-dlp
    # ------------------------------------------------------------------

    def _base_ytdlp_opts(self, outdir: Path | str, preferred: str = "best") -> dict:
        fmt_map = {
            # ⚠️ bestvideo (بدون *) = فيديو فقط — يجبر الدمج مع الصوت.
            # bestvideo* كان يثق بالصيغ التي تدّعي وجود صوت (كذبة تيك توك HEVC)
            # وينتج ملفات فيديو بلا صوت، ثم يلجأ لخطة طوارئ بجودة أقل.
            "best": "bestvideo+bestaudio/best",
            "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "audio": "bestaudio/best",
        }

        def _fmt_for(pref: str) -> str:
            # أي دقة رقمية (من الفحص الفعلي للمقطع) تُترجم ديناميكيًا
            if pref in ("best", "audio"):
                return fmt_map[pref]
            if pref.isdigit():
                return f"bestvideo[height<={pref}]+bestaudio/best[height<={pref}]"
            return fmt_map.get(pref, pref or "best")

        fmt = _fmt_for(preferred)
        opts = {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": str(Path(outdir) / "%(title).120s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # لا تستخدم restrictfilenames: يمسح الحروف العربية/يونيكود من أسماء الملفات
            "restrictfilenames": False,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "http_headers": _ytdlp_headers(),
            "format_sort": ["res", "ext:mp4:m4a", "vcodec:h264"],
            "ffmpeg_location": ffmpeg_location(),
            # 🚀 تسريع: 16 اتصال متوازي للأجزاء (يكسر خنق يوتيوب لـ IP السيرفرات)
            # يوتيوب يخنق كل اتصال مفرد ~3MB/s → 16 اتصال = حتى ~50MB/s
            "concurrent_fragment_downloads": 16,
            # ⚠️ لا extractor_args لعميل android — يجبر يوتيوب على إرجاع تنسيق واحد فقط (360p)
            # ويوقف دمج العملاء الآخرين. العميل الافتراضي (web) يعيد كل الجودات حتى 4K/8K.
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if getattr(settings, "ytdlp_proxy", ""):
            opts["proxy"] = settings.ytdlp_proxy
        if self.max_filesize:
            opts["max_filesize"] = self.max_filesize
        if self.progress_hook:
            opts["progress_hooks"] = [self.progress_hook]
        if preferred == "audio":
            # بعض المواقع (يوتيوب بعميل android) لا توفر صوتًا منفصلًا — استخرج الصوت من الملف
            opts["merge_output_format"] = "m4a"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192",
            }]
        return opts
    def _ytdlp_info(self, url: str) -> dict:
        from yt_dlp import YoutubeDL
        # 🧹 نظف معاملات القوائم من روابط يوتيوب (تخرب الفحص)
        clean_url = _clean_youtube_url(url) if self._is_youtube(url) else url
        opts = {
            "quiet": True, "skip_download": True, "noplaylist": True,
            "no_warnings": True, "socket_timeout": 30,
            "http_headers": _ytdlp_headers(),
        }
        if getattr(settings, "ytdlp_proxy", ""):
            opts["proxy"] = settings.ytdlp_proxy
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        
        # محاولة أساسية + إعادتان لنفس الطلب حرفيًا عند الأخطاء الشبكية اللحظية فقط
        # (ومضة بعد تبديل الـ IP). نفس البصمة تمامًا = لا نمط طلبات جديد لليوتيوب.
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(clean_url, download=False)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2 and _is_transient_error(e):
                    time.sleep(2)
                    continue
                raise
        else:
            raise DownloadError(f"الفحص فشل بعد 3 محاولات: {str(last_err)[:200]}")
        
        if not info:
            raise DownloadError("yt-dlp لم يُرجع بيانات")
        if info.get("is_live"):
            raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
        return info

    @staticmethod
    def _is_youtube(url: str) -> bool:
        u = url.lower()
        return "youtube.com" in u or "youtu.be" in u

    def _download_ytdlp(self, url: str, outdir: Path, preferred: str) -> dict:
        from yt_dlp import YoutubeDL
        # محاولة أساسية + إعادتان لنفس الطلب حرفيًا عند الأخطاء الشبكية اللحظية فقط
        opts = self._base_ytdlp_opts(outdir, preferred)
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2 and _is_transient_error(e):
                    time.sleep(2)
                    continue
                raise
        else:
            raise DownloadError(f"التحميل فشل بعد 3 محاولات: {str(last_err)[:200]}")
        files = [p for p in outdir.iterdir() if p.is_file()]
        if not files:
            raise DownloadError("لم يُنشأ ملف")
        path = max(files, key=lambda p: p.stat().st_mtime)

        if path is None or not path.is_file():
            raise DownloadError("لم يُنشأ ملف")
        if not _has_audio(path):
            raise DownloadError("المقطع المتاح بدون صوت (لا يوجد تدفق صوتي في المصدر)")
        if preferred != "audio" and not _has_video(path):
            # يمنع إرسال ملف صوتي كمقطع فيديو فاضي (مشكلة إنستغرام)
            raise DownloadError("المقطع المتاح بدون فيديو (لا يوجد تدفق فيديو في المصدر)")
        # نظّف أي ملفات يتيمة في مجلد المهمة (بقايا محاولات فاشلة)
        for p in outdir.iterdir():
            if p.is_file() and p != path:
                try:
                    p.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

        if self.max_duration and info.get("duration") and info["duration"] > self.max_duration:
            raise DownloadError("مدة الفيديو تتجاوز الحد المسموح")
        if self.max_filesize and path.stat().st_size > self.max_filesize:
            raise DownloadError("حجم الفيديو يتجاوز الحد المسموح")
        return {"path": str(path), "title": info.get("title") or "",
                "duration": info.get("duration"), "thumbnail": info.get("thumbnail") or "",
                "ext": path.suffix.lstrip(".")}
    def cleanup(self):
        """يحذف ملفات الكوكيز المؤقتة."""
        for p in self._cookies_tmp:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self._cookies_tmp.clear()
# ---------------------------------------------------------------------------
# دوال مساعدة عامة
# ---------------------------------------------------------------------------
def _has_audio(path) -> bool:
    """يفحص وجود تدفق صوتي في الملف عبر ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return bool(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return True  # عند عدم توفر ffprobe لا نمنع التحميل


def _has_video(path) -> bool:
    """يفحص وجود تدفق فيديو في الملف عبر ffprobe (يمنع إرسال ملفات صوتية كمقاطع فاضية)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return bool(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return True

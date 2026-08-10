"""
app/extractor.py — Universal Video Extractor Engine
===================================================
أي رابط في العالم → اعثر على الفيديو → نزّله.

سلسلة التراجع (كل طبقة تحاول، وإذا فشلت تنتقل للتالية):
  L1  yt-dlp              : 2000+ موقع معروف + الماسح العام (generic)
  L2  HTML media scan     : <video>/<source>، og:video، JSON-LD، كتل إعداد المشغّل في JS، روابط مباشرة
  L3  Headless browser    : التقاط شبكة CDP — يمسك مشغّلات JS فقط والروابط المولّدة وقت التشغيل،
                            مع سحب كوكيز جلسة المتصفح تلقائيًا لتمريرها للتحميل.

ملاحظة: البث المشفّر بالكامل (DRM مثل Netflix/Prime) لا يمكن فكّه بدون كسر التشفير نفسه،
وهذا غير مدعوم هنا. كل ما عدا ذلك — صفحات عامة، سوشيال ميديا، روابط مباشرة، HLS/DASH،
مواقع بمشغّلات JS معقدة — مغطى.
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
from .security import is_private_host

# ---------------------------------------------------------------------------
# ثوابت
# ---------------------------------------------------------------------------

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

MEDIA_EXT_RE = re.compile(
    r"\.(mp4|webm|mkv|mov|m4v|avi|flv|m3u8|mpd|ts)(?=$|[?#])", re.I
)
DIRECT_VIDEO_RE = re.compile(
    r"""https?://[^\s"'<>\\]+?\.(?:mp4|webm|mkv|mov|m4v|avi|flv)(?:$|[?#][^\s"'<>\\]*)""",
    re.I,
)
MANIFEST_RE = re.compile(
    r"""https?://[^\s"'<>\\]+?\.(?:m3u8|mpd)(?:$|[?#][^\s"'<>\\]*)""", re.I
)

AD_DOMAINS = (
    "doubleclick.net", "googlesyndication.com", "scorecardresearch.com",
    "moatads.com", "adservice.google", "adsystem.com", "taboola.com",
    "outbrain.com", "criteo.com", "rubiconproject.com", "pubmatic.com",
    "openx.net", "amazon-adsystem.com", "facebook.com/tr", "google-analytics.com",
    "googletagmanager.com", "hotjar.com", "mixpanel.com", "segment.io",
)

VIDEO_MIME_PREFIXES = ("video/", "application/vnd.apple.mpegurl",
                       "application/x-mpegurl", "application/dash+xml",
                       "application/vnd.apple.mpegurlaudio",
                       "application/octet-stream")

# أنماط كتل إعداد المشغّل الشائعة في JavaScript
PLAYER_KEY_PATTERNS = [
    re.compile(r'''"file"\s*:\s*"([^"]+)"''', re.I),                       # JWPlayer
    re.compile(r'''"src"\s*:\s*"([^"]+\.(?:mp4|webm|m3u8|mpd)[^"]*)"''', re.I),  # Video.js/Plyr
    re.compile(r'''(?:playUrl|videoUrl|video_url|play_url|mediaUrl|media_url|streamUrl|downloadUrl)\s*[:=]\s*["']([^"']+)["']''', re.I),
    re.compile(r'''"url"\s*:\s*"([^"]+\.(?:mp4|webm|m3u8|mpd)(?:\?[^"]*)?)"''', re.I),
    re.compile(r'''"(?:mp4|hls|m3u8|dash|sd|hd|source)"\s*:\s*"([^"]+)"''', re.I),
    re.compile(r'''"hlsManifestUrl"\s*:\s*"([^"]+)"''', re.I),              # يوتيوب HLS المدمج
    re.compile(r'''"url"\s*:\s*"(https?:[^"]+)"''', re.I),                  # روابط CDN بلا امتداد (googlevideo…)
]

# أرقام itag المدمجة في يوتيوب (فيديو + صوت معًا) — تُفضَّل على التكييفية المنفصلة
COMBINED_ITAGS = {18, 22, 37, 59}


def _unescape_js(s: str) -> str:
    """يفك إخفاء الروابط داخل كتل JSON/JS: الشرطة المائلة المهرّبة ورموز unicode."""
    return s.replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")


class DownloadError(Exception):
    """فشل استخراج/تحميل الفيديو من الرابط المعطى."""


class MediaCandidate:
    """مرشح وسائط: رابط واحد يحتمل أن يكون الفيديو المطلوب."""

    __slots__ = ("url", "kind", "source", "title", "height", "size", "score", "cookies_file")

    def __init__(self, url: str, kind: str, source: str, title: str = "",
                 height: int = 0, size: int = 0, score: int = 0, cookies_file: str | None = None):
        self.url = url
        self.kind = kind          # direct | hls | dash
        self.source = source      # ytdlp | video-tag | meta | jsonld | js-blob | direct-regex | cdp
        self.title = title
        self.height = height
        self.size = size          # بايت (تقديري)
        self.score = score
        self.cookies_file = cookies_file

    def __repr__(self):
        return f"<MediaCandidate {self.kind} score={self.score} {self.url[:80]}>"


def _guess_kind(url: str) -> str:
    low = url.lower()
    if ".m3u8" in low:
        return "hls"
    if ".mpd" in low or "dash" in low:
        return "dash"
    return "direct"


def _is_ad_or_tracker(url: str) -> bool:
    low = url.lower()
    return any(d in low for d in AD_DOMAINS)


def _absolutize(base: str, url: str) -> str | None:
    url = url.strip().strip('"').strip("'")
    if not url or url.startswith(("data:", "blob:", "javascript:", "mailto:", "about:")):
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base, url)


def _ytdlp_headers() -> dict:
    """هيدرات المتصفح لطلبات yt-dlp — بدون Accept-Encoding:
    ترويسة br اليدوية تكسر استخراج player response في يوتيوب (yt-dlp يدير التشفير بنفسه)."""
    h = dict(BROWSER_HEADERS)
    h.pop("Accept-Encoding", None)
    return h


def _has_real_formats(info: dict) -> bool:
    """هل يحتوي الرد على صيغ وسائط فعلية (فيديو/صوت) أم storyboards فقط؟"""
    return any(
        (f.get("vcodec") or "none") != "none" or (f.get("acodec") or "none") != "none"
        for f in (info.get("formats") or [])
    )


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

    def discover(self, url: str, skip_ytdlp: bool = False) -> dict:
        """يكتشف الفيديو في الرابط ويعيد بياناته + قائمة مرشحين.

        يُرجع: {source, title, thumbnail, duration, candidates: [MediaCandidate]}
        يرمي DownloadError إذا لم يجد شيئًا.
        """
        errors: list[str] = []

        # L1: yt-dlp
        if skip_ytdlp:
            errors.append("yt-dlp: متخطى")
        else:
            try:
                info = self._ytdlp_info(url)
                if info and info.get("url") or (info and info.get("formats")):
                    return self._info_to_discovery(info)
            except Exception as e:  # noqa: BLE001
                errors.append(f"yt-dlp: {str(e)[:120]}")

        # L2: مسح HTML
        try:
            cands, meta = self._scan_html(url)
            if cands:
                return {"source": "html-scan", "title": meta.get("title", ""),
                        "thumbnail": meta.get("thumbnail", ""), "duration": None,
                        "candidates": cands}
        except Exception as e:  # noqa: BLE001
            errors.append(f"html-scan: {str(e)[:120]}")

        # L3: متصفح خفي
        if self.use_browser:
            try:
                cands = self._capture_browser(url)
                if cands:
                    return {"source": "browser", "title": cands[0].title or url,
                            "thumbnail": "", "duration": None, "candidates": cands}
            except Exception as e:  # noqa: BLE001
                errors.append(f"browser: {str(e)[:120]}")

        raise DownloadError("لم يُعثر على أي فيديو في هذا الرابط. " + " | ".join(errors[:3]))

    def _download_innertube(self, url: str, outdir: Path, preferred: str) -> dict:
        """مسار يوتيوب الشامل: youtubei.js + PO tokens داخليًا — كل الجودات بلا كوكيز.

        من IP سليم يعمل بكامل الجودات؛ من IP محجوب يفشل (لا روابط) فيعود
        المتصل للمسار العادي (yt-dlp ← android 360p) بسلاسة.
        """
        from .youtube_innertube import run_youtube_innertube, YouTubeInnertubeError
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            result = run_youtube_innertube(
                action="download",
                url=url,
                preferred=preferred,
                output_dir=outdir,
                max_bytes=self.max_filesize,
                timeout_seconds=900,
            )
        except YouTubeInnertubeError as exc:
            raise DownloadError(str(exc)) from exc
        path = Path(result["path"])
        if not _has_audio(path):
            path.unlink(missing_ok=True)
            raise DownloadError("المقطع المحمّل بلا صوت (YouTube.js)")
        if preferred != "audio" and not _has_video(path):
            path.unlink(missing_ok=True)
            raise DownloadError("المقطع المحمّل بلا فيديو (YouTube.js)")
        return {"path": str(path), "title": result.get("title") or "",
                "duration": result.get("duration"), "thumbnail": result.get("thumbnail") or "",
                "ext": result.get("ext") or path.suffix.lstrip(".")}

    def download(self, url: str, outdir: Path, preferred: str = "best",
                 title_hint: str = "") -> dict:
        """ينزّل الفيديو من أي رابط ويعيد {path,title,duration,thumbnail,ext}.

        التدفق (2026-08): يوتيوب ← YouTube.js الشامل أولًا (كل الجودات بلا كوكيز)
        ← yt-dlp نظيفة واحدة ← L4 للمرايا ← L2/L3 مرشح سليم ← android 360p.
        لا تعدد عملاء ولا محاولات متكررة — كل طلب إضافي يرفع علم الروبوت.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        # 🚀 يوتيوب: المسار الشامل أولًا (youtubei.js + bgutils PO tokens — يتجاوز خنق السيرفرات!)
        if self._is_youtube(url):
            try:
                from .youtube_innertube import innertube_available
                if innertube_available():
                    return self._download_innertube(url, outdir, preferred)
            except DownloadError:
                pass  # انزل للمسار العادي — لا نمنع التحميل أبدًا

        # L1: yt-dlp كامل (الأقوى: دمج جودة عالية + صوت)
        try:
            return self._download_ytdlp(url, outdir, preferred)
        except DownloadError as de:
            # يوتيوب محجوب من الخادم (فحص روبوت)؟ جرّب الخوادم البديلة قبل الاستسلام
            if not (self._is_youtube(url) and not self.cookies_file):
                raise
            try:
                return self._download_yt_fallback(url, outdir, preferred)
            except DownloadError:
                raise de
        except Exception as e:  # noqa: BLE001
            first_err = str(e)[:200]

        # L2+L3: اكتشاف ثم تحميل أفضل مرشح سليم (لا نسلّم ملفًا ناقصًا أبدًا)
        try:
            d = self.discover(url)
            cands = d.get("candidates") or []
            if d.get("title") and cands and not cands[0].title:
                cands[0].title = d["title"]
            last_err = "لا مرشحين"
            best_res = None
            for cand in cands[:4]:
                try:
                    res = self._download_candidate(cand, outdir, referer=url)
                    if best_res is None:
                        best_res = res
                    ok_audio = _has_audio(res["path"])
                    ok_video = preferred == "audio" or _has_video(res["path"])
                    if ok_audio and ok_video:
                        return res
                    last_err = "المقطع المحمّل ناقص (صوت/فيديو) — جربت بدائل"
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)[:200]
            if best_res:
                ok_audio = _has_audio(best_res["path"])
                ok_video = preferred == "audio" or _has_video(best_res["path"])
                if ok_audio and ok_video:
                    return best_res
                raise DownloadError(f"فشل التحميل: {last_err or 'المقطع ناقص (صوت/فيديو)'}")
            # L4: يوتيوب محجوب (فحص روبوت) — خوادم Invidious/Piped العامة بلا كوكيز
            if not self.cookies_file and self._is_youtube(url):
                try:
                    return self._download_yt_fallback(url, outdir, preferred)
                except DownloadError:
                    pass
            hint = ""
            if (("youtube" in url.lower() or "youtu.be" in url.lower())
                    and not self.cookies_file):
                hint = (" 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). "
                        "الحل المضمون: أرسل ملف كوكيز يوتيوب للبوت (Get cookies.txt) مرة واحدة.")
            raise DownloadError(f"فشل التحميل: {last_err}{hint}")
        except DownloadError as de:
            # 🎯 الاكتشاف نفسه فشل (لا مرشحين) — L4 لا تزال آخر أمل ليوتيوب المحجوب
            if not self.cookies_file and self._is_youtube(url):
                try:
                    return self._download_yt_fallback(url, outdir, preferred)
                except DownloadError:
                    pass
            raise de
        except Exception as e:  # noqa: BLE001
            raise DownloadError(f"فشل التحميل: {first_err} / {str(e)[:200]}") from e

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
                    "formats": formats[:12], "audio_size": audio_size}
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("extractor").warning("inspect yt-dlp failed for %s: %s", url, str(e)[:200])
        d = self.discover(url, skip_ytdlp=True)
        # 🩺 سبب محتمل لفشل الفحص
        hint = "قد يكون IP الخادم محجوبًا من الموقع — أرسل كوكيز المتصفح للبوت (Get cookies.txt)"
        return {"source": d.get("source"), "title": d.get("title"),
                "thumbnail": d.get("thumbnail"), "duration": d.get("duration"),
                "formats": [], "audio_size": None,
                "_inspect_error": hint}

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
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        
        info = None
        # المحاولة ١: بدون كوكيز (للمحتوى العام — يعرض كل الجودات من IP نظيف)
        try:
            opts_no_cookies = dict(opts)
            opts_no_cookies.pop("cookiefile", None)
            with YoutubeDL(opts_no_cookies) as ydl:
                info = ydl.extract_info(clean_url, download=False)
        except Exception:  # noqa: BLE001
            pass
        
        # المحاولة ٢: مع كوكيز (للمحتوى المقيد — Geographic/Age-restricted)
        if self._is_youtube(url) and self.cookies_file and (
                info is None or not _has_real_formats(info)):
            try:
                with YoutubeDL(opts) as ydl:  # opts فيها cookiefile
                    info = ydl.extract_info(clean_url, download=False)
            except Exception:  # noqa: BLE001
                pass
        
        # 🛑 IP محجوب (SABR): web client يرجع storyboards فقط أو يرمي استثناء.
        # نجرب android كخطة طوارئ — 360p على الأقل.
        if self._is_youtube(url) and (info is None or not _has_real_formats(info)):
            try:
                opts_a = dict(opts)
                opts_a.pop("cookiefile", None)  # android لا يدعم الكوكيز
                opts_a["extractor_args"] = {"youtube": {"player_client": ["android"]}}
                with YoutubeDL(opts_a) as ydl:
                    info = ydl.extract_info(clean_url, download=False)
            except Exception:  # noqa: BLE001
                pass
        
        if not info:
            raise DownloadError("yt-dlp لم يُرجع بيانات")
        if info.get("is_live"):
            raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
        return info

    def _info_to_discovery(self, info: dict) -> dict:
        cands = []
        for f in info.get("formats") or []:
            if f.get("vcodec") == "none" and not f.get("acodec"):
                continue
            u = f.get("url") or f.get("manifest_url")
            if not u or _is_ad_or_tracker(u):
                continue
            cands.append(MediaCandidate(
                url=u,
                kind=_guess_kind(u),
                source="ytdlp",
                title=info.get("title") or "",
                height=f.get("height") or 0,
                size=f.get("filesize") or f.get("filesize_approx") or 0,
                score=100 + (f.get("height") or 0) // 100,
            ))
        if not cands:
            u = info.get("url")
            if u and not _is_ad_or_tracker(u):
                cands.append(MediaCandidate(url=u, kind=_guess_kind(u), source="ytdlp",
                                            title=info.get("title") or "", score=100))
        return {"source": "ytdlp", "title": info.get("title") or "",
                "thumbnail": info.get("thumbnail") or "", "duration": info.get("duration"),
                "candidates": cands}

    @staticmethod
    def _is_youtube(url: str) -> bool:
        u = url.lower()
        return "youtube.com" in u or "youtu.be" in u

    def _download_yt_fallback(self, url: str, outdir: Path, preferred: str) -> dict:
        """L4: يوتيوب محجوب من الخادم — يحمّل عبر خوادم Invidious/Piped العامة (بلا كوكيز).

        لا يلمس يوتيوب إطلاقًا — التحميل من خوادم مرآة عامة (مجرّب 2026-08).
        """
        from yt_dlp import YoutubeDL

        m = re.search(r"(?:v=|shorts/|youtu\.be/|embed/|live/)([A-Za-z0-9_-]{11})", url)
        video_id = m.group(1) if m else None
        if not video_id:
            raise DownloadError("لا يمكن تحديد معرّف يوتيوب")
        last_err = "لا خوادم بديلة متاحة"
        for host in ("yewtu.be", "invidious.nerdvpn.de", "piped.video"):
            alt = f"https://{host}/watch?v={video_id}"
            try:
                opts = self._base_ytdlp_opts(outdir, preferred)
                opts["outtmpl"] = str(outdir / "%(title).120s.%(ext)s")
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(alt, download=True)
                files = [p for p in outdir.iterdir() if p.is_file()]
                if not files:
                    raise DownloadError("لم يُنشأ ملف")
                path = max(files, key=lambda p: p.stat().st_mtime)
                if not _has_audio(path):
                    raise DownloadError("بدون صوت")
                if preferred != "audio" and not _has_video(path):
                    raise DownloadError("بدون فيديو")
                return {"path": str(path), "title": info.get("title") or video_id,
                        "duration": info.get("duration"),
                        "thumbnail": info.get("thumbnail") or "",
                        "ext": path.suffix.lstrip(".")}
            except Exception as e:  # noqa: BLE001
                last_err = f"{host}: {str(e)[:120]}"
        raise DownloadError(f"فشلت خوادم يوتيوب البديلة: {last_err}")

    def _download_ytdlp(self, url: str, outdir: Path, preferred: str) -> dict:
        from yt_dlp import YoutubeDL
        info = None
        path = None
        # المحاولة ١: بدون كوكيز (للمحتوى العام — جودة كاملة من IP نظيف)
        try:
            opts = self._base_ytdlp_opts(outdir, preferred)
            opts.pop("cookiefile", None)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            files = [p for p in outdir.iterdir() if p.is_file()]
            if files:
                path = max(files, key=lambda p: p.stat().st_mtime)
        except Exception as first_err:  # noqa: BLE001
            # المحاولة ٢: مع كوكيز (للمحتوى المقيد)
            if info is None and self.cookies_file and self._is_youtube(url):
                try:
                    opts = self._base_ytdlp_opts(outdir, preferred)
                    with YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                    files = [p for p in outdir.iterdir() if p.is_file()]
                    if files:
                        path = max(files, key=lambda p: p.stat().st_mtime)
                except Exception:  # noqa: BLE001
                    pass
            # المحاولة ٣: android (360p مضمون من أي IP)
            if info is None and self._is_youtube(url):
                try:
                    opts_a = self._base_ytdlp_opts(outdir, preferred)
                    opts_a.pop("cookiefile", None)
                    opts_a["extractor_args"] = {"youtube": {"player_client": ["android"]}}
                    opts_a["outtmpl"] = str(outdir / "%(title).120s_alt.%(ext)s")
                    with YoutubeDL(opts_a) as ydl:
                        info = ydl.extract_info(url, download=True)
                    alt_files = [p for p in outdir.glob("*_alt.*") if p.is_file()]
                    if alt_files:
                        cand = max(alt_files, key=lambda p: p.stat().st_mtime)
                        if _has_audio(cand) and (preferred == "audio" or _has_video(cand)):
                            final = cand.with_name(cand.name.replace("_alt", ""))
                            cand.rename(final)
                            path = final
                except Exception:  # noqa: BLE001
                    pass
            if info is None:
                raise DownloadError(str(first_err)[:300]) from first_err

        # بعض المواقع (تيك توك) تعلن صوتًا ولا توفره فعليًا (HEVC بلا صوت).
        # إذا فشل الاستخراج أو خرج الملف بلا صوت — أعد المحاولة بصيغة فيديو+صوت منفصلين
        # (دمج h264 يضمن صوتًا حقيقيًا) مع احترام ارتفاع الجودة المطلوب.
        if info is None or path is None or not _has_audio(path):
            opts2 = self._base_ytdlp_opts(outdir, preferred)
            # 🎯 صيغة الإعادة: نفضل دمج h264 (صوت مضمون) مع احترام الدقة المطلوبة
            # bestvideo (بدون *) = فيديو فقط، يُدمج مع bestaudio — يتجنب كذبة HEVC
            if preferred.isdigit():
                h = preferred
                opts2["format"] = (f"bestvideo[height<={h}][ext=mp4][vcodec^=h264]+bestaudio/"
                                   f"bestvideo[height<={h}][ext=mp4]+bestaudio/"
                                   f"best[height<={h}][ext=mp4][vcodec^=h264]/best[height<={h}]")
            else:
                opts2["format"] = ("bestvideo[ext=mp4][vcodec^=h264]+bestaudio/"
                                   "bestvideo[ext=mp4]+bestaudio/"
                                   "best[ext=mp4][vcodec^=h264]/best")
            # اسم مؤقت مختلف — لا نكتب فوق الملف الأول
            opts2["outtmpl"] = str(outdir / "%(title).120s_retry.%(ext)s")
            with YoutubeDL(opts2) as ydl:
                info2 = ydl.extract_info(url, download=True)
            info = info or info2
            retry_files = [p for p in outdir.glob("*_retry.*") if p.is_file()]
            if retry_files:
                cand = max(retry_files, key=lambda p: p.stat().st_mtime)
                if _has_audio(cand):
                    try:
                        if path:
                            path.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
                    final = cand.with_name(cand.name.replace("_retry", ""))
                    cand.rename(final)
                    path = final
                else:
                    try:
                        cand.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
            if info is None:
                raise DownloadError("لم يُنشأ ملف")

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

    # ------------------------------------------------------------------
    # L2 — مسح HTML
    # ------------------------------------------------------------------

    def _fetch(self, url: str) -> tuple[str, str]:
        """يجلب الصفحة بانتحال بصمة TLS لمتصفح Chrome (curl_cffi) — يتجاوز كشف البوتات
        القائم على بصمة TLS (Pornhub ومواقع كثيرة ترد 410/403 على httpx العادي)."""
        headers = dict(BROWSER_HEADERS)
        if self.referer_headers:
            headers["Referer"] = url
        try:
            from curl_cffi import requests as cr
            r = cr.get(url, headers=headers, impersonate="chrome",
                       allow_redirects=True, timeout=self.request_timeout)
        except ImportError:
            r = httpx.get(url, headers=headers, follow_redirects=True,
                          timeout=self.request_timeout, verify=True)
        r.raise_for_status()
        final = str(r.url)
        # SSRF: التحقق من المضيف الأصلي وحده لا يكفي — إعادة التحقق بعد إعادة التوجيه
        if is_private_host(final):
            raise DownloadError("الوجهة النهائية للرابط عنوان داخلي — مرفوض")
        ctype = r.headers.get("content-type", "")
        if ctype.startswith("video/") or any(
                m in ctype for m in ("mpegurl", "dash", "mp4", "webm")):
            # الرابط نفسه ملف وسائط مباشر
            return "", final
        return r.text, final

    def _scan_html(self, url: str) -> tuple[list[MediaCandidate], dict]:
        html, final_url = self._fetch(url)
        if not html:
            # الملف مباشر — الرابط نفسه هو الفيديو
            return [MediaCandidate(url=final_url, kind=_guess_kind(final_url),
                                   source="direct-regex", score=120)], {"title": final_url}
        base = final_url or url
        cands: list[MediaCandidate] = []
        seen: set[str] = set()

        # معرّف الفيديو الرئيسي في الصفحة — لترجيح روابطه على روابط المقاطع المشابهة
        video_id = ""
        vm = re.search(r'"video_id"\s*:\s*"?(\d+)', html) or \
             re.search(r"/videos/\d{4}/\d{2}/\d{2}/(\d+)/", html)
        if vm:
            video_id = vm.group(1)

        def add(u: str, kind: str, source: str, score: int, title: str = "",
                height: int = 0, size: int = 0):
            absu = _absolutize(base, u)
            if not absu or absu in seen or _is_ad_or_tracker(absu):
                return
            # SSRF: أي رابط مستخرج من محتوى الصفحة يُفحص — يمنع <video src="http://redis:6379/">
            if is_private_host(absu):
                return
            # ضجيج: صور متحركة / مصغرات / روابط ليست وسائط
            low = absu.lower()
            if "/pics/" in low or "rs:fit" in low or low.endswith((".gif", ".jpg", ".jpeg", ".png", ".webp")):
                return
            # روابط meta التي ليست ملفات وسائط فعلية (مثل صفحات embed) تُسقط
            if source == "meta" and not MEDIA_EXT_RE.search(low):
                return
            seen.add(absu)
            h = height or _hint_height(absu)
            if video_id and video_id in low:
                score += 40
            if kind in ("hls", "dash"):
                score += 25
            if h:
                score += min(h // 50, 30)
            cands.append(MediaCandidate(absu, kind, source, title, h, size, score))

        # 1) وسم <video> و <source>
        for tag in re.findall(r"<(?:video|source)\b[^>]*>", html, re.I):
            attrs = dict(re.findall(r"""([a-zA-Z-]+)\s*=\s*["']([^"']*)["']""", tag))
            for key in ("src", "data-src", "data-url", "data-source", "data-video", "data-file"):
                if attrs.get(key):
                    add(attrs[key], _guess_kind(attrs[key]), "video-tag", 95, height=int(
                        re.search(r"(\d{3,4})x(\d{3,4})", tag).group(2)
                        if re.search(r"(\d{3,4})x(\d{3,4})", tag) else 0))

        # 2) وسوم meta
        for m in re.finditer(
                r"""<meta[^>]+(?:property|name)=["'](og:video(?::url|:secure_url)?|twitter:player:stream)["'][^>]*content=["']([^"']+)["']""",
                html, re.I):
            add(m.group(2), _guess_kind(m.group(2)), "meta", 100)

        # 3) JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             html, re.S | re.I):
            try:
                data = json.loads(m.group(1))
            except Exception:  # noqa: BLE001
                continue
            for node in _walk_json(data):
                if isinstance(node, dict):
                    for k in ("contentUrl", "embedUrl", "url", "content_url"):
                        v = node.get(k)
                        if isinstance(v, str) and re.search(r"\.(mp4|webm|m3u8|mpd)", v, re.I):
                            add(v, _guess_kind(v), "jsonld", 90, title=str(node.get("name") or ""))
                    if node.get("@type") in ("VideoObject", "Video", "MediaObject"):
                        for k in ("contentUrl", "embedUrl", "url", "content_url"):
                            v = node.get(k)
                            if isinstance(v, str):
                                add(v, _guess_kind(v), "jsonld", 95, title=str(node.get("name") or ""))

        # 4) روابط مباشرة في الصفحة (نسختان: خام + مفكوكة الإخفاء \\/ الموجودة في كتل JSON/JS)
        for m in DIRECT_VIDEO_RE.finditer(html):
            add(m.group(0), "direct", "direct-regex", 70)
        for m in MANIFEST_RE.finditer(html):
            add(m.group(0), _guess_kind(m.group(0)), "direct-regex", 75)
        html_unesc = html.replace("\\/", "/")
        for m in DIRECT_VIDEO_RE.finditer(html_unesc):
            add(m.group(0), "direct", "direct-regex", 70)
        for m in MANIFEST_RE.finditer(html_unesc):
            add(m.group(0), _guess_kind(m.group(0)), "direct-regex", 75)

        # 5) كتل إعداد المشغّل في JS
        for pat in PLAYER_KEY_PATTERNS:
            for m in pat.finditer(html):
                raw = _unescape_js(m.group(1))
                if re.search(r"\.(mp4|webm|m3u8|mpd)", raw, re.I):
                    add(raw, _guess_kind(raw), "js-blob", 55)
                elif "mime=video" in raw.lower() or "mime=audio" in raw.lower() \
                        or "videoplayback" in raw.lower():
                    # روابط CDN بلا امتداد (googlevideo…) — مدمجة (فيديو+صوت) تُفضَّل
                    itag_m = re.search(r"[?&]itag=(\d+)", raw.lower())
                    itag_n = int(itag_m.group(1)) if itag_m else 0
                    bonus = 25 if itag_n in COMBINED_ITAGS else 10
                    add(raw, "direct", "js-blob", 50 + bonus)

        # عنوان الصفحة
        title = ""
        tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()[:200]
        if not title:
            om = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
            title = om.group(1) if om else urlparse(base).netloc
        thumb = ""
        im = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if im:
            thumb = im.group(1)

        # ترتيب نهائي
        cands.sort(key=lambda c: c.score, reverse=True)
        return cands, {"title": title, "thumbnail": thumb}

    # ------------------------------------------------------------------
    # L3 — التقاط عبر متصفح هيدلس (CDP)
    # ------------------------------------------------------------------

    def _capture_browser(self, url: str, wait_quiet: float = 6.0, max_wait: float = 35.0) -> list[MediaCandidate]:
        """يفتح الصفحة في Chromium هيدلس ويلتقط طلبات الوسائط من الشبكة."""
        from websockets.sync.client import connect

        bin_path = self._find_browser()
        port = _free_port()
        prof = tempfile.mkdtemp(prefix="yj-browser-")

        def _raise_as_limit():
            # Chrome يحجز ~36GB مساحة افتراضية (V8 sandbox) — ارفع حد RLIMIT_AS إن وُجد
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_AS,
                                   (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
            except Exception:  # noqa: BLE001
                pass

        proc = subprocess.Popen(
            [bin_path, "--headless", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--mute-audio",
             "--autoplay-policy=no-user-gesture-required",
             "--disable-blink-features=AutomationControlled",
             f"--remote-debugging-port={port}",
             f"--user-data-dir={prof}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_raise_as_limit,
        )
        try:
            # انتظر ظهور واجهة CDP
            ws_url = None
            for _ in range(40):
                if proc.poll() is not None:
                    break
                try:
                    import urllib.request
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                        ws_url = json.loads(r.read())["webSocketDebuggerUrl"]
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.25)
            if not ws_url:
                raise DownloadError("تعذر تشغيل المتصفح الخفي (CDP)")

            # أنشئ تبويبًا
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{url}",
                method="PUT")
            with urllib.request.urlopen(req, timeout=5) as r:
                page = json.loads(r.read())
            page_ws = page["webSocketDebuggerUrl"]

            media: dict[str, dict] = {}
            last_seen = time.time()

            def send(ws, msg_id, method, params=None):
                ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    raw = ws.recv()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    if msg.get("id") == msg_id:
                        return msg

            with connect(page_ws, open_timeout=10, max_size=None) as ws:
                mid = 0
                send(ws, mid := mid + 1, "Network.enable")
                send(ws, mid := mid + 1, "Page.enable")
                send(ws, mid := mid + 1, "Page.navigate", {"url": url})
                deadline = time.time() + max_wait
                while time.time() < deadline:
                    try:
                        raw = ws.recv(timeout=0.5)
                    except Exception:  # noqa: BLE001
                        # مهلة الانتظار = لا أحداث جديدة
                        if media and time.time() - last_seen > wait_quiet:
                            break
                        if time.time() - deadline > 0:
                            break
                        continue
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    method = msg.get("method", "")
                    if method == "Network.responseReceived":
                        resp = msg.get("params", {}).get("response", {})
                        murl = resp.get("url", "")
                        mime = resp.get("mimeType", "")
                        if _is_media_request(murl, mime):
                            media.setdefault(murl, {"mime": mime, "size": 0,
                                                    "height": _hint_height(murl)})
                            media[murl]["size"] = max(media[murl]["size"],
                                                      resp.get("encodedDataLength") or 0)
                            last_seen = time.time()
                    elif method == "Network.requestWillBeSent":
                        req = msg.get("params", {}).get("request", {})
                        murl = req.get("url", "")
                        if _is_media_request(murl, ""):
                            media.setdefault(murl, {"mime": "", "size": 0,
                                                    "height": _hint_height(murl)})
                            last_seen = time.time()

                # كوكيز الجلسة للمطابقة مع مضيف الوسائط
                cookies_raw = send(ws, mid := mid + 1, "Network.getAllCookies")
                cookies = cookies_raw.get("result", {}).get("cookies", [])

                # HTML بعد التنفيذ (للمشغّلات التي تحقن src عبر JS)
                dom = send(ws, mid := mid + 1, "Runtime.evaluate",
                           {"expression": "document.documentElement.outerHTML",
                            "returnByValue": True})
                dom_html = ""
                try:
                    dom_html = dom["result"]["result"].get("value", "") or ""
                except Exception:  # noqa: BLE001
                    pass

            # أغلق التبويب
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/close/{page['id']}", timeout=3):
                    pass
            except Exception:  # noqa: BLE001
                pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()
            shutil.rmtree(prof, ignore_errors=True)

        cands: list[MediaCandidate] = []
        cookies_file = self._write_cookies(cookies) if cookies else None
        for murl, m in media.items():
            if _is_ad_or_tracker(murl) or is_private_host(murl):
                continue
            cands.append(MediaCandidate(
                url=murl, kind=_guess_kind(murl), source="cdp",
                title=urlparse(url).netloc, height=m["height"],
                size=m["size"], score=110 + m["size"] // (1024 * 1024),  # أكبر = أفضل
                cookies_file=cookies_file))
        if dom_html:
            # مسح DOM الحيّ — يمسك المشغّلات التي تحقن src عبر JS
            base = url
            seen = {c.url for c in cands}
            for tag in re.findall(r"<(?:video|source)\b[^>]*>", dom_html, re.I):
                attrs = dict(re.findall(r"""([a-zA-Z-]+)\s*=\s*["']([^"']*)["']""", tag))
                for key in ("src", "data-src", "data-url", "data-source", "data-video"):
                    v = attrs.get(key)
                    absu = _absolutize(base, v) if v else None
                    if absu and absu not in seen and not _is_ad_or_tracker(absu) \
                            and not is_private_host(absu):
                        seen.add(absu)
                        cands.append(MediaCandidate(absu, _guess_kind(absu), "cdp-dom",
                                                    height=int(
                                                        re.search(r"(\d{3,4})x(\d{3,4})", tag).group(2)
                                                        if re.search(r"(\d{3,4})x(\d{3,4})", tag) else 0),
                                                    score=95, cookies_file=cookies_file))
        cands.sort(key=lambda c: c.score, reverse=True)
        return cands

    def _write_cookies(self, cookies: list[dict]) -> str | None:
        """يكتب كوكيز الجلسة بصيغة Netscape لاستخدامها مع yt-dlp/httpx."""
        if not cookies:
            return None
        path = Path(tempfile.gettempdir()) / f"yj-cookies-{uuid.uuid4().hex[:10]}.txt"
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            dom = c.get("domain", "")
            if not dom:
                continue
            hostonly = "FALSE"  # دومين ملف الكوكيز قد يبدأ بنقطة — كلها تُكتب كـ host-only=FALSE
            secure = "TRUE" if c.get("secure") else "FALSE"
            exp = int(c.get("expires", 0))
            if exp <= 0:
                exp = 2147483647
            name = c.get("name", "")
            value = c.get("value", "")
            pathv = c.get("path", "/")
            lines.append(f"{dom}\t{hostonly}\t{pathv}\t{secure}\t{exp}\t{name}\t{value}")
        p = Path(path)
        p.write_text("\n".join(lines), encoding="utf-8")
        self._cookies_tmp.append(str(p))
        return str(p)

    # ------------------------------------------------------------------
    # أدوات مساعدة
    # ------------------------------------------------------------------

    def _pick_best(self, cands: list[MediaCandidate]) -> MediaCandidate:
        if not cands:
            raise DownloadError("لا مرشحين")
        cands.sort(key=lambda c: c.score, reverse=True)
        return cands[0]

    def _download_candidate(self, cand: MediaCandidate, outdir: Path,
                            referer: str) -> dict:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        # SSRF: الفحص النهائي قبل فتح أي اتصال (حتى لو جاء المرشح من طبقة أخرى)
        if is_private_host(cand.url):
            raise DownloadError("رابط الوسائط عنوان داخلي — مرفوض")
        if cand.kind in ("hls", "dash"):
            # سلّم الرابط إلى yt-dlp (يتعامل مع المانيفستات حتى للمواقع المجهولة)
            from yt_dlp import YoutubeDL
            opts = self._base_ytdlp_opts(outdir, "best")
            if cand.cookies_file:
                opts["cookiefile"] = cand.cookies_file
            if self.referer_headers:
                opts["http_headers"]["Referer"] = referer
            if cand.title:
                # اسم الملف = اسم المقطع (بدل "master" القادم من رابط المانيفست)
                opts["outtmpl"] = str(outdir / f"{_safe_filename(cand.title)}.%(ext)s")
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(cand.url, download=True)
            files = [p for p in outdir.iterdir() if p.is_file()]
            if not files:
                raise DownloadError("لم يُنشأ ملف من المانيفست")
            path = max(files, key=lambda p: p.stat().st_mtime)
            return {"path": str(path), "title": info.get("title") or cand.title,
                    "duration": info.get("duration"), "thumbnail": info.get("thumbnail") or "",
                    "ext": path.suffix.lstrip(".")}

        # ملف مباشر: تحميل متدفق مع Referer/UA/كوكيز
        headers = dict(BROWSER_HEADERS)
        if self.referer_headers:
            headers["Referer"] = referer
        if cand.cookies_file:
            try:
                from http.cookiejar import MozillaCookieJar
                jar = MozillaCookieJar(cand.cookies_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                for c in jar:
                    headers.setdefault("Cookie", "")
                    headers["Cookie"] += f"{c.name}={c.value}; "
            except Exception:  # noqa: BLE001
                pass
        title = _safe_filename(cand.title or urlparse(cand.url).netloc or "video")
        ext = _ext_from_url(cand.url)
        dest = outdir / f"{title}.{ext}"
        try:
            with httpx.stream("GET", cand.url, headers=headers, follow_redirects=True,
                              timeout=60) as r:
                r.raise_for_status()
                ctype = (r.headers.get("content-type") or "").lower()
                if ctype and not (ctype.startswith("video/") or ctype in (
                        "application/octet-stream", "application/x-mpegurl",
                        "application/vnd.apple.mpegurl", "binary/octet-stream")):
                    raise DownloadError(f"الرابط لم يُرجع فيديو (Content-Type: {ctype})")
                total = int(r.headers.get("content-length") or 0)
                if self.max_filesize and total > self.max_filesize:
                    raise DownloadError("حجم الفيديو يتجاوز الحد المسموح")
                n = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(65536):
                    f.write(chunk)
                    n += len(chunk)
                    if self.progress_hook:
                        try:
                            self.progress_hook({"status": "downloading",
                                                "downloaded_bytes": n,
                                                "total_bytes": total or None})
                        except Exception:  # noqa: BLE001
                            pass
        except httpx.HTTPStatusError as http_err:
            # 🎯 403/410 على رابط مباشر (حجب IP/توقيع): سلّم الرابط لـ yt-dlp قبل الاستسلام —
            # يتعامل مع التوقيعات والهيدرات المطلوبة (مشكلة googlevideo)
            if http_err.response.status_code in (403, 410):
                from yt_dlp import YoutubeDL
                try:
                    opts = self._base_ytdlp_opts(outdir, "best")
                    if cand.cookies_file:
                        opts["cookiefile"] = cand.cookies_file
                    if self.referer_headers:
                        opts["http_headers"]["Referer"] = referer
                    if cand.title:
                        opts["outtmpl"] = str(outdir / f"{_safe_filename(cand.title)}.%(ext)s")
                    with YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(cand.url, download=True)
                    files = [p for p in outdir.iterdir() if p.is_file()]
                    if not files:
                        raise DownloadError("لم يُنشأ ملف")
                    p2 = max(files, key=lambda p: p.stat().st_mtime)
                    return {"path": str(p2), "title": info.get("title") or cand.title,
                            "duration": info.get("duration"),
                            "thumbnail": info.get("thumbnail") or "",
                            "ext": p2.suffix.lstrip(".")}
                except Exception as ydl_err:  # noqa: BLE001
                    raise DownloadError(f"فشل التحميل المباشر ({http_err.response.status_code}) "
                                        f"وبواسطة yt-dlp: {str(ydl_err)[:150]}") from ydl_err
            raise
        # تحقق من البايتات السحرية: mp4 يبدأ بـ ftyp، webm/mkv بـ 1A45DFA3
        with open(dest, "rb") as f:
            head = f.read(16)
        if not (b"ftyp" in head[:12] or head[:4] == b"\x1aE\xdf\xa3"
                or head.startswith(b"#EXTM3U") or head.startswith(b"RIFF")):
            dest.unlink(missing_ok=True)
            raise DownloadError("الملف المُستلم ليس فيديو صالحًا (تم رفضه)")
        if self.max_filesize and dest.stat().st_size > self.max_filesize:
            dest.unlink(missing_ok=True)
            raise DownloadError("حجم الفيديو يتجاوز الحد المسموح")
        return {"path": str(dest), "title": title, "duration": None,
                "thumbnail": "", "ext": ext}

    def _find_browser(self) -> str:
        """يحدد مسار متصفح عامل: الإعداد أولاً، ثم متصفحات Playwright المثبتة."""
        candidates = []
        b = self.browser_bin or ""
        if b:
            candidates.append(shutil.which(b) or b)
        # متصفحات Playwright (chromium-headless-shell ثم chromium الكامل)
        try:
            import glob as _glob
            hs = sorted(_glob.glob(
                str(Path.home() / ".cache/ms-playwright/chromium_headless_shell-*/"
                    "chrome-headless-shell-linux64/chrome-headless-shell")))
            if hs:
                candidates.append(hs[-1])
            ch = sorted(_glob.glob(
                str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome")))
            if ch:
                candidates.append(ch[-1])
        except Exception:  # noqa: BLE001
            pass
        for c in candidates:
            if c and Path(c).exists() and os.access(c, os.X_OK):
                return c
        raise DownloadError(
            "لا يوجد متصفح متاح لطبقة الالتقاط (L3). ثبّت chromium أو شغّل: "
            "python -m playwright install chromium")

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

def _is_media_request(url: str, mime: str) -> bool:
    if MEDIA_EXT_RE.search(url):
        return True
    low_mime = mime.lower()
    if any(low_mime.startswith(p) for p in VIDEO_MIME_PREFIXES):
        return True
    return False


def _hint_height(url: str) -> int:
    m = re.search(r"[/_-](\d{3,4})[pP](?![0-9])", url)
    if m:
        return int(m.group(1))
    return 0


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(unquote(path)).suffix.lstrip(".").lower()
    if ext in ("m3u8", "mpd"):
        return "mp4"  # سينزّل عبر yt-dlp في مسار آخر؛ هذا للملفات المباشرة فقط
    if ext in ("mp4", "webm", "mkv", "mov", "m4v", "avi", "flv", "ts"):
        return ext
    return "mp4"


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return (name or "video")[:120]


def _walk_json(obj):
    """يتنقل في بنية JSON مهما كان عمقها (لا نهائي)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

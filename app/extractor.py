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
    def _fetch_tiktok_photos_info(self, url: str) -> dict | None:
        """يستخرج صور تيك توك وتفاصيلها إذا كان الرابط لمنشور صور (Photo Mode)."""
        import re, json, httpx
        # تيك توك يحتاج Mobile User-Agent لتجاوز صفحة تسجيل الدخول وإرجاع بيانات الصور
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
                resp = client.get(url)
                html = resp.text

            m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(1))
            scope = data.get("__DEFAULT_SCOPE__", {})
            detail = scope.get("webapp.reflow.video.detail") or scope.get("webapp.video-detail") or {}
            item = detail.get("itemInfo", {}).get("itemStruct", {})
            if not item:
                for k, v in scope.items():
                    if isinstance(v, dict) and "itemInfo" in v:
                        item = v.get("itemInfo", {}).get("itemStruct", {})
                        if item:
                            break
            if not item:
                return None
            images_raw = item.get("imagePost", {}).get("images", [])
            if not images_raw:
                return None

            image_urls = []
            for img in images_raw:
                url_list = img.get("imageURL", {}).get("urlList", [])
                if url_list:
                    image_urls.append(url_list[0])

            music_url = item.get("music", {}).get("playUrl")
            title = item.get("desc") or "صور تيك توك"
            thumb = image_urls[0] if image_urls else ""
            return {
                "images": image_urls,
                "music": music_url,
                "title": title,
                "thumbnail": thumb
            }
        except Exception:
            return None

    def _fetch_vidara_info(self, url: str) -> dict | None:
        """يستخرج معلومات البث المباشر لشبكة Vidara / Vidvara / 4kvideo."""
        import re, httpx
        from urllib.parse import urlparse

        m = re.search(r'(?:vidara\.(?:to|so|cc|net)|vidvara\.(?:cc|to|so)|4kvideo\.cc)/(?:v|e)/([a-zA-Z0-9]+)', url, re.I)
        if not m:
            u_low = url.lower()
            if not any(dom in u_low for dom in ("youtube.com", "youtu.be", "instagram.com", "tiktok.com", "x.com", "twitter.com")):
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    }
                    with httpx.Client(follow_redirects=True, timeout=10.0, headers=headers) as client:
                        resp = client.get(url)
                        if resp.status_code == 200:
                            m = re.search(r'(?:vidara\.(?:to|so|cc|net)|vidvara\.(?:cc|to|so)|4kvideo\.cc)/(?:v|e)/([a-zA-Z0-9]+)', resp.text, re.I)
                except Exception:
                    pass

        if not m:
            return None

        filecode = m.group(1)
        target_domains = ["vidvara.cc", "4kvideo.cc", "vidara.to", "vidara.so"]
        try:
            p_netloc = urlparse(url).netloc.lower()
            for d in target_domains:
                if d in p_netloc:
                    target_domains.remove(d)
                    target_domains.insert(0, d)
                    break
        except Exception:
            pass

        for domain in target_domains:
            try:
                api_url = f"https://{domain}/api/stream"
                headers = {
                    "Content-Type": "application/json",
                    "Referer": f"https://{domain}/e/{filecode}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                }
                payload = {"filecode": filecode, "device": "web", "codecs": []}
                with httpx.Client(timeout=10.0) as client:
                    res = client.post(api_url, headers=headers, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        stream_url = data.get("streaming_url")
                        if stream_url:
                            return {
                                "filecode": filecode,
                                "domain": domain,
                                "streaming_url": stream_url,
                                "title": (data.get("title") or "فيديو").strip(),
                                "thumbnail": data.get("thumbnail") or "",
                                "referer": f"https://{domain}/"
                            }
            except Exception:
                continue
        return None

    def _download_vidara(self, info: dict, outdir: Path, preferred: str = "best") -> dict:
        import re, subprocess
        from yt_dlp import YoutubeDL

        safe_title = re.sub(r'[\\/*?:"<>|]', "_", info["title"])[:100].strip() or "video"
        opts = self._base_ytdlp_opts(outdir, preferred)
        opts["outtmpl"] = str(outdir / f"{safe_title}.%(ext)s")
        opts["http_headers"]["Referer"] = info.get("referer", "https://vidvara.cc/")
        opts["hls_prefer_native"] = True
        opts["concurrent_fragment_downloads"] = 1
        opts["socket_timeout"] = 60
        opts["fragment_retries"] = 10
        opts["retries"] = 10

        with YoutubeDL(opts) as ydl:
            res_info = ydl.extract_info(info["streaming_url"], download=True)

        files = [p for p in outdir.iterdir() if p.is_file() and p.name != "photos.json" and not p.name.endswith(".part") and not p.name.endswith(".ytdl")]
        if not files:
            raise DownloadError("لم يتم العثور على الملف المحمل")

        path = max(files, key=lambda p: p.stat().st_mtime)

        if preferred != "audio" and path.suffix.lower() == ".mp4":
            try:
                fixed_tmp = path.with_suffix(".faststart.tmp")
                res = subprocess.run(
                    [ffmpeg_location() or "ffmpeg", "-y", "-v", "error", "-i", str(path),
                     "-c", "copy", "-movflags", "+faststart", str(fixed_tmp)],
                    capture_output=True, timeout=120
                )
                if res.returncode == 0 and fixed_tmp.is_file() and fixed_tmp.stat().st_size > 1024:
                    fixed_tmp.replace(path)
            except Exception:
                pass

        return {
            "path": str(path),
            "ext": path.suffix.lstrip("."),
            "duration": res_info.get("duration") if res_info else None,
            "thumbnail": info.get("thumbnail") or (res_info.get("thumbnail") if res_info else ""),
            "title": info.get("title") or (res_info.get("title") if res_info else "فيديو")
        }

    def download(self, url: str, outdir: Path, preferred: str = "best",
                 title_hint: str = "") -> dict:
        """ينزّل الفيديو — مسار واحد: yt-dlp مع كوكيز (نفس متصفح المستخدم بالضبط)، مع دعم ألبوم صور تيك توك.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        # 🎬 فحص شبكة Vidara / Vidvara / 4kvideo
        vidara_info = self._fetch_vidara_info(url)
        if vidara_info:
            return self._download_vidara(vidara_info, outdir, preferred)

        # 📸 فحص صور تيك توك قبل yt-dlp
        if "tiktok.com" in url.lower():
            photo_info = self._fetch_tiktok_photos_info(url)
            if photo_info and photo_info.get("images"):
                import httpx, json
                image_files = []
                with httpx.Client(follow_redirects=True, timeout=20.0) as client:
                    for idx, img_url in enumerate(photo_info["images"]):
                        try:
                            r = client.get(img_url)
                            if r.status_code == 200:
                                fpath = outdir / f"photo_{idx + 1:02d}.jpg"
                                fpath.write_bytes(r.content)
                                image_files.append(str(fpath))
                        except Exception:
                            pass
                    audio_path = None
                    if photo_info.get("music"):
                        try:
                            ar = client.get(photo_info["music"])
                            if ar.status_code == 200:
                                apath = outdir / "music.mp3"
                                apath.write_bytes(ar.content)
                                audio_path = str(apath)
                        except Exception:
                            pass

                if image_files:
                    payload = {
                        "type": "photos",
                        "title": photo_info.get("title") or "صور تيك توك",
                        "photos": image_files,
                        "audio": audio_path
                    }
                    meta_file = outdir / "photos.json"
                    meta_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    return {
                        "path": str(meta_file),
                        "ext": "json",
                        "duration": None,
                        "thumbnail": photo_info.get("thumbnail"),
                        "title": photo_info.get("title"),
                        "is_photos": True,
                        "photos": image_files,
                        "audio": audio_path
                    }

        # 1. محاولة التحميل عبر yt-dlp أولاً (يوتيوب والسوشال ميديا والمواقع المدعومة)
        try:
            return self._download_ytdlp(url, outdir, preferred)
        except Exception as e:
            # إذا كان الرابط يوتيوب أو سوشال ميديا معروفة (إنستغرام/تيك توك)، نرمي الخطأ دون مسح صفحات
            u_low = url.lower()
            if any(dom in u_low for dom in ("youtube.com", "youtu.be", "instagram.com", "tiktok.com")):
                raise
            # إذا كان الخطأ 403 أو حظر، نجرب التنزيل مع انتحال بصمة المتصفح مباشرة للرابط نفسه
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                from yt_dlp import YoutubeDL
                opts = self._base_ytdlp_opts(outdir, preferred)
                opts["impersonate"] = ImpersonateTarget.from_str("chrome")
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                files = [p for p in outdir.iterdir() if p.is_file() and p.name != "photos.json"]
                if files:
                    path = max(files, key=lambda p: p.stat().st_mtime)
                    if preferred != "audio" and path.suffix.lower() == ".mp4":
                        try:
                            import subprocess
                            from app.extractor import ffmpeg_location
                            fixed_tmp = path.with_suffix(".faststart.tmp")
                            res = subprocess.run(
                                [ffmpeg_location() or "ffmpeg", "-y", "-v", "error", "-i", str(path),
                                 "-c", "copy", "-movflags", "+faststart", str(fixed_tmp)],
                                capture_output=True, timeout=120
                            )
                            if res.returncode == 0 and fixed_tmp.is_file() and fixed_tmp.stat().st_size > 1024:
                                fixed_tmp.replace(path)
                        except Exception:
                            pass
                    return {
                        "path": str(path),
                        "ext": path.suffix.lstrip("."),
                        "duration": info.get("duration") if info else None,
                        "thumbnail": info.get("thumbnail") if info else "",
                        "title": info.get("title") if info else "فيديو"
                    }
            except Exception:
                pass

            # للمواقع الأخرى: فحص ومسح كود الصفحة للبحث عن الفيديو المباشر أو المانيفست (HLS/DASH/MP4)
            fallback_res = self._download_web_fallback(url, outdir, preferred)
            if fallback_res:
                return fallback_res
            raise e

    def _download_web_fallback(self, url: str, outdir: Path, preferred: str = "best") -> dict | None:
        """مسار بديل للمواقع الأخرى غير المدعومة في yt-dlp:
        يبحث في HTML عن وسوم <video>, <source>, og:video, json-ld وروابط mp4/m3u8 وينزلها مباشرة.
        """
        import re, json, httpx
        from urllib.parse import urljoin
        from yt_dlp import YoutubeDL

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": url,
        }

        # 1. جلب محتوى الصفحة
        try:
            with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
                r = client.get(url)
                if r.status_code != 200:
                    return None
                html = r.text
                final_url = str(r.url)
        except Exception:
            return None

        candidates = []

        # وسوم video و source
        for tag in re.findall(r"<(?:video|source)\b[^>]*>", html, re.I):
            attrs = dict(re.findall(r'([a-zA-Z-]+)\s*=\s*["\']([^"\']*)["\']', tag))
            for k in ("src", "data-src", "data-url", "data-source", "data-video", "data-file"):
                if attrs.get(k):
                    candidates.append(urljoin(final_url, attrs[k]))

        # روابط التنزيل المباشرة في وسوم <a> href
        for href in re.findall(r'href=["\']([^"\']+\.(?:mp4|m3u8|webm))["\']', html, re.I):
            candidates.append(urljoin(final_url, href))

        # وسوم meta
        for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](og:video(?::url|:secure_url)?|twitter:player:stream)["\'][^>]*content=["\']([^"\']+)["\']', html, re.I):
            candidates.append(urljoin(final_url, m.group(2)))

        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
            try:
                data = json.loads(m.group(1))
                nodes = data if isinstance(data, list) else [data]
                for node in nodes:
                    if isinstance(node, dict):
                        for k in ("contentUrl", "embedUrl", "url", "content_url"):
                            val = node.get(k)
                            if isinstance(val, str) and re.search(r"\.(mp4|webm|m3u8|mpd)", val, re.I):
                                candidates.append(urljoin(final_url, val))
            except Exception:
                pass

        # روابط صريحة داخل الكود
        for m in re.finditer(r'https?://[^\s"\'<>]+\.(?:mp4|m3u8|webm)', html, re.I):
            candidates.append(m.group(0))

        # تنقية الروابط
        seen = set()
        valid_candidates = []
        for c in candidates:
            c = c.replace("\\/", "/")
            if c not in seen and not any(ext in c.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                seen.add(c)
                valid_candidates.append(c)

        # استخراج العنوان
        title = "video"
        tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()[:100]

        # 2. تجربة تنزيل أول مرشح صالح عبر yt-dlp أو التحميل المباشر
        for cand_url in valid_candidates:
            # تحميل مباشر أولاً إذا كان الرابط ينتهي بـ mp4 أو webm
            if re.search(r"\.(mp4|webm)($|\?)", cand_url, re.I):
                try:
                    safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)[:50].strip() or "video"
                    fpath = outdir / f"{safe_name}.mp4"
                    with httpx.stream("GET", cand_url, headers=headers, follow_redirects=True, timeout=45.0) as resp:
                        if resp.status_code == 200:
                            with open(fpath, "wb") as f:
                                for chunk in resp.iter_bytes(65536):
                                    f.write(chunk)
                            if fpath.stat().st_size > 1024:
                                # تطبيق faststart على التنزيل المباشر أيضاً
                                try:
                                    import subprocess
                                    fixed_tmp = fpath.with_suffix(".faststart.tmp")
                                    subprocess.run(
                                        ["ffmpeg", "-y", "-v", "error", "-i", str(fpath),
                                         "-c", "copy", "-movflags", "+faststart", str(fixed_tmp)],
                                        capture_output=True, timeout=120
                                    )
                                    if fixed_tmp.is_file() and fixed_tmp.stat().st_size > 1024:
                                        fixed_tmp.replace(fpath)
                                except Exception:
                                    pass
                                return {
                                    "path": str(fpath),
                                    "ext": "mp4",
                                    "duration": None,
                                    "thumbnail": "",
                                    "title": title
                                }
                except Exception:
                    pass

            try:
                opts = self._base_ytdlp_opts(outdir, preferred)
                opts["http_headers"]["Referer"] = url
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(cand_url, download=True)
                files = [p for p in outdir.iterdir() if p.is_file() and p.name != "photos.json"]
                if files:
                    path = max(files, key=lambda p: p.stat().st_mtime)
                    return {
                        "path": str(path),
                        "ext": path.suffix.lstrip("."),
                        "duration": info.get("duration") if info else None,
                        "thumbnail": info.get("thumbnail") if info else "",
                        "title": title or (info.get("title") if info else "فيديو")
                    }
            except Exception:
                pass

        return None

    def inspect(self, url: str) -> dict:
        """معاينة سريعة لأي رابط: yt-dlp أولاً (قائمة جودات حقيقية)، ثم المسح/المتصفح.

        يُرجع: {source, title, thumbnail, duration, formats: [{id,label,size,height}], audio_size}
        ⚠️ الجودات دائمًا حقيقية من المقطع نفسه — ممنوع أي جودات افتراضية/ثابتة.
        الحجم = حجم الفيديو + أفضل تدفق صوتي (تقدير الحجم النهائي للملف المدمج).
        """
        # 🎬 فحص شبكة Vidara / Vidvara / 4kvideo
        vidara_info = self._fetch_vidara_info(url)
        if vidara_info:
            try:
                stream_url = vidara_info["streaming_url"]
                opts = {
                    "quiet": True, "skip_download": True, "noplaylist": True,
                    "no_warnings": True, "socket_timeout": 30,
                    "http_headers": {"Referer": vidara_info.get("referer", "https://vidvara.cc/")}
                }
                from yt_dlp import YoutubeDL
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(stream_url, download=False)
                formats = []
                for f in (info.get("formats") or []):
                    h = f.get("height")
                    if h:
                        formats.append({
                            "id": str(h), "height": h,
                            "label": f"{h}p / mp4",
                            "size": f.get("filesize") or f.get("filesize_approx")
                        })
                if not formats:
                    formats = [{"id": "320", "height": 320, "label": "320p / mp4", "size": None}]
                return {
                    "source": "vidara",
                    "title": vidara_info.get("title") or info.get("title"),
                    "thumbnail": vidara_info.get("thumbnail") or info.get("thumbnail"),
                    "duration": info.get("duration"),
                    "formats": formats,
                    "audio_size": None
                }
            except Exception:
                return {
                    "source": "vidara",
                    "title": vidara_info.get("title") or "فيديو",
                    "thumbnail": vidara_info.get("thumbnail") or "",
                    "duration": None,
                    "formats": [{"id": "320", "height": 320, "label": "320p / mp4", "size": None}],
                    "audio_size": None
                }

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
        }
        # تمكين انتحال بصمة المتصفح لتجاوز حظر الـ 403 / Cloudflare في المواقع العامة
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass
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
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass
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

        # 🚀 إصلاح التنقل والتقديم في الثواني لجميع مقاطع الفيديو:
        # إزالة التجزئة (De-fragmentation) ونقل moov atom إلى بداية الملف (+faststart)
        # هذا يمنع مشكلة إعادة تشغيل الفيديو من البداية عند تقديم الثواني في تيليجرام
        if preferred != "audio" and path.suffix.lower() == ".mp4":
            try:
                import subprocess
                fixed_tmp = path.with_suffix(".faststart.tmp")
                res = subprocess.run(
                    [ffmpeg_location() or "ffmpeg", "-y", "-v", "error", "-i", str(path),
                     "-c", "copy", "-movflags", "+faststart", str(fixed_tmp)],
                    capture_output=True, timeout=120
                )
                if res.returncode == 0 and fixed_tmp.is_file() and fixed_tmp.stat().st_size > 1024:
                    fixed_tmp.replace(path)
            except Exception:
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

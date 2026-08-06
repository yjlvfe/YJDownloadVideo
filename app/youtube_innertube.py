"""غلاف Python لسكربت YouTube.js (youtubei.js + bgutils PO tokens) — مسار يوتيوب شامل.

يوفّر الفحص والتحميل بكل الجودات من خادم واحد عبر innertube API مع حل BotGuard
داخليًا (bgutils-js) — بلا كوكيز وبلا خدمات خارجية. أي فشل = يعود المتصل
للمسار العادي (yt-dlp ← android 360p) بسلاسة.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

_VIDEO_ID = re.compile(r"(?:v=|shorts/|youtu\.be/|embed/|live/)([A-Za-z0-9_-]{11})")


class YouTubeInnertubeError(RuntimeError):
    pass


def extract_youtube_video_id(url: str) -> str:
    match = _VIDEO_ID.search(str(url or ""))
    if not match:
        raise YouTubeInnertubeError("تعذر تحديد معرّف فيديو يوتيوب")
    return match.group(1)


def _sanitize(message: str, limit: int = 300) -> str:
    """يزيل الروابط (قد تحوي توكنات) ويقصر الرسالة."""
    cleaned = re.sub(r"https?://\S+", "[URL]", str(message or ""))
    return cleaned[:limit]


def _parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise YouTubeInnertubeError("YouTube.js لم يُرجع نتيجة صالحة")


def _raise_as_limit() -> None:
    """يرفع حد الذاكرة الافتراضية — node/undici ينهار بـ Wasm OOM مع RLIMIT_AS المنخفض."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,
                           (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    except Exception:  # noqa: BLE001
        pass


def run_youtube_innertube(
    *,
    action: str,
    url: str,
    preferred: str = "best",
    output_dir: Path | None = None,
    max_bytes: int = 0,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """يشغّل سكربت youtubei.js (node) — inspect أو download."""
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "youtube_innertube.mjs"
    if not script.is_file():
        raise YouTubeInnertubeError("YouTube.js runtime غير مثبت (scripts/youtube_innertube.mjs)")
    video_id = extract_youtube_video_id(url)
    destination = Path(output_dir or root / "storage").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        "node",
        str(script),
        action,
        video_id,
        str(preferred or "best"),
        str(destination),
        str(max(0, int(max_bytes or 0))),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_seconds)),
            cwd=root,
            check=False,
            preexec_fn=_raise_as_limit,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise YouTubeInnertubeError(
            _sanitize(f"YouTube.js runtime failed: {exc}", 260)
        ) from exc

    if completed.returncode != 0:
        raise YouTubeInnertubeError(
            _sanitize(completed.stderr or completed.stdout or "YouTube.js failed", 420)
        )

    result = _parse_last_json(completed.stdout)
    if action == "download":
        media_path = Path(str(result.get("path") or "")).resolve()
        try:
            media_path.relative_to(destination)
        except ValueError as exc:
            raise YouTubeInnertubeError("YouTube.js أعاد مسارًا غير آمن") from exc
        if not media_path.is_file():
            raise YouTubeInnertubeError("YouTube.js لم ينشئ الملف")
        if max_bytes and media_path.stat().st_size > max_bytes:
            media_path.unlink(missing_ok=True)
            raise YouTubeInnertubeError("حجم الفيديو يتجاوز الحد المسموح")
        result["path"] = str(media_path)
    return result


def innertube_available() -> bool:
    """فحص سريع: node موجود + السكربت موجود؟ (بلا تشغيل فعلي)"""
    if not (Path(__file__).resolve().parent.parent / "scripts" / "youtube_innertube.mjs").is_file():
        return False
    try:
        import shutil
        return shutil.which("node") is not None
    except Exception:  # noqa: BLE001
        return False

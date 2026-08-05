from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import cookie_file_for_url, settings


_MAX_COOKIE_BYTES = 1_000_000
_TARGET_NAME = "youtube.txt"
_ALLOWED_YOUTUBE_DOMAINS = ("youtube.com", "google.com", "googlevideo.com")


def _target() -> Path:
    return settings.cookies_path / _TARGET_NAME


def _environment_cookie_configured() -> bool:
    return any(
        os.getenv(name, "").strip()
        for name in (
            "YOUTUBE_COOKIES_FILE",
            "YOUTUBE_COOKIES_B64",
            "YTDLP_COOKIES_FILE",
            "YTDLP_COOKIES_B64",
        )
    )


def _parse_cookie_payload(payload: bytes) -> tuple[bytes, int, bool]:
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("ملف Cookies غير صالح")
    raw = bytes(payload)
    if not raw or len(raw) > _MAX_COOKIE_BYTES or b"\x00" in raw:
        raise ValueError("حجم ملف Cookies غير صالح")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("يجب أن يكون ملف Cookies نصيًا بصيغة UTF-8") from exc

    cookie_count = 0
    has_youtube = False
    first_content = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not first_content:
            first_content = stripped
        if stripped.startswith("#") and not stripped.startswith("#HttpOnly_"):
            continue
        candidate = stripped[len("#HttpOnly_") :] if stripped.startswith("#HttpOnly_") else stripped
        fields = candidate.split("\t")
        if len(fields) != 7:
            raise ValueError("الملف ليس بصيغة Netscape cookies.txt الصحيحة")
        domain = fields[0].lstrip(".").lower()
        if not domain:
            raise ValueError("يوجد نطاق فارغ داخل ملف Cookies")
        cookie_count += 1
        if any(domain == allowed or domain.endswith("." + allowed) for allowed in _ALLOWED_YOUTUBE_DOMAINS):
            has_youtube = True

    if not first_content.startswith("# Netscape HTTP Cookie File"):
        raise ValueError("الملف ليس بصيغة Netscape cookies.txt")
    if cookie_count == 0:
        raise ValueError("ملف Cookies لا يحتوي على أي سجلات")
    if not has_youtube:
        raise ValueError("ملف Cookies لا يحتوي على بيانات YouTube")
    if not raw.endswith(b"\n"):
        raw += b"\n"
    return raw, cookie_count, has_youtube


def install_youtube_cookies(payload: bytes) -> dict[str, Any]:
    normalized, cookie_count, _ = _parse_cookie_payload(payload)
    target = _target()
    temporary = target.with_suffix(".txt.tmp")
    temporary.write_bytes(normalized)
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    return {
        "configured": True,
        "cookie_count": cookie_count,
        "source": "uploaded",
    }


def remove_uploaded_youtube_cookies() -> dict[str, Any]:
    target = _target()
    removed = target.is_file()
    if removed:
        target.unlink()
    return {
        "removed": removed,
        "configured": _environment_cookie_configured(),
    }


def youtube_cookie_status() -> dict[str, Any]:
    target = _target()
    resolved = cookie_file_for_url("https://youtube.com")
    source = "none"
    if resolved:
        source = "uploaded" if Path(resolved) == target else "environment"
    return {
        "configured": bool(resolved),
        "source": source,
        "uploaded": target.is_file(),
        "private_permissions": bool(target.is_file() and target.stat().st_mode & 0o777 == 0o600),
    }

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .config import settings
from .redaction import sanitize_external_error


def classify_media_error(value: object) -> str:
    text = str(value or "").lower()
    known = (
        "youtube_circuit_open",
        "youtube_bot_check",
        "youtube_login_required",
        "youtube_fragment_forbidden",
        "youtube_rate_limited",
    )
    for code in known:
        if code in text:
            return code
    if "requested format is not available" in text or "format is not available" in text:
        return "format_unavailable"
    if "429" in text or "too many requests" in text:
        return "rate_limited"
    if "database is locked" in text or "database table is locked" in text:
        return "database_busy"
    if "بدون صوت" in text or "no audio" in text:
        return "missing_audio"
    if "بدون فيديو" in text or "no video" in text:
        return "missing_video"
    if "لم يُنشأ ملف" in text or "file is missing" in text:
        return "missing_output"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "403" in text or "forbidden" in text:
        return "forbidden"
    if "login" in text or "cookie" in text or "sign in" in text:
        return "authentication_required"
    return "unclassified"


def recent_media_failures(jobs: Iterable[Any], *, limit: int = 5) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    secrets = (
        settings.telegram_bot_token,
        settings.telegram_api_hash,
        settings.worker_shared_secret,
        settings.admin_api_token,
        settings.youtube_proxy_url,
    )
    for job in jobs:
        if str(getattr(job, "status", "")) != "failed":
            continue
        raw_error = str(getattr(job, "error", "") or "")
        result.append(
            {
                "status": "failed",
                "format": str(getattr(job, "requested_format", "") or "unknown")[:32],
                "code": classify_media_error(raw_error),
                "message": sanitize_external_error(
                    raw_error,
                    secrets_to_hide=secrets,
                    limit=220,
                ),
            }
        )
        if len(result) >= max(1, int(limit)):
            break
    return result

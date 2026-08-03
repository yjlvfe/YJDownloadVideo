from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path

import httpx
from yt_dlp import YoutubeDL

from .config import settings
from .security import validate_public_url

log = logging.getLogger("vercel-remote")

_seen_updates: set[int] = set()
_seen_order: deque[int] = deque(maxlen=512)


def _remember_update(update_id: int | None) -> bool:
    """Return False for duplicate Telegram webhook deliveries."""
    if update_id is None:
        return True
    if update_id in _seen_updates:
        return False
    if len(_seen_order) == _seen_order.maxlen:
        oldest = _seen_order.popleft()
        _seen_updates.discard(oldest)
    _seen_order.append(update_id)
    _seen_updates.add(update_id)
    return True


def select_remote_url(info: dict) -> str | None:
    """Choose one HTTP MP4 format that already contains video and audio."""
    direct = info.get("url")
    if (
        isinstance(direct, str)
        and direct.startswith(("https://", "http://"))
        and not info.get("requested_formats")
        and str(info.get("ext") or "").lower() == "mp4"
        and info.get("vcodec") not in (None, "none")
        and info.get("acodec") not in (None, "none")
    ):
        return direct

    candidates: list[tuple[int, str]] = []
    for item in info.get("formats") or []:
        media_url = item.get("url")
        if not isinstance(media_url, str) or not media_url.startswith(("https://", "http://")):
            continue
        if str(item.get("ext") or "").lower() != "mp4":
            continue
        if item.get("vcodec") in (None, "none") or item.get("acodec") in (None, "none"):
            continue
        protocol = str(item.get("protocol") or "https").lower()
        if "m3u8" in protocol or "dash" in protocol or "http_dash" in protocol:
            continue
        size = item.get("filesize") or item.get("filesize_approx") or 0
        if size and size > 50 * 1024 * 1024:
            continue
        height = int(item.get("height") or 0)
        score = height + (100 if item.get("format_note") else 0)
        candidates.append((score, media_url))

    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0], reverse=True)
    return candidates[0][1]


def _extract_remote_media(url: str, user_id: int | None) -> dict:
    cookie_file = None
    if user_id is not None:
        candidate = settings.cookies_path / f"tg:{user_id}.txt"
        if candidate.is_file():
            cookie_file = str(candidate)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 2,
        "format": "best[ext=mp4][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]",
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
            )
        },
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError("لم يتم العثور على بيانات الفيديو")
    if info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise RuntimeError("لم يتم العثور على الفيديو")
    if info.get("is_live"):
        raise RuntimeError("البث المباشر غير مدعوم")

    remote_url = select_remote_url(info)
    if not remote_url:
        raise RuntimeError("لا توجد نسخة MP4 مباشرة تجمع الصوت والفيديو")

    return {
        "url": remote_url,
        "title": str(info.get("title") or "الفيديو")[:900],
        "duration": int(info.get("duration") or 0),
    }


async def _telegram_call(method: str, payload: dict, timeout: float = 40.0) -> dict:
    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=0)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        limits=limits,
        trust_env=False,
    ) as client:
        response = await client.post(endpoint, json=payload)
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Telegram returned HTTP {response.status_code}") from exc
    if not response.is_success or not body.get("ok"):
        description = str(body.get("description") or f"HTTP {response.status_code}")
        raise RuntimeError(description[:240])
    return body.get("result") or {}


async def _edit_status(chat_id: int, message_id: int | None, text: str) -> None:
    if message_id is None:
        try:
            await _telegram_call("sendMessage", {"chat_id": chat_id, "text": text})
        except Exception:  # noqa: BLE001
            log.exception("failed to send Telegram status")
        return
    try:
        await _telegram_call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )
    except Exception:  # noqa: BLE001
        log.exception("failed to edit Telegram status")


async def handle_vercel_remote_update(data: dict) -> bool:
    """Handle URL messages without downloading media into Vercel's filesystem."""
    message = data.get("message") or data.get("edited_message")
    if not isinstance(message, dict):
        return False
    text = str(message.get("text") or "").strip()
    if not text.startswith(("https://", "http://")):
        return False

    update_id = data.get("update_id")
    if not _remember_update(update_id if isinstance(update_id, int) else None):
        return True

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = sender.get("id")
    if not isinstance(chat_id, int):
        return True

    try:
        source_url = validate_public_url(text)
    except ValueError as exc:
        await _telegram_call("sendMessage", {"chat_id": chat_id, "text": f"⚠️ {exc}"})
        return True

    status_id: int | None = None
    try:
        result = await _telegram_call(
            "sendMessage",
            {"chat_id": chat_id, "text": "🔎 جارٍ استخراج رابط الفيديو المباشر…"},
        )
        status_id = result.get("message_id") if isinstance(result, dict) else None
    except Exception:  # noqa: BLE001
        log.exception("failed to create Telegram status message")

    try:
        media = await asyncio.wait_for(
            asyncio.to_thread(_extract_remote_media, source_url, user_id if isinstance(user_id, int) else None),
            timeout=55,
        )
    except asyncio.TimeoutError:
        await _edit_status(chat_id, status_id, "❌ استغرق استخراج الرابط وقتًا طويلًا. جرّب رابطًا آخر.")
        return True
    except Exception as exc:  # noqa: BLE001
        await _edit_status(chat_id, status_id, f"❌ تعذر استخراج الفيديو: {str(exc)[:220]}")
        return True

    await _edit_status(chat_id, status_id, "📤 تم العثور على الفيديو، تيليجرام يجلبه الآن…")

    payload = {
        "chat_id": chat_id,
        "video": media["url"],
        "caption": media["title"],
        "supports_streaming": True,
    }
    if media.get("duration"):
        payload["duration"] = media["duration"]

    try:
        await asyncio.wait_for(_telegram_call("sendVideo", payload, timeout=105), timeout=110)
    except Exception as video_error:  # noqa: BLE001
        try:
            await asyncio.wait_for(
                _telegram_call(
                    "sendDocument",
                    {"chat_id": chat_id, "document": media["url"], "caption": media["title"]},
                    timeout=105,
                ),
                timeout=110,
            )
        except Exception as document_error:  # noqa: BLE001
            log.warning("Telegram remote fetch failed: video=%s document=%s", video_error, document_error)
            await _edit_status(
                chat_id,
                status_id,
                "❌ تيليجرام لم يستطع جلب الملف من المصدر. لن يظل الطلب معلّقًا؛ جرّب رابطًا آخر أو استخدم الموقع.",
            )
            return True

    if status_id is not None:
        try:
            await _telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": status_id})
        except Exception:  # noqa: BLE001
            pass
    return True

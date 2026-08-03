from __future__ import annotations

import asyncio
import logging
from collections import deque

import httpx

from .config import settings
from .security import validate_public_url
from .vercel_upload import download_and_upload

log = logging.getLogger("vercel-remote")

_seen_updates: set[int] = set()
_seen_order: deque[int] = deque(maxlen=512)


def _remember_update(update_id: int | None) -> bool:
    """Return False for duplicate Telegram webhook deliveries in this instance."""
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


def _select_remote_format(info: dict) -> dict | None:
    """Compatibility helper retained for existing format-selection tests."""
    direct = info.get("url")
    if (
        isinstance(direct, str)
        and direct.startswith(("https://", "http://"))
        and not info.get("requested_formats")
        and str(info.get("ext") or "").lower() == "mp4"
        and info.get("vcodec") not in (None, "none")
        and info.get("acodec") not in (None, "none")
    ):
        return info

    candidates: list[tuple[int, dict]] = []
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
        candidates.append((height, item))

    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0], reverse=True)
    return candidates[0][1]


def select_remote_url(info: dict) -> str | None:
    selected = _select_remote_format(info)
    if not selected:
        return None
    media_url = selected.get("url")
    return media_url if isinstance(media_url, str) else None


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
    result = body.get("result")
    return result if isinstance(result, dict) else {}


async def _edit_status(chat_id: int, message_id: int | None, text: str) -> None:
    try:
        if message_id is None:
            await _telegram_call("sendMessage", {"chat_id": chat_id, "text": text})
        else:
            await _telegram_call(
                "editMessageText",
                {"chat_id": chat_id, "message_id": message_id, "text": text},
            )
    except Exception:  # noqa: BLE001
        log.exception("failed to update Telegram status")


async def handle_vercel_remote_update(data: dict) -> bool:
    """Download and upload a small video inside one bounded Vercel invocation."""
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
            {
                "chat_id": chat_id,
                "text": "⬇️ جارٍ تنزيل الفيديو ثم رفعه مباشرة إلى تيليجرام…",
            },
        )
        status_id = result.get("message_id")
    except Exception:  # noqa: BLE001
        log.exception("failed to create Telegram status message")

    try:
        await asyncio.to_thread(
            download_and_upload,
            chat_id,
            source_url,
            user_id if isinstance(user_id, int) else None,
        )
    except Exception as exc:  # noqa: BLE001
        safe_error = str(exc)[:220]
        log.warning("direct Telegram upload failed: %s", safe_error)
        await _edit_status(
            chat_id,
            status_id,
            f"❌ فشل تنزيل أو رفع الفيديو: {safe_error}",
        )
        return True

    if status_id is not None:
        try:
            await _telegram_call(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": status_id},
            )
        except Exception:  # noqa: BLE001
            pass
    return True

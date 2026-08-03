from __future__ import annotations

import re

from .remote_worker import forward_telegram_update
from .security import validate_public_url

_MEDIA_CHOICE_CALLBACK_RE = re.compile(
    r"^pick:(?:(?:v|a):[A-Za-z0-9_-]{6,24}|q:[A-Za-z0-9_-]{6,24}:(?:original|\d{2,4}))$"
)


def _is_media_choice_callback(value: str) -> bool:
    return bool(_MEDIA_CHOICE_CALLBACK_RE.fullmatch(value))


async def handle_vercel_remote_update(data: dict) -> bool:
    """Forward URL and media-choice updates to the persistent 2GB worker.

    Vercel validates metadata and never creates a media file under /tmp.
    """
    callback = data.get("callback_query")
    if isinstance(callback, dict):
        callback_data = str(callback.get("data") or "").strip()
        if _is_media_choice_callback(callback_data):
            await forward_telegram_update(data)
            return True
        return False

    message = data.get("message") or data.get("edited_message")
    if not isinstance(message, dict):
        return False
    text = str(message.get("text") or "").strip()
    if not text.startswith(("https://", "http://")):
        return False

    validate_public_url(text)
    await forward_telegram_update(data)
    return True

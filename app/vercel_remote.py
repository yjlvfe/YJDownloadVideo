from __future__ import annotations

from .remote_worker import forward_telegram_update
from .security import validate_public_url


async def handle_vercel_remote_update(data: dict) -> bool:
    """Forward URL updates to the persistent 2GB Render worker.

    Vercel only validates metadata and never creates a media file under /tmp.
    """
    message = data.get("message") or data.get("edited_message")
    if not isinstance(message, dict):
        return False
    text = str(message.get("text") or "").strip()
    if not text.startswith(("https://", "http://")):
        return False

    validate_public_url(text)
    await forward_telegram_update(data)
    return True

from __future__ import annotations

from typing import Any

from .telegram_flow import TelegramFlow


_PATCHED = False
_ORIGINAL_INSPECT_WITH_PROGRESS = TelegramFlow._inspect_with_progress


async def _safe_inspect_with_progress(
    self: TelegramFlow,
    chat_id: int,
    message_id: int,
    url: str,
) -> dict[str, Any]:
    try:
        return await _ORIGINAL_INSPECT_WITH_PROGRESS(self, chat_id, message_id, url)
    except Exception:  # noqa: BLE001
        await self._safe_edit(
            chat_id,
            message_id,
            "⚠️ تعذر فحص الجودات؛ سيظهر خيار الأفضل فقط.",
        )
        return {
            "title": "",
            "thumbnail": "",
            "duration": None,
            "formats": [],
            "inspection_failed": True,
        }


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    TelegramFlow._inspect_with_progress = _safe_inspect_with_progress
    _PATCHED = True

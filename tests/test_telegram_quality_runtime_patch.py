from __future__ import annotations

import pytest

from app import telegram_quality_runtime_patch


class FakeFlow:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def _safe_edit(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append(text)


@pytest.mark.asyncio
async def test_failed_inspection_returns_truthful_empty_formats(monkeypatch):
    async def fail(self, chat_id, message_id, url):
        raise RuntimeError("inspection unavailable")

    monkeypatch.setattr(
        telegram_quality_runtime_patch,
        "_ORIGINAL_INSPECT_WITH_PROGRESS",
        fail,
    )
    flow = FakeFlow()

    result = await telegram_quality_runtime_patch._safe_inspect_with_progress(
        flow,
        7,
        11,
        "https://example.com/video",
    )

    assert result["formats"] == []
    assert result["inspection_failed"] is True
    assert flow.edits == ["⚠️ تعذر فحص الجودات؛ سيظهر خيار الأفضل فقط."]
from types import SimpleNamespace

import pytest

from app.media_choice import QualityOption
from app.telegram_flow import (
    TelegramFlow,
    kind_keyboard,
    parse_callback,
    progress_text,
    quality_keyboard,
)


class FakeAPI:
    def __init__(self):
        self.markups = []
        self.edits = []
        self.answers = []

    async def answer_callback_query(self, callback_id, text="", *, show_alert=False):
        self.answers.append((callback_id, text, show_alert))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        self.markups.append(reply_markup)
        return {}

    async def edit_message_text(self, chat_id, message_id, text, **extra):
        self.edits.append(text)
        return {}

    async def delete_message(self, chat_id, message_id):
        return None


def test_kind_keyboard_places_video_and_audio_under_preview():
    markup = kind_keyboard("abc123")

    assert markup == {
        "inline_keyboard": [[
            {"text": "🎬 فيديو", "callback_data": "pick:v:abc123"},
            {"text": "🎧 صوت MP3", "callback_data": "pick:a:abc123"},
        ]]
    }


def test_quality_keyboard_uses_only_inspected_qualities():
    qualities = [
        QualityOption("best", "⭐ الأفضل", "best-selector", None),
        QualityOption("2160", "2160p", "selector-2160", 30),
        QualityOption("720", "720p", "selector-720", 20),
    ]

    markup = quality_keyboard("abc123", qualities)

    values = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert values == [
        "pick:q:abc123:best",
        "pick:q:abc123:2160",
        "pick:q:abc123:720",
    ]


def test_callback_parser_accepts_dynamic_real_quality_shapes():
    assert parse_callback("pick:v:abc123") == ("video", "abc123", None)
    assert parse_callback("pick:a:abc123") == ("audio", "abc123", None)
    assert parse_callback("pick:q:abc123:720") == ("quality", "abc123", "720")
    assert parse_callback("pick:q:abc123:2160") == ("quality", "abc123", "2160")
    assert parse_callback("pick:q:abc123:4320") == ("quality", "abc123", "4320")
    assert parse_callback("pick:q:abc123:9999") is None
    assert parse_callback("mp3:old") is None
    assert parse_callback("pick:q:abc123:720:extra") is None


@pytest.mark.asyncio
async def test_video_callback_inspects_and_stores_only_real_qualities(monkeypatch, tmp_path):
    api = FakeAPI()
    settings = SimpleNamespace(storage_path=tmp_path)
    flow = TelegramFlow(api=api, settings=settings)
    token = flow.selections.create(
        owner_id=7,
        value={"url": "https://youtu.be/kTRs4ycFp_o", "title": "", "qualities": [], "thumbnail": ""},
    )

    calls = []

    async def fake_inspect(chat_id, message_id, url):
        calls.append(url)
        return {
            "title": "4K sample",
            "thumbnail": "",
            "formats": [
                {"height": 2160, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "filesize": 80},
                {"height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 30},
                {"height": None, "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 5},
            ],
        }

    monkeypatch.setattr(flow, "_inspect_with_progress", fake_inspect)

    await flow._handle_callback({
        "id": "cb1",
        "data": f"pick:v:{token}",
        "from": {"id": 7},
        "message": {"message_id": 11, "chat": {"id": 99}},
    })

    session = flow.selections.get(token, 7)
    assert calls == ["https://youtu.be/kTRs4ycFp_o"]
    assert session["title"] == "4K sample"
    assert [item.key for item in session["qualities"]] == ["best", "2160", "720"]
    values = [
        button["callback_data"]
        for row in api.markups[-1]["inline_keyboard"]
        for button in row
    ]
    assert values == [
        f"pick:q:{token}:best",
        f"pick:q:{token}:2160",
        f"pick:q:{token}:720",
    ]


@pytest.mark.asyncio
async def test_video_callback_never_invents_qualities_when_inspection_fails(monkeypatch, tmp_path):
    api = FakeAPI()
    flow = TelegramFlow(api=api, settings=SimpleNamespace(storage_path=tmp_path))
    token = flow.selections.create(
        owner_id=7,
        value={"url": "https://example.com/video", "title": "", "qualities": [], "thumbnail": ""},
    )

    async def failed_inspection(chat_id, message_id, url):
        raise RuntimeError("inspection unavailable")

    monkeypatch.setattr(flow, "_inspect_with_progress", failed_inspection)

    await flow._handle_callback({
        "id": "cb2",
        "data": f"pick:v:{token}",
        "from": {"id": 7},
        "message": {"message_id": 12, "chat": {"id": 99}},
    })

    session = flow.selections.get(token, 7)
    assert [item.key for item in session["qualities"]] == ["best"]
    values = [
        button["callback_data"]
        for row in api.markups[-1]["inline_keyboard"]
        for button in row
    ]
    assert values == [f"pick:q:{token}:best"]
    assert any("تعذر فحص الجودات" in text for text in api.edits)


def test_progress_text_is_consistent_for_every_stage():
    assert progress_text("downloading", 40) == "⬇️ جارٍ التحميل [█████░░░░░░░] 40%"
    assert progress_text("preparing", 20) == "🔧 جارٍ تجهيز الفيديو [██░░░░░░░░░░] 20%"
    assert progress_text("converting", 70) == "🎧 جارٍ التحويل إلى MP3 [████████░░░░] 70%"
    assert progress_text("uploading", 100) == "⬆️ جارٍ الرفع [████████████] 100%"

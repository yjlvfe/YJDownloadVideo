import pytest

from app import vercel_remote


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        "pick:v:abc_123",
        "pick:a:abc_123",
        "pick:q:abc_123:360",
        "pick:q:abc_123:original",
    ],
)
async def test_forwards_media_choice_callback_queries(monkeypatch, callback_data: str):
    forwarded: list[dict] = []

    async def fake_forward(data: dict) -> dict:
        forwarded.append(data)
        return {"accepted": True}

    monkeypatch.setattr(vercel_remote, "forward_telegram_update", fake_forward)
    update = {
        "update_id": 77,
        "callback_query": {
            "id": "callback-1",
            "data": callback_data,
            "from": {"id": 9},
            "message": {"message_id": 5, "chat": {"id": 9}},
        },
    }

    assert await vercel_remote.handle_vercel_remote_update(update) is True
    assert forwarded == [update]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        "mp3:55",
        "pick:v:x",
        "pick:q:abc_123:not-a-quality",
        "other:55",
    ],
)
async def test_ignores_unrelated_or_invalid_callback_queries(
    monkeypatch, callback_data: str
):
    async def fail_forward(_data: dict) -> dict:
        raise AssertionError("invalid callbacks must not be forwarded")

    monkeypatch.setattr(vercel_remote, "forward_telegram_update", fail_forward)
    assert (
        await vercel_remote.handle_vercel_remote_update(
            {
                "update_id": 78,
                "callback_query": {"data": callback_data},
            }
        )
        is False
    )

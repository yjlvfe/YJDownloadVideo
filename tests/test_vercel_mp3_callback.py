import pytest

from app import vercel_remote


@pytest.mark.asyncio
async def test_forwards_mp3_callback_query(monkeypatch):
    forwarded: list[dict] = []

    async def fake_forward(data: dict) -> dict:
        forwarded.append(data)
        return {"accepted": True}

    monkeypatch.setattr(vercel_remote, "forward_telegram_update", fake_forward)
    update = {
        "update_id": 77,
        "callback_query": {
            "id": "callback-1",
            "data": "mp3:55",
            "from": {"id": 9},
            "message": {"message_id": 5, "chat": {"id": 9}},
        },
    }

    assert await vercel_remote.handle_vercel_remote_update(update) is True
    assert forwarded == [update]


@pytest.mark.asyncio
async def test_ignores_unrelated_callback_query(monkeypatch):
    async def fail_forward(_data: dict) -> dict:
        raise AssertionError("unrelated callbacks must not be forwarded")

    monkeypatch.setattr(vercel_remote, "forward_telegram_update", fail_forward)
    assert (
        await vercel_remote.handle_vercel_remote_update(
            {"update_id": 78, "callback_query": {"data": "other:55"}}
        )
        is False
    )

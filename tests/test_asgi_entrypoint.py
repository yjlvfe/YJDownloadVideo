import asyncio
import json
from pathlib import Path

from app.asgi import RateLimitedASGI
from app.rate_limit import SlidingWindowLimiter


async def _request(app, path: str, client: str = "203.0.113.10") -> tuple[int, dict]:
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "client": (client, 12345),
    }
    await app(scope, receive, send)
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return status, json.loads(body or b"{}")


def test_public_media_routes_are_rate_limited() -> None:
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    wrapper = RateLimitedASGI(
        downstream,
        limiter=SlidingWindowLimiter(clock=lambda: 1000.0),
        public_limit=1,
    )

    first = asyncio.run(_request(wrapper, "/api/inspect"))
    second = asyncio.run(_request(wrapper, "/api/inspect"))
    other_route = asyncio.run(_request(wrapper, "/health"))

    assert first[0] == 200
    assert second == (429, {"detail": "تم تجاوز حد الطلبات، حاول لاحقًا"})
    assert other_route[0] == 200


def test_vercel_and_render_use_the_same_asgi_entrypoint() -> None:
    vercel = Path("api/index.py").read_text(encoding="utf-8")
    render = Path("docker/start-render.sh").read_text(encoding="utf-8")

    assert "from app.asgi import app" in vercel
    assert "uvicorn app.asgi:app" in render

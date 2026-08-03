from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .config import settings
from .main import app as fastapi_app
from .rate_limit import SlidingWindowLimiter


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]


class RateLimitedASGI:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowLimiter | None = None,
        public_limit: int | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter or SlidingWindowLimiter()
        self.public_limit = max(
            1,
            int(settings.rate_limit_per_minute if public_limit is None else public_limit),
        )

    @staticmethod
    def _client_key(scope: dict[str, Any]) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])
        return "unknown"

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if method == "POST" and path in {"/api/inspect", "/api/jobs"}:
            client = self._client_key(scope)
            allowed = self.limiter.allow(
                f"{path}:{client}",
                limit=self.public_limit,
                window_seconds=60,
            )
            if not allowed:
                body = json.dumps(
                    {"detail": "تم تجاوز حد الطلبات، حاول لاحقًا"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"retry-after", b"60"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)


app = RateLimitedASGI(fastapi_app)

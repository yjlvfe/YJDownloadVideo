from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import zlib

_SAFE_REQUEST_HEADERS = ("User-Agent", "Referer", "Origin", "Accept-Language")
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def proxy_headers(source_headers: dict | None, range_header: str | None = None) -> dict[str, str]:
    source_headers = source_headers or {}
    result: dict[str, str] = {}
    for name in _SAFE_REQUEST_HEADERS:
        value = source_headers.get(name) or source_headers.get(name.lower())
        if value:
            result[name] = str(value)
    result.setdefault("User-Agent", _DEFAULT_USER_AGENT)
    result["Accept"] = "*/*"
    result["Accept-Encoding"] = "identity"
    if range_header and range_header.startswith("bytes=") and len(range_header) <= 100:
        result["Range"] = range_header
    return result


def make_proxy_token(
    *,
    media_url: str,
    source_url: str,
    headers: dict | None,
    secret: str,
    now: int | None = None,
    ttl_seconds: int = 600,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "media_url": media_url,
        "source_url": source_url,
        "headers": proxy_headers(headers),
        "exp": issued_at + max(30, min(int(ttl_seconds), 1800)),
    }
    packed = zlib.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), 9)
    body = _b64encode(packed)
    signature = _b64encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def read_proxy_token(token: str, *, secret: str, now: int | None = None) -> dict:
    try:
        body, supplied_signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid token") from exc

    expected_signature = _b64encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("invalid signature")

    try:
        payload = json.loads(zlib.decompress(_b64decode(body)).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid token") from exc

    current = int(time.time() if now is None else now)
    if int(payload.get("exp") or 0) < current:
        raise ValueError("expired token")
    if not str(payload.get("media_url") or "").startswith(("https://", "http://")):
        raise ValueError("invalid media URL")
    return payload

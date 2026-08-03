from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Callable

import httpx

from .config import settings

_LEGACY_PREFIX = b"yjdownload-worker-v1"
_PREFIX = b"yjdownload-worker-v2"
ALLOWED_TELEGRAM_METHODS = frozenset(
    {
        "sendMessage",
        "editMessageText",
        "editMessageReplyMarkup",
        "deleteMessage",
        "answerCallbackQuery",
        "sendPhoto",
        "sendVideo",
        "sendDocument",
        "sendAudio",
        "getMe",
        "getWebhookInfo",
        "setWebhook",
        "deleteWebhook",
    }
)


class RemoteWorkerError(RuntimeError):
    """Safe failure raised when the Render worker cannot complete a request."""


def _worker_secret() -> str:
    return settings.worker_shared_secret or settings.telegram_bot_token


def worker_auth_mode() -> str:
    return "dedicated" if settings.worker_shared_secret else "legacy"


def create_worker_signature(
    token: str,
    body: bytes,
    context: str = "telegram-update",
    *,
    timestamp: int | str | None = None,
    nonce: str | None = None,
) -> str:
    if not token:
        raise ValueError("worker secret is required")
    if not context:
        raise ValueError("signature context is required")
    if (timestamp is None) != (nonce is None):
        raise ValueError("timestamp and nonce must be provided together")

    digest = hmac.new(token.encode("utf-8"), digestmod=hashlib.sha256)
    if timestamp is None:
        # Transitional helper retained for old tests and rollback compatibility.
        digest.update(_LEGACY_PREFIX)
        digest.update(b"\0")
        digest.update(context.encode("utf-8"))
        digest.update(b"\0")
        digest.update(body)
        return digest.hexdigest()

    normalized_nonce = str(nonce or "").strip()
    if not normalized_nonce:
        raise ValueError("nonce is required")
    normalized_timestamp = str(int(timestamp))
    digest.update(_PREFIX)
    digest.update(b"\0")
    digest.update(normalized_timestamp.encode("ascii"))
    digest.update(b"\0")
    digest.update(normalized_nonce.encode("utf-8"))
    digest.update(b"\0")
    digest.update(context.encode("utf-8"))
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def verify_worker_signature(
    token: str,
    body: bytes,
    signature: str,
    context: str = "telegram-update",
) -> bool:
    """Verify only the legacy v1 format during the migration window."""
    if not isinstance(signature, str) or len(signature) != 64:
        return False
    expected = create_worker_signature(token, body, context)
    return hmac.compare_digest(expected, signature.lower())


def verify_worker_request(
    secret: str,
    body: bytes,
    signature: str,
    context: str,
    *,
    timestamp: str,
    nonce: str,
    consume_nonce: Callable[[str, float], bool],
    now: float | None = None,
    max_age_seconds: int = 300,
    future_skew_seconds: int = 30,
) -> bool:
    if not secret or not isinstance(signature, str) or len(signature) != 64:
        return False
    normalized_nonce = str(nonce or "").strip()
    if not normalized_nonce or len(normalized_nonce) > 160:
        return False
    try:
        issued_at = int(timestamp)
    except (TypeError, ValueError):
        return False

    current = float(time.time() if now is None else now)
    if issued_at > current + max(0, int(future_skew_seconds)):
        return False
    if current - issued_at > max(1, int(max_age_seconds)):
        return False

    expected = create_worker_signature(
        secret,
        body,
        context,
        timestamp=issued_at,
        nonce=normalized_nonce,
    )
    if not hmac.compare_digest(expected, signature.lower()):
        return False
    return bool(consume_nonce(normalized_nonce, issued_at + max_age_seconds))


def worker_endpoint(origin: str, path: str) -> str:
    if not origin.startswith(("https://", "http://")):
        raise ValueError("remote worker URL must use HTTP or HTTPS")
    return f"{origin.rstrip('/')}/{path.lstrip('/')}"


def _body(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


async def _post_signed(
    path: str,
    payload: dict[str, Any],
    *,
    context: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not settings.remote_worker_url:
        raise RemoteWorkerError("خادم معالجة الملفات غير مضبوط")
    secret = _worker_secret()
    if not secret:
        raise RemoteWorkerError("سر الاتصال بخادم المعالجة غير مضبوط")

    raw = _body(payload)
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(24)
    signature = create_worker_signature(
        secret,
        raw,
        context,
        timestamp=timestamp,
        nonce=nonce,
    )
    timeout = httpx.Timeout(timeout_seconds, connect=min(20.0, timeout_seconds))
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                worker_endpoint(settings.remote_worker_url, path),
                content=raw,
                headers={
                    "content-type": "application/json",
                    "x-telegram-worker-auth": signature,
                    "x-worker-timestamp": str(timestamp),
                    "x-worker-nonce": nonce,
                    "x-worker-version": "2",
                },
            )
    except httpx.TimeoutException as exc:
        raise RemoteWorkerError("خادم المعالجة استغرق وقتًا طويلًا في الاستجابة") from exc
    except httpx.HTTPError as exc:
        raise RemoteWorkerError("تعذر الاتصال بخادم معالجة الملفات") from exc

    try:
        result = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RemoteWorkerError(f"خادم المعالجة أعاد HTTP {response.status_code}") from exc

    if response.status_code >= 500:
        raise RemoteWorkerError("خادم معالجة الملفات غير جاهز حاليًا")
    if response.status_code in {401, 403}:
        raise RemoteWorkerError("رفض خادم المعالجة المصادقة")
    if response.status_code >= 400:
        reason = str(result.get("reason") or result.get("error") or "تعذر قبول الطلب")
        raise RemoteWorkerError(reason[:180])
    return result if isinstance(result, dict) else {}


async def forward_telegram_update(data: dict[str, Any]) -> dict[str, Any]:
    return await _post_signed(
        "/telegram/update",
        data,
        context="telegram-update",
        timeout_seconds=25.0,
    )


async def telegram_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if method not in ALLOWED_TELEGRAM_METHODS:
        raise ValueError("unsupported Telegram method")
    response = await _post_signed(
        f"/telegram/call/{method}",
        payload,
        context=f"telegram-call:{method}",
        timeout_seconds=90.0,
    )
    if not response.get("ok"):
        description = str(response.get("description") or "Telegram request failed")
        raise RemoteWorkerError(description[:200])
    result = response.get("result")
    return result if isinstance(result, dict) else {}


async def remote_worker_health() -> dict[str, Any]:
    if not settings.remote_worker_url:
        return {"ok": False, "error": "not_configured"}
    timeout = httpx.Timeout(20.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(worker_endpoint(settings.remote_worker_url, "/health"))
        body = response.json()
        if response.status_code >= 400 or not isinstance(body, dict):
            return {"ok": False, "error": "invalid_response"}
        return body
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unreachable"}

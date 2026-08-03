from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from .config import settings

_PREFIX = b"yjdownload-worker-v1"
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


def create_worker_signature(
    token: str,
    body: bytes,
    context: str = "telegram-update",
) -> str:
    if not token:
        raise ValueError("bot token is required")
    if not context:
        raise ValueError("signature context is required")
    digest = hmac.new(token.encode("utf-8"), digestmod=hashlib.sha256)
    digest.update(_PREFIX)
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
    if not isinstance(signature, str) or len(signature) != 64:
        return False
    expected = create_worker_signature(token, body, context)
    return hmac.compare_digest(expected, signature.lower())


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
    if not settings.telegram_bot_token:
        raise RemoteWorkerError("توكن تيليجرام غير مضبوط")

    raw = _body(payload)
    signature = create_worker_signature(
        settings.telegram_bot_token,
        raw,
        context,
    )
    timeout = httpx.Timeout(timeout_seconds, connect=min(45.0, timeout_seconds))
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
        timeout_seconds=90.0,
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
    timeout = httpx.Timeout(45.0, connect=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(worker_endpoint(settings.remote_worker_url, "/health"))
        body = response.json()
        if response.status_code >= 400 or not isinstance(body, dict):
            return {"ok": False, "error": "invalid_response"}
        return body
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unreachable"}

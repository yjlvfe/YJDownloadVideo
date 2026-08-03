"""
app/mtproto.py — إرسال الملفات الكبيرة (حتى 2GB) عبر حساب المستخدم الشخصي (MTProto).

لماذا؟ بوتات Telegram (Bot API) لها حد صارم 50MB لا يمكن تجاوزه.
لكن الحساب الشخصي (عبر MTProto/Telethon) يرسل حتى 2GB.
الجلسة تُحفظ لكل مستخدم في data/sessions/ ويبقى الإرسال من حسابه هو.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import settings

log = logging.getLogger("mtproto")

_clients: dict[int, object] = {}  # user_id -> TelegramClient


def _session_path(user_id: int) -> Path:
    return settings.sessions_path / f"tg_{user_id}.session"


def has_session(user_id: int) -> bool:
    return _session_path(user_id).exists()


def _check_creds():
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH غير مضبوطين في .env")


async def _client(user_id: int):
    """عميل Telethon واحد لكل مستخدم (الجلسة محفوظة على القرص)."""
    if user_id in _clients:
        return _clients[user_id]
    _check_creds()
    from telethon import TelegramClient
    cli = TelegramClient(str(_session_path(user_id)),
                         settings.telegram_api_id, settings.telegram_api_hash)
    await cli.connect()
    _clients[user_id] = cli
    return cli


async def start_login(user_id: int, phone: str) -> str:
    """يرسل رمز التحقق لرقم الهاتف. يعيد 'code_sent' أو 'already'."""
    cli = await _client(user_id)
    if await cli.is_user_authorized():
        return "already"
    from telethon.errors import PhoneNumberInvalidError
    try:
        await cli.send_code_request(phone)
    except PhoneNumberInvalidError:
        return "bad_phone"
    except Exception as e:  # noqa: BLE001
        log.exception("send_code_request failed")
        return f"error: {str(e)[:150]}"
    return "code_sent"


async def complete_login(user_id: int, phone: str, code: str) -> str:
    """يكمل تسجيل الدخول بالرمز. يعيد 'ok' أو '2fa' أو 'bad_code'."""
    cli = await _client(user_id)
    from telethon.errors import (SessionPasswordNeededError,
                                 PhoneCodeInvalidError, PhoneCodeExpiredError)
    try:
        await cli.sign_in(phone, code=code.strip())
        return "ok"
    except SessionPasswordNeededError:
        return "2fa"
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        return "bad_code"
    except Exception as e:  # noqa: BLE001
        log.exception("sign_in failed")
        return f"error: {str(e)[:150]}"


async def complete_2fa(user_id: int, password: str) -> str:
    """كلمة مرور الخطوتين (إن كانت مفعلة)."""
    cli = await _client(user_id)
    from telethon.errors import PasswordHashInvalidError
    try:
        await cli.sign_in(password=password)
        return "ok"
    except PasswordHashInvalidError:
        return "bad_password"
    except Exception as e:  # noqa: BLE001
        log.exception("2fa sign_in failed")
        return f"error: {str(e)[:150]}"


async def send_file(user_id: int, chat_id: int, path: str, caption: str = "") -> bool:
    """يرسل ملفًا (حتى 2GB) من حساب المستخدم الشخصي إلى المحادثة المعطاة."""
    cli = await _client(user_id)
    if not await cli.is_user_authorized():
        # جلسة مكسورة/منتهية — نظّفها تلقائيًا حتى لا تسبب فشلًا متكررًا
        await logout(user_id)
        raise RuntimeError("not_authorized")
    await cli.send_file(chat_id, file=str(path), caption=caption or None)
    return True


async def logout(user_id: int) -> None:
    cli = _clients.pop(user_id, None)
    if cli is not None:
        try:
            await cli.log_out()
        except Exception:  # noqa: BLE001
            pass
        try:
            await cli.disconnect()
        except Exception:  # noqa: BLE001
            pass
    _session_path(user_id).unlink(missing_ok=True)
    # telethon قد يخلق ملفات مساعدة بامتدادات أخرى
    for p in settings.sessions_path.glob(f"tg_{user_id}.*"):
        p.unlink(missing_ok=True)

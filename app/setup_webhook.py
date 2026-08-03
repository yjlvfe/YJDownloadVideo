"""
ربط Webhook تيليجرام (للوضع غير المحلي) — يُشغَّل مرة واحدة:

    WEBHOOK_URL=https://<عنوانك>/webhook/telegram uv run python -m app.setup_webhook
"""
import asyncio, hashlib, os

from telegram import Bot


async def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    url = os.environ.get("WEBHOOK_URL")
    if not token or not url:
        raise SystemExit("ضع TELEGRAM_BOT_TOKEN و WEBHOOK_URL ثم أعد التشغيل")
    secret = hashlib.sha256(f"yjdownload-local-webhook-v1\0{token}".encode("utf-8")).hexdigest()
    bot = Bot(token)
    ok = await bot.set_webhook(
        url=url,
        secret_token=secret,
        allowed_updates=["message", "edited_message", "callback_query"],
        drop_pending_updates=True,
    )
    info = await bot.get_webhook_info()
    print("✅ تم ربط الويب هوك:", ok)
    print("   العنوان:", info.url)
    print("   سر التوقيع: مفعل")


if __name__ == "__main__":
    asyncio.run(main())

"""
ربط Webhook تيليجرام بعد نشر Vercel — يُشغَّل مرة واحدة من جهازك:

    WEBHOOK_URL=https://<مشروعك>.vercel.app/webhook/telegram uv run python -m app.setup_webhook
"""
import asyncio, os

from telegram import Bot


async def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    url = os.environ.get("WEBHOOK_URL")
    if not token or not url:
        raise SystemExit("ضع TELEGRAM_BOT_TOKEN و WEBHOOK_URL ثم أعد التشغيل")
    bot = Bot(token)
    ok = await bot.set_webhook(url)
    info = await bot.get_webhook_info()
    print("✅ تم ربط الويب هوك:", ok)
    print("   العنوان:", info.url)
    print("   ملاحظة: في وضع Vercel حد الإرسال 50MB (بدون خادم محلي) — المقاطع الأصغر تصل مباشرة.")


if __name__ == "__main__":
    asyncio.run(main())

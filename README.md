# Current route override — owner-approved NordVPN trial

Default docker-compose.yml now uses Germany NordLynx via socks5://nordvpn:1080 for downloads only. Keep 64 fragments and unlimited admission; Telegram uploads remain direct. Existing Redis/TBA volumes are preserved. WARP standby: docker-compose.warp-standby.yml. The WARP /ip watcher is stopped and disabled; /ip rotation is unavailable during this trial. This override supersedes historical WARP route statements below.

# 🚀 YJDownloadVideo

نظام احترافي لتحميل الفيديو والصوت وألبومات الصور عبر Telegram، مبني على Docker وRedis وSQLite وyt-dlp وFFmpeg.

> استخدم النظام فقط للمحتوى الذي تملك حق تنزيله. لا يدعم المشروع تجاوز DRM أو الحماية التقنية أو الوصول غير المصرح به.

## المميزات

- دعم YouTube وTikTok وInstagram وX/Twitter وFacebook ومصادر ويب متعددة عبر yt-dlp.
- دعم مشغلات HLS/CDN وبعض بوابات JWPlayer ومصادر البث المتوافقة.
- تحميل فيديو أو صوت أو ألبومات صور TikTok.
- Telegram Bot API محلي لرفع ملفات كبيرة حتى 2GB حسب إعدادات Telegram.
- طابور Redis غير محدود: أول المهام تعمل مباشرة، والباقي ينتظر تلقائياً ولا يُرفض.
- استرداد المهام بعد restart اعتماداً على SQLite وملفات الحالة وheartbeat حقيقي.
- المسار الحالي لـYJ هو Cloudflare WARP داخل Docker منفصل، مع HTTP proxy داخلي `http://warp:9002`.
- NordVPN محفوظ كخطة رجوع مستقلة ولا يُستخدم لمسار YJ الحالي إلا بعد طلب صريح.
- تشغيل طبيعي بدون VPN متاح فقط كخيار تشخيصي، وليس مسار التشغيل الحالي.

## المتطلبات

- Docker Engine
- Docker Compose v2
- Telegram bot token
- `TELEGRAM_API_ID` و`TELEGRAM_API_HASH` لخادم Telegram Bot API المحلي
- `NORDVPN_TOKEN` اختياري فقط عند تفعيل وضع VPN

## التشغيل السريع

```bash
git clone https://github.com/yjlvfe/YJSocialMediaDownload.git
cd YJSocialMediaDownload
cp .env.example .env
```

عدّل `.env` وضع القيم المطلوبة:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
SECRET_KEY=<قيمة عشوائية طويلة>
```

### التشغيل الافتراضي المعتمد: WARP

`docker-compose.yml` هو مصدر التشغيل الافتراضي ويستخدم WARP، وليس NordVPN. ينشر `deploy.sh` المصدر الحالي عبر Compose والبناء، ولا ينسخ كوداً مؤقتاً داخل الحاويات. خدمات التطبيق تستخدم `YTDLP_PROXY=http://warp:9002` وتوازي fragments بقيمة `32`. سياسات `restart: unless-stopped` تعيد الحاويات الحالية بعد إقلاع Docker؛ لا يوجد تحويل تلقائي إلى NordVPN.

```bash
docker compose --env-file .env up -d --build --remove-orphans
```

هذا ربط HTTP proxy لتنزيلات yt-dlp فقط. رفع الملفات إلى Telegram يمر عبر Bot API المحلي `http://tba:8081` باستخدام HTTPX مع `trust_env=False`، أي عبر شبكة VPS المباشرة وليس WARP. Redis وBot API يبقيان على الشبكة الداخلية. تم اختبار stop/start للمشروع وعودة `warp=on`؛ لم تتم إعادة تشغيل السيرفر نفسه.

### NordVPN احتياطي — بطلب صريح فقط

الملف الاحتياطي هو `docker-compose.nordvpn.yml`. للرجوع اليدوي فقط:

```bash
docker compose down --remove-orphans
docker compose -f docker-compose.nordvpn.yml --env-file .env up -d --build --remove-orphans
```

الأوامر القديمة أدناه تخص الوضع الاحتياطي، ويجب استخدام `-f docker-compose.nordvpn.yml` عند تشغيله.

### إعداد اعتماد NordVPN الاحتياطي

ضع توكن NordVPN في `.env`:

```env
NORDVPN_TOKEN=...
# المسار المقاس حالياً لـ YJ هو ألمانيا؛ غيّره عند حاجة موقع لمنطقة محددة.
NORDVPN_CONNECT=Germany
# اتركه فارغاً لاختيار NordVPN خادماً موصى به تلقائياً.
```

ثم شغّل:

```bash
docker compose up -d --build
```

تحقق من جاهزية VPN قبل اعتبار الوضع جاهزاً:

```bash
docker compose ps
docker compose logs --tail=100 nordvpn
```

### وضع بدون VPN

للمحتوى العام أو عندما لا تريد استخدام NordVPN:

```bash
docker compose -f docker-compose.novpn.yml --env-file .env up -d --build
```

في هذا الوضع لا تحتاج `NORDVPN_TOKEN`. قد تفشل المواقع المحجوبة جغرافياً أو التي تتطلب جلسة/كوكيز، وهذا متوقع.

## الطابور والسرعة

الإعداد الافتراضي المصمم لنفق VPN واحد وIP واحد:

```env
WORKER_MAX_WORKERS=4
MAX_ACTIVE_JOBS_PER_IP=4
MAX_ACTIVE_JOBS_PER_TELEGRAM_USER=4
MAX_FRAGMENT_CONNECTIONS_PER_JOB=16
```

- يمكن إرسال 20 مقطعاً أو أكثر؛ لا يتم رفض الطلبات الزائدة.
- تعمل 4 مهام فعلياً، والباقي في `waiting` داخل Redis.
- عند انتهاء مهمة يبدأ الطلب التالي تلقائياً.
- لا ترفع عدد workers عشوائياً فوق قدرة VPN/IP؛ زيادة التوازي قد تقسم السرعة وتسبب throttling.
- عند تشغيل أنفاق VPN مستقلة وIPs مختلفة يمكن رفع العدد بعد قياس throughput والأخطاء.
- أخطاء النقل المؤقتة مثل Redis socket timeout أو `The downloaded file is empty` تُعاد محاولتها تلقائياً بنفس الطلب ضمن حدٍّ مضبوط؛ أخطاء الموقع الدائمة لا تُعاد بلا نهاية ولا تُسبب تبديل VPN.

## مصدر الكود والنشر

- ملفات `app/` في المستودع هي مصدر الحقيقة الوحيد.
- Compose يربط `./app:/app/app` حتى لا تعود الحاويات إلى نسخة قديمة.
- لا تعتمد على تعديل داخل الحاوية أو `docker cp`.
- لا ترفع `.env` أو التوكنات أو ملفات الكوكيز إلى GitHub.
- مشروع YJ معزول عن `CryptoVPN` الخاص بـ `BinanceChecker` و`MyWalletChecker`؛ لا تستخدم إعدادات YJ لتغيير مسار CryptoVPN.

أوامر التحقق:

```bash
docker compose config
python -m compileall -q app
docker compose ps
docker compose logs -f --tail=100 worker
```

بعد تعديل المصدر:

```bash
docker compose build worker bot web
docker compose up -d --force-recreate worker bot web
```

## الترخيص

مشروع مفتوح المصدر للاستخدام الشخصي والمطورين، مع الالتزام بالقوانين وشروط المواقع ومصادر المحتوى.

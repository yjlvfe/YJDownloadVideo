# YJSocialMediaDownload 🎬

**أقوى برنامج تحميل فيديو — أي رابط في العالم يحتوي فيديو يتم تحميله فورًا.**
ليس فقط السوشيال ميديا: يوتيوب (مقاطع + شورتس)، تيك توك، إنستغرام ريلز، X، فيسبوك،
Pornhub، صفحات عادية، مشغّلات JS، بث HLS/DASH، روابط مباشرة — أي موقع.

> هذا المستودع خاص ويحتوي المفاتيح كاملة (`.env`) — نسخة احتياطية كاملة لأي جهاز.
> لا تشاركه مع أحد ولا ترفع `.env` لأي مستودع عام.

---

## ✨ المميزات

| الميزة | الوصف |
|---|---|
| 🌐 **أي موقع** | محرك استخراج من 3 طبقات (أدناه) — لا قيود على فئة مواقع |
| 🤖 **بوت تيليجرام كامل** | أرسل رابط → شريط تقدم حي → المقطع يوصلك في المحادثة |
| 📦 **حتى 2GB** | عبر خادم Bot API المحلي (tba) — بدون ربط أي حساب شخصي |
| 🎵 **فيديو أو صوت** | `/audio` صوت فقط (m4a) — `/video` العودة للفيديو |
| 🎧 **زر MP3** | تحت كل مقطع: ضغطة → تحويل فوري بجودة عالية |
| 📥 **طابور ذكي** | روابط متعددة دفعة واحدة — تُقبل كلها وتُعالج بالترتيب (FIFO) |
| 📊 **تقدم تفاعلي** | رسالة واحدة بشريط `█░` يتحدث كل 10%: تحميل ← رفع ← حذف ← المقطع |
| 🗑️ **حذف تلقائي 24 ساعة** | لا تراكم على السيرفر إطلاقًا |
| 🌐 **واجهة ويب** | RTL عربية كاملة + REST API |
| 🚀 **نشر بأمر واحد** | `deploy.sh` على أي سيرفر Ubuntu جديد |

---

## 🧠 المعمارية (كيف يشتغل)

نظام Docker Compose من 6 حاويات:

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  web        │   │  worker     │   │  bot        │
│  FastAPI    │   │  yt-dlp +   │   │  PTB v21    │
│  واجهة + API│   │  محرك 3 طبقات│   │  polling    │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                │
       └─────────┬───────┴───────┬────────┘
                 │               │
           ┌─────┴─────┐   ┌─────┴──────┐
           │  redis    │   │  tba       │
           │  طابور    │   │  Bot API   │
           │  jobs     │   │  محلي 2GB  │
           └───────────┘   └────────────┘
   + cleanup (حذف بعد 24 ساعة)
```

- **web** — FastAPI: الواجهة + `POST /api/inspect` + `POST /api/jobs` + `GET /download/<token>`
- **worker** — `python -m app.worker`: يسحب المهام من Redis (`blpop`) ويعالجها **واحدًا تلو الآخر** (طابور حقيقي) ويمرر التقدم لملفات الحالة
- **bot** — python-telegram-bot: يستقبل الروابط، يشغّل شريط التقدم، يرسل الملفات
- **tba** — خادم Bot API الرسمي (aiogram/telegram-bot-api) بوضع `--local`: **يرفع حد إرسال البوت من 50MB إلى 2GB**
- **cleanup** — كل 5 دقائق يحذف أي ملف انتهت صلاحيته (24 ساعة)

### قاعدة البيانات والملفات

- SQLite في `data/yjdownload.db` — يسجل **الطلبات والحالات فقط**
- الملفات في `storage/<job_id>/` — مجلد لكل مهمة (تنظيف سهل)
- حالة التقدم في `data/status/<job_id>.json` — جسر بين الـ worker والبوت

---

## 🕵️ محرك الاستخراج الشامل (Universal Extractor) — `app/extractor.py`

ثلاث طبقات هجومية، كل فشل ينقلك للتالية:

| الطبقة | الآلية | تلتقط |
|---|---|---|
| **L1** | yt-dlp (كل المستخرجات) | يوتيوب، تيك توك، إنستغرام، X، فيسبوك… |
| **L2** | ماسح HTML (`curl_cffi` بانتحال بصمة Chrome) | وسوم `<video>`، og:video، JSON-LD، إعدادات المشغّل في JS، روابط CDN بلا امتداد |
| **L3** | متصفح خفي (Chromium + CDP) | أي مشغّل JavaScript بحت — التقاط طلبات الشبكة + كوكيز الجلسة |

### الحروب التي انتُصرت فيها (لا تعد اختراعها من جديد)

1. **يوتيوب "Failed to extract any player response"** → `extractor_args={"youtube": {"player_client": ["android", "web", "ios"]}}` (لا تضف `tv` أبدًا — يرجع DRM)
2. **ترويسة `Accept-Encoding: br` اليدوية تكسر استخراج يوتيوب/إنستغرام/تيك توك** → احذفها من هيدرات yt-dlp (`_ytdlp_headers()`)
3. **تيك توك HEVC يكذب**: يعلن `acodec: aac` والملف بلا صوت! → افحص بـ `ffprobe` (`_has_audio`) وأعد المحاولة بـ `best[ext=mp4][vcodec^=h264]` — وباسم ملف `_retry` مؤقت (نفس الاسم يحذف الملف الجديد!)
4. **Pornhub يرد 410 على yt-dlp/httpx** (بصمة TLS) → `curl_cffi impersonate="chrome"` — ولاحظ: API أسلوب requests (`allow_redirects`)
5. **روابط CDN بلا امتداد** (googlevideo `videoplayback?mime=video`) → نمط إضافي + فك `\u0026`/`\/` (`_unescape_js`)
6. **ترجيح المرشحين**: +40 لرابط يحوي معرّف الفيديو، +25 لـ HLS، +30 للدقة — وفلترة الإعلانات/المصغرات
7. **`restrictfilenames=True` يمسح العربية** → اجعله `False` (العناوين العربية تبقى)
8. **تحقق مزدوج**: البايتات السحرية (`ftyp`/`1A45DFA3`) + فحص الصوت — لا ملف مكسور يخرج

---

## 🚀 التشغيل (أي سيرفر VPS — Ubuntu/Debian)

### الطريقة السريعة (أمر واحد — يثبّت Docker وكل شيء):

```bash
bash deploy.sh
```

### يدويًا:

```bash
git clone https://github.com/yjlvfe/YJSocialMediaDownload.git
cd YJSocialMediaDownload

# .env موجود أصلًا في المستودع (خاص) — عدّل BASE_URL لسيرفرك
docker compose up --build -d
```

افتح: `http://<IP>:8000` — والبوت يشتغل فورًا.

### أوامر الإدارة:

```bash
docker compose logs -f bot      # سجلات البوت
docker compose logs -f worker   # سجلات التحميل
docker compose up -d            # إعادة تشغيل بعد تعديل .env
docker compose down             # إيقاف (البيانات تبقى)
```

---

## ⚙️ متغيرات البيئة (`.env`)

| المتغير | الافتراضي | الوصف |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | توكن البوت من BotFather (ضروري) |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | — | من my.telegram.org — ضروري لخادم 2GB |
| `BASE_URL` | localhost | عنوان السيرفر العام (لروابط التحميل) |
| `FILE_TTL_SECONDS` | 86400 | مدة بقاء الملف (24 ساعة ثم حذف) |
| `MAX_FILE_SIZE_BYTES` | 2000000000 | حد الحجم 2GB |
| `TELEGRAM_SEND_LIMIT_BYTES` | 2000000000 | متى يرسل البوت الملف مباشرة |
| `TELEGRAM_BOT_API_BASE` | `http://tba:8081` | خادم Bot API المحلي (لا تمسه في Docker) |
| `ENABLE_BROWSER_CAPTURE` | true | طبقة المتصفح الخفي (يتطلب Chromium داخل الحاوية — مثبت) |

⚠️ **تحذير حاسم:** حاوية `tba` تقرأ **متغيرات البيئة فقط** وتتجاهل `command:` — بدون `TELEGRAM_LOCAL=true` يبقى حد 50MB بصمت! تحقق: `docker exec yjdownloadvideo-tba-1 ps aux | grep -- --local`

---

## 🤖 أوامر البوت

| الأمر | الوظيفة |
|---|---|
| (أي رابط) | تحميل + إرسال مع شريط تقدم حي |
| `/audio` | المقاطع القادمة صوت فقط (m4a 192kbps) |
| `/video` | العودة للفيديو (الافتراضي) |
| زر **MP3 🎧** تحت المقطع | تحويل فوري لـ MP3 |
| `/clearcookies` | حذف كوكيز الجلسة المحفوظة |
| `/link2gb` | (احتياطي) ربط حساب شخصي لحالات >2GB النادرة |
| `/unlink2gb` | فصل الحساب الشخصي |

### تدفق المستخدم:

```
📥 تم استلام الرابط، جارٍ المعالجة… (🎬 فيديو)
⬇️ جارٍ التحميل [████░░░░░░░░] 40%
⬇️ جارٍ التحميل [████████████] 100%
✅ تم التحميل — ⬆️ جارٍ الرفع [████████████] 100%
🗑️ يُحذف الشريط ← 🎬 المقطع يوصلك + [ MP3 🎧 ]
```

---

## 🌐 واجهة الويب (REST API)

| المسار | الوظيفة |
|---|---|
| `GET /` | الواجهة (RTL) |
| `POST /api/inspect` | فحص رابط: العنوان + الجودات `{"url": "..."}` |
| `POST /api/jobs` | إنشاء مهمة `{"url": "...", "format_id": "best"}` |
| `GET /api/jobs/{id}` | حالة المهمة |
| `GET /download/{token}` | رابط تحميل مؤقت (ينتهي بعد 24 ساعة) |
| `GET /api/health` | صحة النظام |

---

## 🧪 الاختبارات

```bash
uv run --project . python -m pytest tests/ -q
```

والتحميل المباشر من الطرفية (اختبار سريع لأي رابط):

```bash
uv run --project . python cli.py "https://youtube.com/shorts/XXXX" -o /tmp/dl
uv run --project . python cli.py "https://...tiktok..." -q audio -o /tmp/dl
```

---

## 🛠️ استكشاف الأخطاء (دروس ميدانية — لا تكرر المعاناة)

| العَرَض | السبب | الحل |
|---|---|---|
| `413 Request Entity Too Large` | tba بدون `--local` | `TELEGRAM_LOCAL=true` في compose (انظر أعلاه) |
| `Bot.send_video() got an unexpected keyword argument 'progress'` | PTB v21 حذف `progress` | `app/progress_request.py` (لا تستخدم المعامل) |
| الملف ما يوصل + "Task exception was never retrieved" | `InputFile` يبلع الملف بالرام | `InputFile(f, read_file_handle=False)` |
| `TypeError: type object 'Document' has no attribute...` | `filters.Document` كلاس في PTB v21 | استخدم `filters.ATTACHMENT` |
| يوتيوب/تيك توك/إنستغرام "player response" | ترويسة br اليدوية | `_ytdlp_headers()` تحذفها |
| المقطع بدون صوت (تيك توك) | تنسيقات HEVC كاذبة | فحص `_has_audio` + إعادة بمصدر h264 |
| `1009 message too big` (المتصفح الخفي) | حد WebSocket 1MB | `connect(..., max_size=None)` |
| Chromium يموت بـ SIGTRAP في حاويات | حد RLIMIT_AS | `preexec_fn` يرفع الحد (موجود) |
| yt-dlp يرفض `http://127.0.0.1...` | حجب العناوين الخاصة | طبيعي — للاختبار المحلي استخدم طبقات L2/L3 |
| `send_video` مهلة 5 ثوانٍ | الخادم المحلي يرد بعد الرفع الكامل | مهلات 600 ثانية (مضبوطة) |

---

## 🔒 الأمان والملاحظات

- SSRF محمي: يرفض الروابط الداخلية/المحلية
- المفاتيح في `.env` داخل مستودع **خاص** — لا ترفعه عامًا أبدًا
- الاستخدام الشخصي للمحتوى الذي يحق لك الوصول إليه. DRM (Netflix/Shahid) غير مدعوم — فكّ التشفير مخالفة قانونية
- الملفات تُحذف تلقائيًا بعد 24 ساعة — لا تراكم

---

## 📁 هيكل المشروع

```
app/
  extractor.py         ← المحرك الشامل (3 طبقات)
  worker.py            ← طابور المعالجة + التقدم
  bot.py               ← بوت تيليجرام (شريط تقدم + MP3 + طابور)
  main.py              ← FastAPI + الويب هوك
  progress_request.py  ← تتبع رفع الملفات + إعادة المحاولة
  status.py            ← ملفات الحالة + تفضيلات المستخدمين
  mtproto.py           ← (احتياطي) إرسال >2GB عبر حساب شخصي
  security.py          ← SSRF + توكنات التحميل
  cleanup.py           ← الحذف بعد 24 ساعة
  db.py / config.py    ← القاعدة والإعدادات
cli.py                 ← تحميل من الطرفية
deploy.sh              ← نشر بأمر واحد
docker-compose.yml     ← 6 حاويات
```

---

## ❓ أي سؤال لأي ذكاء اصطناعي جديد على المشروع؟

1. اقرأ هذا الملف كاملًا
2. شغّل `docker compose up --build -d`
3. جرّب `cli.py` برابط يوتيوب ثم تيك توك ثم إنستغرام
4. لا تعدّل `extractor.py` بدون فهم الطبقات الثلاث والحروب الموثقة أعلاه

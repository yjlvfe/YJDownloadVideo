# Live production acceptance

Overall: **FAIL**

## tiktok
Inspection: **PASS**
- `best`: **PASS** — 5021299 bytes, video=True, audio=True, height=1024
- `1024`: **PASS** — 5021299 bytes, video=True, audio=True, height=1024
- `audio`: **PASS** — 684001 bytes, video=False, audio=True, height=0

## instagram
Inspection: **PASS**
- `best`: **PASS** — 8266977 bytes, video=True, audio=True, height=1914
- `960`: **PASS** — 2872047 bytes, video=True, audio=True, height=960
- `640`: **PASS** — 1106900 bytes, video=True, audio=True, height=640
- `audio`: **PASS** — 250720 bytes, video=False, audio=True, height=0

## youtube
Inspection: **FAIL**
- `unclassified`: `تعذر قراءة الفيديو: لم يُعثر على أي فيديو في هذا الرابط. yt-dlp: متخطى`

## youtube
Inspection: **PASS**
- `best`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `1080`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `720`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `480`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `audio`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`


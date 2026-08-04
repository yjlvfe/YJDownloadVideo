# Live production acceptance

Overall: **FAIL**

## tiktok
Inspection: **FAIL**
- `unclassified`: `HTTP 502: invalid JSON response`

## instagram
Inspection: **PASS**
- `best`: **PASS** — 8266977 bytes, video=True, audio=True, height=1914
- `960`: **PASS** — 2872047 bytes, video=True, audio=True, height=960
- `640`: **PASS** — 1106900 bytes, video=True, audio=True, height=640
- `audio`: **PASS** — 250720 bytes, video=False, audio=True, height=0

## youtube
Inspection: **FAIL**
- `unclassified`: `HTTP 502: invalid JSON response`

## youtube
Inspection: **PASS**
- `best`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة و`
- `1080`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة و`
- `720`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة و`
- `480`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة و`
- `audio`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة و`


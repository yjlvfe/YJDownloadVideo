# YouTube live acceptance

Overall: **FAIL**

## YouTube sample 1
Inspection: **FAIL**
Formats: `[]`
- `تعذر قراءة الفيديو: لم يُعثر على أي فيديو في هذا الرابط. yt-dlp: متخطى | html-scan: HTTP Error 429: `

## YouTube sample 2
Inspection: **PASS**
Formats: `['best', '360']`
- `best`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `360`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`
- `audio`: **FAIL** [youtube_fragment_forbidden] `فشل التحميل: youtube_fragment_forbidden 💡 يوتيوب يحجب خوادم الاستضافة (فحص روبوت). الحل المضمون: أضف كوكيز يوتيوب من متصفحك عبر متغير YOUTUBE_COOKIES_B64 في إعدادات الخادم، أو أرسل ملف كوكيز للبوت (Get cookies.txt) مرة واحدة.`


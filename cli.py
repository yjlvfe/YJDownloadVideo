#!/usr/bin/env python3
"""
YJ Universal Downloader — CLI
=============================
تحميل أي فيديو من أي رابط في العالم:

    python cli.py "https://أي-موقع/أي-صفحة"

خيارات:
    -o, --out DIR      مجلد الحفظ (افتراضي ./downloads)
    -q, --quality X    best | 1080 | 720 | 480 | audio
    -c, --cookies FILE ملف كوكيز Netscape (اختياري — لمواقع تحتاج تسجيل دخول)
    --no-browser       عطّل طبقة المتصفح الخفي (أبطأ لكن أشمل)
    --list             اعرض المرشحين المكتشفين فقط دون تحميل
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.extractor import UniversalExtractor, DownloadError  # noqa: E402

BAR = "█"


def hook(d):
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    done = d.get("downloaded_bytes") or 0
    if not total:
        print(f"\r⏳ جارٍ التحميل… {done / 1024 / 1024:.1f} MB", end="", flush=True)
        return
    pct = done / total * 100
    w = 28
    filled = int(pct / 100 * w)
    print(f"\r{BAR * filled}{'░' * (w - filled)} {pct:5.1f}%  {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB", end="", flush=True)


def main():
    ap = argparse.ArgumentParser(description="YJ Universal Downloader — أي رابط فيه فيديو يُحمَّل")
    ap.add_argument("url", help="رابط الصفحة أو الفيديو")
    ap.add_argument("-o", "--out", default="./downloads", help="مجلد الحفظ")
    ap.add_argument("-q", "--quality", default="best",
                    choices=["best", "1080", "720", "480", "audio"])
    ap.add_argument("-c", "--cookies", default=None, help="ملف كوكيز Netscape")
    ap.add_argument("--no-browser", action="store_true", help="تعطيل المتصفح الخفي")
    ap.add_argument("--list", action="store_true", help="عرض المرشحين فقط")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    ex = UniversalExtractor(cookies_file=args.cookies,
                            use_browser=not args.no_browser,
                            progress_hook=hook)

    try:
        if args.list:
            d = ex.discover(args.url)
            print(f"\n🎬 المصدر: {d['source']}  |  العنوان: {d.get('title', '')[:80]}")
            print(f"   المرشحون: {len(d['candidates'])}\n")
            for i, c in enumerate(d["candidates"], 1):
                sz = f"{c.size / 1024 / 1024:.1f}MB" if c.size else "?"
                h = f"{c.height}p" if c.height else "?"
                print(f"  {i:2}. [{c.kind:6}] [{c.source:11}] {h:5} {sz:9} {c.url[:100]}")
            return

        print(f"🎯 الرابط: {args.url}")
        print(f"🔍 جودة: {args.quality} | المتصفح الخفي: {'نعم' if not args.no_browser else 'لا'}\n")

        t0 = __import__("time").time()
        res = ex.download(args.url, outdir, preferred=args.quality)
        dt = __import__("time").time() - t0
        p = Path(res["path"])
        print(f"\n\n✅ تم التحميل بنجاح خلال {dt:.1f} ثانية")
        print(f"   📁 الملف : {p}")
        print(f"   📦 الحجم : {p.stat().st_size / 1024 / 1024:.1f} MB")
        if res.get("title"):
            print(f"   🎬 العنوان: {res['title'][:100]}")
    except DownloadError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️ أُلغيت العملية")
        sys.exit(130)
    finally:
        ex.cleanup()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import settings
from .inspect_runtime import inspect_with_timeout
from .main import _require_admin, app
from .youtube_admin import (
    install_youtube_cookies,
    remove_uploaded_youtube_cookies,
    youtube_cookie_status,
)


_ADMIN_PAGE = r'''<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>إعداد YouTube</title>
<style>
body{font-family:system-ui,sans-serif;background:#111827;color:#f9fafb;margin:0;padding:24px}
main{max-width:720px;margin:auto;background:#1f2937;border:1px solid #374151;border-radius:18px;padding:24px}
h1{margin-top:0}label{display:block;margin:16px 0 7px;font-weight:700}
input,button{box-sizing:border-box;width:100%;padding:13px;border-radius:10px;border:1px solid #4b5563;background:#111827;color:#fff}
button{background:#2563eb;border:0;font-weight:700;cursor:pointer;margin-top:12px}button.danger{background:#991b1b}
#result{white-space:pre-wrap;background:#0b1020;padding:14px;border-radius:10px;min-height:48px;margin-top:16px;overflow-wrap:anywhere}
small{color:#cbd5e1;line-height:1.7;display:block}.ok{color:#86efac}.bad{color:#fca5a5}
</style></head>
<body><main>
<h1>إعداد تنزيل YouTube</h1>
<small>ارفع ملف <b>cookies.txt</b> بصيغة Netscape صادرًا من جلسة YouTube. الملف لا يُعرض ولا يُسجّل، ويُحفظ بصلاحية 0600.</small>
<label>رمز الإدارة</label><input id="token" type="password" autocomplete="current-password" placeholder="ADMIN_API_TOKEN">
<label>ملف cookies.txt</label><input id="file" type="file" accept=".txt,text/plain">
<button id="upload">رفع وتفعيل الملف</button>
<label>رابط اختبار YouTube</label><input id="url" value="https://youtu.be/kTRs4ycFp_o">
<button id="test">اختبار الاتصال</button>
<button id="status">قراءة الحالة</button>
<button id="remove" class="danger">حذف الملف المرفوع</button>
<div id="result">جاهز.</div>
<script>
const result=document.getElementById('result'), token=document.getElementById('token');
token.value=sessionStorage.getItem('yj-admin-token')||'';
const headers=()=>({'X-Admin-Token':token.value});
function show(v,ok=true){result.className=ok?'ok':'bad';result.textContent=typeof v==='string'?v:JSON.stringify(v,null,2);sessionStorage.setItem('yj-admin-token',token.value)}
async function read(r){let t=await r.text();try{return JSON.parse(t)}catch{return {status:r.status,detail:t}}}
document.getElementById('upload').onclick=async()=>{try{const f=document.getElementById('file').files[0];if(!f)throw Error('اختر ملف cookies.txt');const r=await fetch('/api/admin/youtube/cookies',{method:'POST',headers:{...headers(),'Content-Type':'text/plain'},body:await f.arrayBuffer()});show(await read(r),r.ok)}catch(e){show(e.message,false)}};
document.getElementById('status').onclick=async()=>{const r=await fetch('/api/admin/youtube/status',{headers:headers()});show(await read(r),r.ok)};
document.getElementById('remove').onclick=async()=>{const r=await fetch('/api/admin/youtube/cookies',{method:'DELETE',headers:headers()});show(await read(r),r.ok)};
document.getElementById('test').onclick=async()=>{const r=await fetch('/api/admin/youtube/test',{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({url:document.getElementById('url').value})});show(await read(r),r.ok)};
</script></main></body></html>'''


def _install_routes() -> None:
    paths = {getattr(route, "path", "") for route in app.router.routes}
    if "/api/admin/youtube/cookies" in paths:
        return

    @app.get("/admin/youtube", response_class=HTMLResponse)
    def youtube_admin_page() -> str:
        return _ADMIN_PAGE

    @app.get("/api/admin/youtube/status")
    def youtube_admin_status(x_admin_token: str | None = Header(default=None)) -> dict:
        _require_admin(x_admin_token)
        return {
            **youtube_cookie_status(),
            "proxy_configured": bool(settings.youtube_proxy_url.strip()),
        }

    @app.post("/api/admin/youtube/cookies")
    async def youtube_admin_upload(
        request: Request,
        x_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_admin_token)
        content_length = request.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > 1_000_000:
            raise HTTPException(413, "ملف Cookies أكبر من الحد المسموح")
        payload = await request.body()
        try:
            return install_youtube_cookies(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/admin/youtube/cookies")
    def youtube_admin_delete(x_admin_token: str | None = Header(default=None)) -> dict:
        _require_admin(x_admin_token)
        return remove_uploaded_youtube_cookies()

    @app.post("/api/admin/youtube/test")
    async def youtube_admin_test(
        request: Request,
        x_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_admin_token)
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "طلب اختبار غير صالح") from exc
        url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
        if not url or not ("youtube.com" in url or "youtu.be" in url):
            raise HTTPException(400, "أدخل رابط YouTube صالحًا")
        try:
            result = inspect_with_timeout(url, timeout_seconds=40)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(424, f"فشل اتصال YouTube: {str(exc)[:300]}") from exc
        return {
            "ok": True,
            "title": str(result.get("title") or "")[:200],
            "duration": result.get("duration"),
            "formats": len(result.get("formats") or []),
        }


_install_routes()

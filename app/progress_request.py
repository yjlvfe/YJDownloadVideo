"""
app/progress_request.py — تتبع تقدم رفع الملفات + إعادة محاولة تلقائية (PTB v21).

python-telegram-bot v21 حذف معامل progress من دوال الإرسال نهائيًا.
الحل: كلاس HTTPXRequest مخصص يلف كائن الملف بقارئ يحسب البايتات المرسلة
ويستدعي progress_cb(current, total) عند كل 10%، مع 3 محاولات عند أخطاء
الشبكة/المهلة (إرجاع الملفات لبدايتها بين المحاولات وعميل جديد لكل محاولة).

ملاحظة: إعادة المحاولة بعد مهلة قد تسبب إرسالًا مزدوجًا نادرًا (تيليجرام
استلم الطلب لكن الرد تأخر) — مقايضة مقبولة مقابل الاستقرار.
"""

from __future__ import annotations

import asyncio
import io

import httpx

from telegram._utils.defaultvalue import DefaultValue
from telegram.error import NetworkError, TimedOut
from telegram.request import BaseRequest, HTTPXRequest


class _ProgressReader(io.RawIOBase):
    """يلف ملف الرفع: يحسب البايتات المقروءة ويستدعي callback عند كل 10%."""

    def __init__(self, fileobj, total: int, cb):
        super().__init__()
        self._f = fileobj
        self._total = total or 0
        self._done = 0
        self._cb = cb
        self._last_step = 0

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        self._done += len(data)
        if self._cb and self._total:
            step = self._done * 100 // self._total
            if (step >= self._last_step + 10 or self._done >= self._total) and step > self._last_step:
                self._last_step = step
                self._cb(self._done, self._total)
        return data

    def readable(self) -> bool:
        return True

    # تفويض العمليات التي يحتاجها httpx لتحديد الطول
    def seek(self, *args):
        return self._f.seek(*args)

    def tell(self) -> int:
        return self._f.tell()

    def fileno(self) -> int:
        return self._f.fileno()


def _wrap_files(request_data, progress_cb) -> dict | None:
    """يلف ملفات multipart بقارئ التقدم ويعيد files جاهزًا للطلب."""
    if request_data is None:
        return None
    files = request_data.multipart_data
    if not files:
        return files
    wrapped = {}
    for field, (fname, fileobj, ctype) in files.items():
        if hasattr(fileobj, "read"):
            try:
                fileobj.seek(0, io.SEEK_END)
                total = fileobj.tell()
                fileobj.seek(0)
            except Exception:  # noqa: BLE001
                total = 0
            wrapped[field] = (fname, _ProgressReader(fileobj, total, progress_cb), ctype)
        else:
            wrapped[field] = (fname, fileobj, ctype)
    return wrapped


def _rewind_files(request_data) -> None:
    """يعيد الملفات لبدايتها قبل كل محاولة إعادة إرسال."""
    if request_data is None:
        return
    try:
        files = request_data.multipart_data
    except Exception:  # noqa: BLE001
        return
    if not files:
        return
    for value in files.values():
        try:
            fileobj = value[1]
        except Exception:  # noqa: BLE001
            continue
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0, io.SEEK_SET)
            except Exception:  # noqa: BLE001
                pass


class ProgressHTTPXRequest(HTTPXRequest):
    """HTTPXRequest بتقدم رفع حي + 3 محاولات عند أخطاء الشبكة."""

    def __init__(self, progress_cb=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_cb = progress_cb

    async def do_request(
        self,
        url: str,
        method: str,
        request_data=None,
        read_timeout=BaseRequest.DEFAULT_NONE,
        write_timeout=BaseRequest.DEFAULT_NONE,
        connect_timeout=BaseRequest.DEFAULT_NONE,
        pool_timeout=BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        last_error: Exception | None = None
        for attempt in range(3):
            _rewind_files(request_data)
            files = _wrap_files(request_data, self.progress_cb)
            data = request_data.json_parameters if request_data else None

            if isinstance(read_timeout, DefaultValue):
                read_timeout = self._client.timeout.read
            if isinstance(connect_timeout, DefaultValue):
                connect_timeout = self._client.timeout.connect
            if isinstance(pool_timeout, DefaultValue):
                pool_timeout = self._client.timeout.pool
            if isinstance(write_timeout, DefaultValue):
                write_timeout = self._client.timeout.write if not files else self._media_write_timeout

            timeout = httpx.Timeout(
                connect=connect_timeout, read=read_timeout, write=write_timeout, pool=pool_timeout,
            )
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    res = await client.request(
                        method=method, url=url,
                        headers={"User-Agent": self.USER_AGENT},
                        timeout=timeout, files=files, data=data,
                    )
                return res.status_code, res.content
            except httpx.TimeoutException as err:
                if isinstance(err, httpx.PoolTimeout):
                    last_error = TimedOut(
                        message="Pool timeout: All connections in the connection pool are occupied. "
                                "Request was *not* sent to Telegram."
                    )
                else:
                    last_error = TimedOut()
                    last_error.__cause__ = err
            except httpx.HTTPError as err:
                last_error = NetworkError(f"httpx.{err.__class__.__name__}: {err}")
                last_error.__cause__ = err

            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))

        if last_error:
            raise last_error
        raise NetworkError("request failed")

"""
app/progress_request.py — تتبع تقدم رفع الملفات في PTB v21.

python-telegram-bot v21 حذف معامل progress من دوال الإرسال نهائيًا.
الحل: كلاس HTTPXRequest مخصص يلف كائن الملف بقارئ يحسب البايتات المرسلة
ويستدعي progress_cb(current, total) عند كل 10%.
"""

from __future__ import annotations

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


class ProgressHTTPXRequest(HTTPXRequest):
    """HTTPXRequest يستدعي progress_cb(current, total) أثناء رفع أي ملف."""

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
        if self._client.is_closed:
            raise RuntimeError("This HTTPXRequest is not initialized!")

        files = request_data.multipart_data if request_data else None
        data = request_data.json_parameters if request_data else None

        # لف الملفات بقارئ التقدم
        if files and self.progress_cb:
            wrapped = {}
            for field, (fname, fileobj, ctype) in files.items():
                if hasattr(fileobj, "read"):
                    try:
                        fileobj.seek(0, io.SEEK_END)
                        total = fileobj.tell()
                        fileobj.seek(0)
                    except Exception:  # noqa: BLE001
                        total = 0
                    wrapped[field] = (fname, _ProgressReader(fileobj, total, self.progress_cb), ctype)
                else:
                    wrapped[field] = (fname, fileobj, ctype)
            files = wrapped

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
            res = await self._client.request(
                method=method, url=url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=timeout, files=files, data=data,
            )
        except httpx.TimeoutException as err:
            if isinstance(err, httpx.PoolTimeout):
                raise TimedOut(message="Pool timeout: All connections in the connection pool are occupied. Request was *not* sent to Telegram.") from err
            raise TimedOut from err
        except httpx.HTTPError as err:
            raise NetworkError(f"httpx.{err.__class__.__name__}: {err}") from err

        return res.status_code, res.content

from __future__ import annotations

import asyncio
import io

from telegram.error import NetworkError, TimedOut
from telegram.request import BaseRequest, HTTPXRequest


class ProgressHTTPXRequest(HTTPXRequest):
    def __init__(self, progress_cb=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_cb = progress_cb

    @staticmethod
    def _rewind_files(request_data) -> None:
        if not request_data:
            return
        try:
            files = request_data.multipart_data
        except Exception:
            return
        if not files:
            return
        for value in files.values():
            try:
                fileobj = value[1]
            except Exception:
                continue
            if hasattr(fileobj, "seek"):
                try:
                    fileobj.seek(0, io.SEEK_SET)
                except Exception:
                    pass

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
            self._rewind_files(request_data)
            client = HTTPXRequest(
                connection_pool_size=1,
                read_timeout=600,
                write_timeout=600,
                connect_timeout=30,
                pool_timeout=30,
                media_write_timeout=600,
            )
            await client.initialize()
            try:
                return await client.do_request(
                    url=url,
                    method=method,
                    request_data=request_data,
                    read_timeout=read_timeout,
                    write_timeout=write_timeout,
                    connect_timeout=connect_timeout,
                    pool_timeout=pool_timeout,
                )
            except (NetworkError, TimedOut) as error:
                last_error = error
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
            finally:
                try:
                    await client.shutdown()
                except Exception:
                    pass
        if last_error:
            raise last_error
        raise NetworkError("request failed")

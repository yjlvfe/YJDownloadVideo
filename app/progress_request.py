"""Stable Telegram HTTP transport with transient network retries.

The previous implementation wrapped uploaded file handles to report upload progress.
That caused concurrent HTTP operations on the same request client while Vercel was
streaming the media upload, which intermittently failed with errno 16
(`Device or resource busy`).  Reliability is more important than upload percentage,
so Vercel now uses python-telegram-bot's native multipart implementation.
"""

from __future__ import annotations

import asyncio
import io

from telegram.error import NetworkError, TimedOut
from telegram.request import BaseRequest, HTTPXRequest


class ProgressHTTPXRequest(HTTPXRequest):
    """HTTPXRequest compatible with the old progress-aware constructor.

    ``progress_cb`` is intentionally retained for API compatibility but native PTB
    multipart uploads are used. Transient transport failures are retried after
    rewinding file objects so a failed upload does not leave the bot silent.
    """

    def __init__(self, progress_cb=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_cb = progress_cb

    @staticmethod
    def _rewind_files(request_data) -> None:
        if not request_data:
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
            try:
                return await super().do_request(
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
                await asyncio.sleep(0.75 * (attempt + 1))

        if last_error is not None:
            raise last_error
        raise NetworkError("Telegram request failed without an explicit error")

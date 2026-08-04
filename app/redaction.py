from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _sanitize_url(raw: str) -> str:
    trailing = ""
    while raw and raw[-1] in ").,;]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "[REDACTED_URL]" + trailing

    hostname = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    query = "[REDACTED_QUERY]" if parsed.query else ""
    fragment = "[REDACTED_FRAGMENT]" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment)) + trailing


def sanitize_external_error(
    value: object,
    *,
    secrets_to_hide: Iterable[str] = (),
    limit: int | None = None,
) -> str:
    text = str(value or "")
    text = _URL_RE.sub(lambda match: _sanitize_url(match.group(0)), text)
    for secret in secrets_to_hide:
        normalized = str(secret or "")
        if normalized:
            text = text.replace(normalized, "[REDACTED_SECRET]")
    text = text.replace("\x1b", "")
    if limit is not None:
        text = text[: max(0, int(limit))]
    return text


class SafeYtdlpLogger:
    def __init__(
        self,
        *,
        sink: Callable[[str, str], None] | None = None,
        secrets_to_hide: Iterable[str] = (),
    ) -> None:
        self.secrets_to_hide = tuple(str(value) for value in secrets_to_hide if value)
        if sink is None:
            logger = logging.getLogger("yt-dlp")

            def sink(level: str, message: str) -> None:
                getattr(logger, level if level in {"debug", "warning", "error"} else "info")(
                    "%s",
                    message,
                )

        self.sink = sink

    def _emit(self, level: str, message: object) -> None:
        self.sink(
            level,
            sanitize_external_error(
                message,
                secrets_to_hide=self.secrets_to_hide,
                limit=500,
            ),
        )

    def debug(self, message: object) -> None:
        self._emit("debug", message)

    def warning(self, message: object) -> None:
        self._emit("warning", message)

    def error(self, message: object) -> None:
        self._emit("error", message)

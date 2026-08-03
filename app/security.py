from __future__ import annotations

import ipaddress
import secrets
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx
from itsdangerous import URLSafeTimedSerializer

from .config import settings


serializer = URLSafeTimedSerializer(settings.secret_key, salt="yjdownload-file")
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_public_url(raw: str) -> str:
    normalized = str(raw or "").strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("الرابط غير صالح")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "0.0.0.0", "::", "::1"} or host.endswith((".local", ".localhost")):
        raise ValueError("لا يسمح بروابط الشبكات الداخلية")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _is_forbidden_address(literal):
        raise ValueError("لا يسمح بعنوان داخلي")

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("تعذر التحقق من عنوان الموقع") from exc
    if not infos:
        raise ValueError("تعذر التحقق من عنوان الموقع")

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in infos:
        try:
            addresses.add(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError) as exc:
            raise ValueError("تعذر التحقق من عنوان الموقع") from exc
    if not addresses or any(_is_forbidden_address(address) for address in addresses):
        raise ValueError("لا يسمح بعنوان داخلي")
    return normalized


def validate_redirect_chain(
    raw: str,
    *,
    max_redirects: int = 5,
    requester: Callable[[str], Any] | None = None,
) -> str:
    current = validate_public_url(raw)
    request = requester
    if request is None:
        def request(url: str) -> httpx.Response:
            return httpx.head(
                url,
                follow_redirects=False,
                timeout=10,
                headers={"user-agent": "YJDownloadVideo/1.0"},
            )

    for _ in range(max(0, int(max_redirects)) + 1):
        response = request(current)
        status = int(response.status_code)
        location = str(response.headers.get("location") or "").strip()
        if status not in _REDIRECT_CODES or not location:
            return current
        current = validate_public_url(urljoin(current, location))
    raise ValueError("عدد التحويلات في الرابط تجاوز الحد المسموح")


def make_token(job_id: str) -> str:
    return serializer.dumps({"job_id": job_id, "nonce": secrets.token_urlsafe(12)})


def read_token(token: str) -> str:
    data = serializer.loads(token, max_age=max(1, settings.file_ttl_seconds))
    return str(data["job_id"])

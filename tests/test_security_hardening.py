import socket
from types import SimpleNamespace

import pytest

from app.security import validate_public_url, validate_redirect_chain


def test_dns_failure_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(ValueError, match="تعذر التحقق"):
        validate_public_url("https://missing.example/video")


def test_private_and_mixed_dns_answers_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="عنوان داخلي"):
        validate_public_url("https://example.com/video")


def test_url_credentials_and_non_http_schemes_are_rejected() -> None:
    with pytest.raises(ValueError):
        validate_public_url("https://user:pass@example.com/video")
    with pytest.raises(ValueError):
        validate_public_url("file:///etc/passwd")


def test_redirect_chain_revalidates_every_location(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolver(host: str, *args, **kwargs):
        address = "93.184.216.34" if host == "public.example" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    def requester(url: str):
        return SimpleNamespace(
            status_code=302,
            headers={"location": "http://internal.example/private"},
        )

    with pytest.raises(ValueError, match="عنوان داخلي"):
        validate_redirect_chain("https://public.example/start", requester=requester)


def test_redirect_chain_returns_final_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    responses = iter([
        SimpleNamespace(status_code=302, headers={"location": "/video"}),
        SimpleNamespace(status_code=200, headers={}),
    ])
    assert validate_redirect_chain(
        "https://public.example/start",
        requester=lambda url: next(responses),
    ) == "https://public.example/video"

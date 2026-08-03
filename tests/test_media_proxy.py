import pytest

from app.media_proxy import make_proxy_token, proxy_headers, read_proxy_token


def test_proxy_token_round_trip_and_expiry():
    token = make_proxy_token(
        media_url="https://cdn.example/video.mp4?x=1",
        source_url="https://www.tiktok.com/@u/video/1",
        headers={"User-Agent": "Browser", "Referer": "https://www.tiktok.com/"},
        secret="secret",
        now=1000,
        ttl_seconds=60,
    )

    payload = read_proxy_token(token, secret="secret", now=1059)
    assert payload["media_url"].startswith("https://cdn.example/")
    assert payload["headers"]["Referer"] == "https://www.tiktok.com/"

    with pytest.raises(ValueError, match="expired"):
        read_proxy_token(token, secret="secret", now=1061)


def test_proxy_token_rejects_tampering():
    token = make_proxy_token(
        media_url="https://cdn.example/video.mp4",
        source_url="https://example.com/post",
        headers={},
        secret="secret",
        now=1000,
    )
    broken = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(ValueError, match="signature"):
        read_proxy_token(broken, secret="secret", now=1001)


def test_proxy_headers_only_forwards_safe_headers_and_range():
    headers = proxy_headers(
        {
            "Cookie": "secret",
            "User-Agent": "Browser",
            "Referer": "https://example.com/",
            "X-Test": "bad",
        },
        range_header="bytes=0-99",
    )

    assert headers == {
        "User-Agent": "Browser",
        "Referer": "https://example.com/",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Range": "bytes=0-99",
    }

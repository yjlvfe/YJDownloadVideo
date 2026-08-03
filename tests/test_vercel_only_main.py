from pathlib import Path


def test_vercel_telegram_routes_do_not_use_a_remote_worker():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert "remote_worker" not in source
    assert "vercel_remote" not in source
    assert "handle_update(data)" in source


def test_health_exposes_zero_retained_media_fields():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert '"retained_media_count"' in source
    assert '"retained_media_bytes"' in source

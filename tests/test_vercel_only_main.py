from pathlib import Path


def test_vercel_telegram_routes_do_not_use_a_remote_worker():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert "remote_worker" not in source
    assert "vercel_remote" not in source
    assert "handle_update(data)" in source


def test_health_exposes_runtime_and_zero_retention_fields():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert '"retainedMediaCount"' in source
    assert '"retainedMediaBytes"' in source
    assert '"ffmpegReady"' in source
    assert '"javascriptRuntimeReady"' in source

from pathlib import Path


def test_vercel_telegram_routes_forward_to_the_render_worker():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert "settings.vercel_mode" in source
    assert "remote_worker" in source
    assert "forward_telegram_update" in source
    assert '"delivery": "render-2gb"' in source
    assert '@app.post("/telegram/update"' in source
    assert '@app.post("/telegram/call/{method}"' in source


def test_health_exposes_runtime_and_zero_retention_fields():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert '"retainedMediaCount"' in source
    assert '"retainedMediaBytes"' in source
    assert '"ffmpegReady"' in source
    assert '"javascriptRuntimeReady"' in source

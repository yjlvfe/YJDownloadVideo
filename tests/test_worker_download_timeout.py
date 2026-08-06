from app import worker


def test_worker_uses_configured_long_download_timeout(monkeypatch):
    monkeypatch.setattr(worker.settings, "download_timeout_seconds", 21600, raising=False)

    assert worker._download_timeout_seconds() == 21600


def test_worker_keeps_a_five_minute_minimum_for_misconfiguration(monkeypatch):
    monkeypatch.setattr(worker.settings, "download_timeout_seconds", 30, raising=False)

    assert worker._download_timeout_seconds() == 300

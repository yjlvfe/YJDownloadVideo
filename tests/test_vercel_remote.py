import inspect

from app import vercel_remote


def test_vercel_remote_forwards_without_downloading_media():
    source = inspect.getsource(vercel_remote)

    assert "download_and_upload" not in source
    assert "yt_dlp" not in source
    assert "forward_telegram_update" in source


def test_vercel_remote_exposes_only_forwarding_handler():
    assert hasattr(vercel_remote, "handle_vercel_remote_update")
    assert not hasattr(vercel_remote, "select_remote_url")

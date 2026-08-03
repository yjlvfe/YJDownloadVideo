from app.vercel_remote import select_remote_url


def test_select_remote_url_prefers_combined_mp4():
    info = {
        "formats": [
            {"url": "https://cdn.test/video-only.mp4", "ext": "mp4", "vcodec": "h264", "acodec": "none", "height": 1080},
            {"url": "https://cdn.test/combined.webm", "ext": "webm", "vcodec": "vp9", "acodec": "opus", "height": 1080},
            {"url": "https://cdn.test/combined.mp4", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 720},
        ]
    }

    assert select_remote_url(info) == "https://cdn.test/combined.mp4"


def test_select_remote_url_rejects_video_only_formats():
    info = {
        "formats": [
            {"url": "https://cdn.test/video-only.mp4", "ext": "mp4", "vcodec": "h264", "acodec": "none", "height": 1080},
        ]
    }

    assert select_remote_url(info) is None

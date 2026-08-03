from pathlib import Path

import pytest

from app.vercel_upload import parse_bot_api_response, select_downloaded_file


def test_select_downloaded_file_ignores_partial_files(tmp_path: Path):
    (tmp_path / "video.mp4.part").write_bytes(b"partial")
    expected = tmp_path / "video.mp4"
    expected.write_bytes(b"complete")

    assert select_downloaded_file(tmp_path, max_bytes=1024) == expected


def test_select_downloaded_file_rejects_oversize_media(tmp_path: Path):
    (tmp_path / "video.mp4").write_bytes(b"x" * 11)

    with pytest.raises(RuntimeError, match="يتجاوز حد تيليجرام"):
        select_downloaded_file(tmp_path, max_bytes=10)


def test_parse_bot_api_response_returns_result():
    assert parse_bot_api_response({"ok": True, "result": {"message_id": 7}}) == {"message_id": 7}


def test_parse_bot_api_response_raises_safe_description():
    with pytest.raises(RuntimeError, match="Bad Request"):
        parse_bot_api_response({"ok": False, "description": "Bad Request: upload failed"})

from types import SimpleNamespace

from app.media_diagnostics import classify_media_error, recent_media_failures


def test_media_error_classification_is_specific() -> None:
    assert classify_media_error("Requested format is not available") == "format_unavailable"
    assert classify_media_error("youtube_bot_check") == "youtube_bot_check"
    assert classify_media_error("HTTP Error 429") == "rate_limited"
    assert classify_media_error("database is locked") == "database_busy"
    assert classify_media_error("المقطع المتاح بدون صوت") == "missing_audio"
    assert classify_media_error("something unexpected") == "unclassified"


def test_recent_failures_do_not_expose_urls_ids_titles_or_owners() -> None:
    jobs = [SimpleNamespace(id="secret-job-id", url="https://example.com/video?token=secret", title="private title", owner_key="203.0.113.1", status="failed", requested_format="720", error="ERROR https://cdn.example.com/file?sig=secret Requested format is not available")]
    result = recent_media_failures(jobs)
    assert result == [{"status": "failed", "format": "720", "code": "format_unavailable", "message": "ERROR https://cdn.example.com/file?[REDACTED_QUERY] Requested format is not available"}]
    rendered = repr(result)
    assert "secret-job-id" not in rendered
    assert "private title" not in rendered
    assert "203.0.113.1" not in rendered
    assert "sig=secret" not in rendered

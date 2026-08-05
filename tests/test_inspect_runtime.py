from __future__ import annotations

import time

import pytest

from app import inspect_runtime


def test_bounded_inspect_returns_child_payload(monkeypatch):
    monkeypatch.setattr(
        inspect_runtime,
        "_inspect_in_child",
        lambda url, queue: queue.put({"ok": True, "result": {"title": "sample", "formats": []}}),
    )

    result = inspect_runtime.inspect_with_timeout("https://example.com/video", timeout_seconds=2)

    assert result["title"] == "sample"


def test_bounded_inspect_kills_hung_child(monkeypatch):
    def hang(url, queue):
        time.sleep(30)

    monkeypatch.setattr(inspect_runtime, "_inspect_in_child", hang)
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="مهلة فحص الرابط"):
        inspect_runtime.inspect_with_timeout("https://example.com/video", timeout_seconds=1)

    assert time.monotonic() - started < 5

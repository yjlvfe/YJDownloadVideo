from __future__ import annotations

import pytest

from app import worker


class FakeCircuit:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed
        self.failures: list[str] = []
        self.successes = 0

    def allow_request(self) -> bool:
        return self.allowed

    def record_failure(self, classification: str) -> None:
        self.failures.append(classification)

    def record_success(self) -> None:
        self.successes += 1


def test_parent_blocks_youtube_job_while_circuit_is_open(monkeypatch):
    circuit = FakeCircuit(allowed=False)
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)

    with pytest.raises(RuntimeError, match="youtube_circuit_open"):
        worker._ensure_parent_youtube_allowed(
            "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        )


def test_parent_does_not_apply_youtube_circuit_to_other_sites(monkeypatch):
    circuit = FakeCircuit(allowed=False)
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)

    worker._ensure_parent_youtube_allowed("https://www.instagram.com/reel/example/")


def test_parent_records_bot_check_and_success(monkeypatch):
    circuit = FakeCircuit()
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)
    url = "https://youtu.be/jNQXAC9IVRw"

    worker._record_parent_youtube_failure(url, "youtube_bot_check")
    worker._record_parent_youtube_success(url)

    assert circuit.failures == ["youtube_bot_check"]
    assert circuit.successes == 1


def test_parent_normalizes_raw_youtube_errors(monkeypatch):
    circuit = FakeCircuit()
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)

    worker._record_parent_youtube_failure(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "Sign in to confirm you’re not a bot",
    )

    assert circuit.failures == ["youtube_bot_check"]

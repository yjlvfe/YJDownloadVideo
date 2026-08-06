from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import worker


class FakeCircuit:
    def __init__(self, *, state: str = "closed", opened_until: float = 0.0):
        self.state = state
        self.opened_until = opened_until
        self.failures: list[str] = []
        self.successes = 0

    def snapshot(self):
        return SimpleNamespace(state=self.state, opened_until=self.opened_until)

    def record_failure(self, classification: str) -> None:
        self.failures.append(classification)

    def record_success(self) -> None:
        self.successes += 1


def test_parent_blocks_youtube_job_while_cooldown_is_active(monkeypatch):
    circuit = FakeCircuit(state="open", opened_until=2000.0)
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)
    monkeypatch.setattr(worker.time, "time", lambda: 1000.0)

    with pytest.raises(RuntimeError, match="youtube_circuit_open"):
        worker._ensure_parent_youtube_allowed(
            "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        )


def test_parent_allows_recovery_attempt_after_cooldown(monkeypatch):
    circuit = FakeCircuit(state="open", opened_until=900.0)
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)
    monkeypatch.setattr(worker.time, "time", lambda: 1000.0)

    worker._ensure_parent_youtube_allowed(
        "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    )


def test_parent_does_not_apply_youtube_circuit_to_other_sites(monkeypatch):
    circuit = FakeCircuit(state="open", opened_until=2000.0)
    monkeypatch.setattr(worker, "get_youtube_circuit", lambda: circuit)
    monkeypatch.setattr(worker.time, "time", lambda: 1000.0)

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

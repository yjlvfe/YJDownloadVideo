from pathlib import Path

from app.durable_runtime import (
    active_media_jobs_for_owner,
    is_retryable_job_error,
)
from app.job_queue import JobQueue


def test_only_transient_failures_are_retried() -> None:
    assert is_retryable_job_error("connection reset by peer")
    assert is_retryable_job_error("TimeoutError")

    assert not is_retryable_job_error("youtube_bot_check")
    assert not is_retryable_job_error("youtube_circuit_open")
    assert not is_retryable_job_error("المقطع محمي بتشفير DRM")
    assert not is_retryable_job_error("حجم الملف يتجاوز الحد المسموح")
    assert not is_retryable_job_error("الجودة المطلوبة غير متاحة")


def test_active_media_jobs_are_counted_per_owner(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: 1000.0)
    queue.enqueue(
        idempotency_key="a",
        kind="media_download",
        payload={"owner_id": 7, "url": "https://example.com/a"},
    )
    queue.enqueue(
        idempotency_key="b",
        kind="media_download",
        payload={"owner_id": 7, "url": "https://example.com/b"},
    )
    other, _ = queue.enqueue(
        idempotency_key="c",
        kind="media_download",
        payload={"owner_id": 8, "url": "https://example.com/c"},
    )

    assert active_media_jobs_for_owner(queue, 7) == 2
    assert active_media_jobs_for_owner(queue, 8) == 1

    claimed = queue.claim("worker", lease_seconds=30)
    assert claimed is not None
    queue.complete(other.id, worker_id=None)
    assert active_media_jobs_for_owner(queue, 8) == 0

from pathlib import Path

from app.job_queue import DurableSelectionStore, JobQueue


def test_duplicate_telegram_update_is_accepted_once(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: 1000.0)

    first, first_created = queue.accept_update(123, {"update_id": 123, "message": {"text": "x"}})
    second, second_created = queue.accept_update(123, {"update_id": 123, "message": {"text": "x"}})

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert queue.queue_depth() == 1


def test_queue_survives_process_recreation(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    JobQueue(path, clock=lambda: 1000.0).accept_update(77, {"update_id": 77})

    recreated = JobQueue(path, clock=lambda: 1001.0)
    job = recreated.claim("worker-a", lease_seconds=30)

    assert job is not None
    assert job.update_id == 77
    assert job.state == "claimed"
    assert job.attempt == 1


def test_expired_claim_is_recovered_by_another_worker(tmp_path: Path) -> None:
    now = [1000.0]
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: now[0])
    queue.accept_update(88, {"update_id": 88})

    first = queue.claim("worker-a", lease_seconds=10)
    assert first is not None
    assert queue.claim("worker-b", lease_seconds=10) is None

    now[0] = 1011.0
    recovered = queue.claim("worker-b", lease_seconds=10)

    assert recovered is not None
    assert recovered.id == first.id
    assert recovered.lease_owner == "worker-b"
    assert recovered.attempt == 2


def test_terminal_state_is_immutable(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: 1000.0)
    job, _ = queue.accept_update(99, {"update_id": 99})
    claimed = queue.claim("worker-a", lease_seconds=30)
    assert claimed is not None

    assert queue.complete(job.id, worker_id="worker-a") is True
    assert queue.fail(job.id, "late_failure", worker_id="worker-a") is False
    assert queue.get(job.id).state == "completed"


def test_retry_wait_is_not_claimed_before_due_time(tmp_path: Path) -> None:
    now = [1000.0]
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: now[0])
    job, _ = queue.accept_update(100, {"update_id": 100})
    queue.claim("worker-a", lease_seconds=30)
    assert queue.fail(job.id, "network", retry_at=1020.0, worker_id="worker-a") is True

    assert queue.claim("worker-b", lease_seconds=30) is None
    now[0] = 1021.0
    assert queue.claim("worker-b", lease_seconds=30) is not None


def test_selection_store_survives_restart_and_enforces_owner(tmp_path: Path) -> None:
    now = [1000.0]
    path = tmp_path / "queue.db"
    store = DurableSelectionStore(path, ttl_seconds=60, clock=lambda: now[0])
    token = store.create(7, {"url": "https://example.com/video"})

    recreated = DurableSelectionStore(path, ttl_seconds=60, clock=lambda: now[0])
    assert recreated.get(token, 8) is None
    assert recreated.get(token, 7) == {"url": "https://example.com/video"}

    now[0] = 1061.0
    assert recreated.get(token, 7) is None


def test_nonce_can_only_be_consumed_once(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.db", clock=lambda: 1000.0)
    assert queue.consume_nonce("nonce-1", expires_at=1300.0) is True
    assert queue.consume_nonce("nonce-1", expires_at=1300.0) is False

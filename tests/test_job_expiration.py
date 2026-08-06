from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, Job
from app.job_expiration import expire_job_artifacts, reconcile_expired_jobs


def _session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _ready_job(job_id: str, file_path: Path, expires_at: datetime) -> Job:
    return Job(
        id=job_id,
        url="https://example.com/video",
        title="video",
        requested_format="best",
        status="ready",
        file_path=str(file_path),
        download_token=f"token-{job_id}",
        file_size=file_path.stat().st_size if file_path.exists() else 10,
        owner_key="owner",
        expires_at=expires_at,
    )


def test_expire_job_artifacts_deletes_job_tree_and_clears_database_fields(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    media = storage / "job-1" / "nested" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    sessions = _session_factory(tmp_path)
    with sessions() as database:
        database.add(
            _ready_job(
                "job-1",
                media,
                datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        database.commit()

    result = expire_job_artifacts(
        "job-1",
        storage_root=storage,
        session_factory=sessions,
    )

    assert result.expired_count == 1
    assert result.removed_count == 1
    assert result.removed_bytes == 5
    assert not (storage / "job-1").exists()
    with sessions() as database:
        job = database.get(Job, "job-1")
        assert job is not None
        assert job.status == "expired"
        assert job.file_path is None
        assert job.download_token is None
        assert job.file_size is None


def test_reconcile_removes_elapsed_and_missing_files_but_keeps_valid_ready_job(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    expired_media = storage / "expired" / "video.mp4"
    valid_media = storage / "valid" / "video.mp4"
    expired_media.parent.mkdir(parents=True)
    valid_media.parent.mkdir(parents=True)
    expired_media.write_bytes(b"old")
    valid_media.write_bytes(b"new")
    missing_media = storage / "missing" / "video.mp4"
    sessions = _session_factory(tmp_path)
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)

    with sessions() as database:
        database.add_all(
            [
                _ready_job("expired", expired_media, now - timedelta(seconds=1)),
                _ready_job("missing", missing_media, now + timedelta(hours=1)),
                _ready_job("valid", valid_media, now + timedelta(hours=1)),
            ]
        )
        database.commit()

    result = reconcile_expired_jobs(
        now=now,
        storage_root=storage,
        session_factory=sessions,
    )

    assert result.expired_count == 2
    assert not expired_media.exists()
    assert valid_media.exists()
    with sessions() as database:
        assert database.get(Job, "expired").status == "expired"
        assert database.get(Job, "missing").status == "expired"
        assert database.get(Job, "valid").status == "ready"


def test_expiration_refuses_to_delete_paths_outside_storage(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"keep")
    sessions = _session_factory(tmp_path)
    with sessions() as database:
        database.add(
            _ready_job(
                "outside",
                outside,
                datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        database.commit()

    result = expire_job_artifacts(
        "outside",
        storage_root=storage,
        session_factory=sessions,
    )

    assert outside.exists()
    assert result.removed_count == 0
    with sessions() as database:
        assert database.get(Job, "outside").status == "expired"

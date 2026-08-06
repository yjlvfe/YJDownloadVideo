import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import asgi, extractor as extractor_module, worker
from app.db import Base, Job
from app.security import make_token
from app.storage_leases import StorageLeaseRegistry
from app.storage_maintenance import run_storage_maintenance
from app.telegram_flow import DownloadRequest, TelegramFlow


def _session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(job_id: str, *, status: str = "waiting", file_path: str | None = None) -> Job:
    return Job(
        id=job_id,
        url="https://example.com/video",
        title="video",
        requested_format="best",
        status=status,
        file_path=file_path,
        download_token=make_token(job_id) if file_path else None,
        file_size=5 if file_path else None,
        owner_key="owner",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
            if file_path
            else None
        ),
    )


def test_worker_holds_and_releases_storage_lease_during_extraction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "storage"
    sessions = _session_factory(tmp_path)
    with sessions() as database:
        database.add(_job("job-lease"))
        database.commit()

    observed_active: list[bool] = []

    class FakeExtractor:
        def __init__(self, **kwargs):
            self.progress_hook = kwargs["progress_hook"]

        def download(self, url, outdir, preferred):
            registry = StorageLeaseRegistry(storage)
            observed_active.append(
                (storage / "job-lease").resolve()
                in registry.active_paths()
            )
            self.progress_hook(
                {
                    "status": "downloading",
                    "total_bytes": 5,
                    "downloaded_bytes": 1,
                }
            )
            output = Path(outdir) / "video.mp4"
            output.write_bytes(b"video")
            return {"path": str(output), "title": "video", "duration": 1}

        def cleanup(self):
            return None

    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker.settings, "storage_dir", str(storage))
    monkeypatch.setattr(extractor_module, "UniversalExtractor", FakeExtractor)
    result_queue = Queue(maxsize=1)

    worker._extract_in_child("job-lease", result_queue)

    payload = result_queue.get_nowait()
    assert payload["ok"] is True
    assert observed_active == [True]
    assert StorageLeaseRegistry(storage).active_paths() == set()


@pytest.mark.asyncio
async def test_telegram_download_holds_lease_and_deletes_temporary_media(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    observed_active: list[bool] = []

    class FakeAPI:
        async def edit_message_text(self, chat_id, message_id, text):
            return {"ok": True}

        async def delete_message(self, chat_id, message_id):
            return {"ok": True}

        async def send_video_file(self, chat_id, path, progress=None):
            if progress:
                progress(Path(path).stat().st_size, Path(path).stat().st_size)
            return {"ok": True}

    settings = SimpleNamespace(
        storage_path=storage,
        telegram_send_limit_bytes=2_000_000_000,
    )
    flow = TelegramFlow(api=FakeAPI(), settings=settings)

    def fake_download(request, root, state):
        observed_active.append(
            root.resolve() in StorageLeaseRegistry(storage).active_paths()
        )
        output = root / "video.mp4"
        output.write_bytes(b"video")
        return {"path": str(output), "title": "video"}

    monkeypatch.setattr(flow, "_download_sync", fake_download)
    request = DownloadRequest(
        chat_id=1,
        owner_id=2,
        url="https://example.com/video",
        title="video",
        kind="video",
        selector="best",
        status_message_id=3,
    )

    await flow._process_request(request)

    assert observed_active == [True]
    assert StorageLeaseRegistry(storage).active_paths() == set()
    assert [path for path in storage.iterdir() if path.name != ".leases"] == []


def test_download_response_removes_file_after_response_completion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage = tmp_path / "storage"
    media = storage / "job-download" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    sessions = _session_factory(tmp_path)
    with sessions() as database:
        database.add(_job("job-download", status="ready", file_path=str(media)))
        database.commit()

    monkeypatch.setattr(asgi, "SessionLocal", sessions)
    monkeypatch.setattr(asgi.settings, "storage_dir", str(storage))
    monkeypatch.setattr(asgi.settings, "vercel_mode", False)
    route = next(
        route
        for route in asgi.fastapi_app.router.routes
        if getattr(route, "path", None) == "/download/{token}"
    )

    response = route.endpoint(make_token("job-download"))

    assert media.exists()
    assert response.background is not None
    asyncio.run(response.background())
    assert not (storage / "job-download").exists()
    with sessions() as database:
        job = database.get(Job, "job-download")
        assert job.status == "expired"
        assert job.file_path is None
        assert job.download_token is None


def test_storage_maintenance_cleans_files_and_reconciles_database(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    media = storage / "job-expired" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    sessions = _session_factory(tmp_path)
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    with sessions() as database:
        job = _job("job-expired", status="ready", file_path=str(media))
        job.expires_at = now - timedelta(seconds=1)
        database.add(job)
        database.commit()

    result = run_storage_maintenance(
        storage_root=storage,
        now=now,
        ttl_seconds=3600,
        session_factory=sessions,
    )

    assert result.expiration.expired_count == 1
    assert not media.exists()
    with sessions() as database:
        assert database.get(Job, "job-expired").status == "expired"

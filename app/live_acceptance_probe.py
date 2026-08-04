from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .db import Job, SessionLocal
from .main import CreateRequest, _create_job_local, _inspect_local, app


_PREFIX = "/__acceptance_f0e41c9e6a13"
_OWNER = "acceptance-probe-f0e41c9e6a13"
_SAMPLES = {
    "tiktok": "https://vm.tiktok.com/ZGdxjnte8/",
    "instagram": "https://www.instagram.com/reel/DbkuNu8oMA5/?igsh=MWVuMjJlOWszdHQzMw==",
    "youtube": "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce",
    "short": "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu",
}


def _sample(name: str) -> str:
    try:
        return _SAMPLES[name]
    except KeyError as exc:
        raise HTTPException(404, "Unknown acceptance sample") from exc


def _sample_name(url: str) -> str:
    return next((name for name, value in _SAMPLES.items() if value == url), "unknown")


def _probe_file(path: str) -> dict[str, Any]:
    media_path = Path(path)
    if not media_path.is_file():
        raise RuntimeError("Acceptance file is missing")
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,height,codec_name",
            "-of",
            "json",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {completed.stderr[:240]}")
    streams = json.loads(completed.stdout or "{}").get("streams") or []
    return {
        "bytes": media_path.stat().st_size,
        "has_video": any(item.get("codec_type") == "video" for item in streams),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "height": max(
            [int(item.get("height") or 0) for item in streams if item.get("codec_type") == "video"]
            or [0]
        ),
        "streams": streams,
    }


def _job_dict(job: Job) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": job.id,
        "sample": _sample_name(job.url),
        "status": job.status,
        "format_id": job.requested_format,
        "file_size": job.file_size,
        "duration": job.duration,
        "error": str(job.error or "")[:500] or None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
    if job.status == "ready" and job.file_path and os.path.isfile(job.file_path):
        try:
            result["probe"] = _probe_file(job.file_path)
        except Exception as exc:  # noqa: BLE001
            result["probe_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return result


def _job_snapshot(job_id: str) -> dict[str, Any]:
    with SessionLocal() as database:
        job = database.get(Job, job_id)
        if job is None or job.owner_key != _OWNER:
            raise HTTPException(404, "Acceptance job not found")
        return _job_dict(job)


def _database_jobs() -> list[dict[str, Any]]:
    with SessionLocal() as database:
        jobs = (
            database.query(Job)
            .filter(Job.owner_key == _OWNER)
            .order_by(Job.created_at.desc())
            .limit(100)
            .all()
        )
        return [_job_dict(job) for job in jobs]


def _install() -> None:
    paths = {getattr(route, "path", "") for route in app.router.routes}
    if f"{_PREFIX}/status" in paths:
        return

    @app.get(f"{_PREFIX}/status")
    def acceptance_status() -> dict[str, Any]:
        return {"status": "manual", "auto_start": False}

    @app.get(f"{_PREFIX}/summary")
    def acceptance_summary() -> dict[str, Any]:
        return {"status": "manual", "jobs": _database_jobs()}

    @app.get(f"{_PREFIX}/jobs")
    def acceptance_jobs() -> dict[str, Any]:
        return {"jobs": _database_jobs()}

    @app.get(f"{_PREFIX}/inspect/{{sample}}")
    def inspect_sample(sample: str) -> dict[str, Any]:
        return _inspect_local(_sample(sample))

    @app.get(f"{_PREFIX}/start/{{sample}}/{{quality}}")
    def start_sample(sample: str, quality: str) -> dict[str, Any]:
        return _create_job_local(
            CreateRequest(url=_sample(sample), format_id=quality),
            _OWNER,
        )

    @app.get(f"{_PREFIX}/probe/{{job_id}}")
    def probe_job(job_id: str) -> dict[str, Any]:
        return _job_snapshot(job_id)


_install()

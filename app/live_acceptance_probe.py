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
    "normal": "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce",
    "short": "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu",
}


def _sample(name: str) -> str:
    try:
        return _SAMPLES[name]
    except KeyError as exc:
        raise HTTPException(404, "Unknown acceptance sample") from exc


def _probe_file(path: str) -> dict[str, Any]:
    media_path = Path(path)
    if not media_path.is_file():
        raise HTTPException(404, "Acceptance file is missing")
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
        raise HTTPException(500, "ffprobe failed")
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
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


def _install() -> None:
    paths = {getattr(route, "path", "") for route in app.router.routes}
    if f"{_PREFIX}/inspect/{{sample}}" in paths:
        return

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
        with SessionLocal() as database:
            job = database.get(Job, job_id)
            if job is None or job.owner_key != _OWNER:
                raise HTTPException(404, "Acceptance job not found")
            result: dict[str, Any] = {
                "id": job.id,
                "status": job.status,
                "format_id": job.requested_format,
                "file_size": job.file_size,
                "duration": job.duration,
                "error": str(job.error or "")[:300] or None,
            }
            if job.status == "ready" and job.file_path and os.path.isfile(job.file_path):
                result["probe"] = _probe_file(job.file_path)
            return result


_install()

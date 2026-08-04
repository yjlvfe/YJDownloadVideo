from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
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
_STATE: dict[str, Any] = {
    "status": "pending",
    "started_at": None,
    "finished_at": None,
    "results": [],
    "error": None,
}
_STATE_LOCK = threading.Lock()
_STARTED = False


def _sample(name: str) -> str:
    try:
        return _SAMPLES[name]
    except KeyError as exc:
        raise HTTPException(404, "Unknown acceptance sample") from exc


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
        raise RuntimeError("ffprobe failed")
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


def _job_snapshot(job_id: str) -> dict[str, Any]:
    with SessionLocal() as database:
        job = database.get(Job, job_id)
        if job is None or job.owner_key != _OWNER:
            raise RuntimeError("Acceptance job not found")
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


def _run_quality(sample: str, quality: str) -> dict[str, Any]:
    created = _create_job_local(
        CreateRequest(url=_sample(sample), format_id=quality),
        _OWNER,
    )
    job_id = str(created.get("id") or "")
    deadline = time.time() + 1_000
    while time.time() < deadline:
        snapshot = _job_snapshot(job_id)
        if snapshot["status"] in {"ready", "failed", "dead"}:
            return snapshot
        time.sleep(1)
    return {
        "id": job_id,
        "status": "failed",
        "format_id": quality,
        "error": "acceptance timeout",
    }


def _run_acceptance() -> None:
    with _STATE_LOCK:
        _STATE.update(
            status="running",
            started_at=int(time.time()),
            finished_at=None,
            results=[],
            error=None,
        )
    try:
        results: list[dict[str, Any]] = []
        for sample, url in _SAMPLES.items():
            sample_result: dict[str, Any] = {
                "sample": sample,
                "url": url,
                "inspect": None,
                "qualities": [],
            }
            try:
                inspection = _inspect_local(url)
                sample_result["inspect"] = inspection
                quality_ids = [
                    str(item.get("id") or "")
                    for item in inspection.get("formats", [])
                    if str(item.get("id") or "")
                ]
                quality_ids.append("audio")
                seen: set[str] = set()
                for quality in quality_ids:
                    if quality in seen:
                        continue
                    seen.add(quality)
                    sample_result["qualities"].append(_run_quality(sample, quality))
            except Exception as exc:  # noqa: BLE001
                sample_result["error"] = f"{type(exc).__name__}: {str(exc)[:800]}"
            results.append(sample_result)
            with _STATE_LOCK:
                _STATE["results"] = list(results)
        overall = all(
            not item.get("error")
            and item.get("inspect")
            and item.get("qualities")
            and all(job.get("status") == "ready" for job in item.get("qualities", []))
            for item in results
        )
        with _STATE_LOCK:
            _STATE.update(
                status="passed" if overall else "failed",
                finished_at=int(time.time()),
                results=results,
            )
    except Exception as exc:  # noqa: BLE001
        with _STATE_LOCK:
            _STATE.update(
                status="failed",
                finished_at=int(time.time()),
                error=f"{type(exc).__name__}: {str(exc)[:800]}",
            )


async def _start_background_acceptance() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    asyncio.create_task(asyncio.to_thread(_run_acceptance))


def _install() -> None:
    paths = {getattr(route, "path", "") for route in app.router.routes}
    if f"{_PREFIX}/status" in paths:
        return

    @app.get(f"{_PREFIX}/status")
    def acceptance_status() -> dict[str, Any]:
        with _STATE_LOCK:
            return json.loads(json.dumps(_STATE, ensure_ascii=False))

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

    app.router.add_event_handler("startup", _start_background_acceptance)


_install()

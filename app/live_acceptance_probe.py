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


def _sample_name(url: str) -> str:
    for name, sample_url in _SAMPLES.items():
        if url == sample_url:
            return name
    return "unknown"


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
            raise RuntimeError("Acceptance job not found")
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


def _publish(results: list[dict[str, Any]]) -> None:
    with _STATE_LOCK:
        _STATE["results"] = json.loads(json.dumps(results, ensure_ascii=False))


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
        "sample": sample,
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
            results.append(sample_result)
            _publish(results)
            try:
                inspection = _inspect_local(url)
                sample_result["inspect"] = inspection
                _publish(results)
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
                    _publish(results)
            except Exception as exc:  # noqa: BLE001
                sample_result["error"] = f"{type(exc).__name__}: {str(exc)[:800]}"
                _publish(results)
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


def _summary() -> dict[str, Any]:
    with _STATE_LOCK:
        state = json.loads(json.dumps(_STATE, ensure_ascii=False))
    samples: list[dict[str, Any]] = []
    for item in state.get("results", []):
        qualities = []
        for job in item.get("qualities", []):
            probe = job.get("probe") or {}
            qualities.append({
                "format_id": job.get("format_id"),
                "status": job.get("status"),
                "error": job.get("error"),
                "bytes": probe.get("bytes"),
                "has_video": probe.get("has_video"),
                "has_audio": probe.get("has_audio"),
                "height": probe.get("height"),
                "probe_error": job.get("probe_error"),
            })
        inspect = item.get("inspect") or {}
        samples.append({
            "sample": item.get("sample"),
            "inspect_ok": bool(item.get("inspect")) and not item.get("error"),
            "inspect_source": inspect.get("source"),
            "formats": [str(value.get("id")) for value in inspect.get("formats", [])],
            "error": item.get("error"),
            "qualities": qualities,
        })
    return {
        "status": state.get("status"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
        "samples": samples,
    }


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

    @app.get(f"{_PREFIX}/summary")
    def acceptance_summary() -> dict[str, Any]:
        return _summary()

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

    app.router.add_event_handler("startup", _start_background_acceptance)


_install()

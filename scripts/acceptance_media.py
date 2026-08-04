from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


ACCEPTANCE_URLS = [
    "https://vm.tiktok.com/ZGdxjnte8/",
    "https://www.instagram.com/reel/DbkuNu8oMA5/?igsh=MWVuMjJlOWszdHQzMw==",
    "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce",
    "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu",
]
_TERMINAL = frozenset({"ready", "failed"})


def planned_quality_keys(
    formats: list[dict[str, Any]] | None,
    *,
    include_audio: bool,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in formats or []:
        key = str(item.get("id") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    if "best" not in seen:
        result.insert(0, "best")
    if include_audio and "audio" not in seen:
        result.append("audio")
    return result


def classify_api_failure(message: str) -> str:
    lowered = str(message or "").lower()
    for value in (
        "youtube_circuit_open",
        "youtube_bot_check",
        "youtube_login_required",
        "youtube_fragment_forbidden",
    ):
        if value in lowered:
            return value
    if "youtube_rate_limited" in lowered or "429" in lowered or "too many requests" in lowered:
        return "youtube_rate_limited"
    return "unclassified"


def platform_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "tiktok" in host:
        return "tiktok"
    if "instagram" in host:
        return "instagram"
    if host == "youtu.be" or "youtube" in host:
        return "youtube"
    return host or "unknown"


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"HTTP {response.status_code}: invalid JSON response") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"HTTP {response.status_code}: invalid object response")
    if response.status_code >= 400:
        raise RuntimeError(str(value.get("detail") or value.get("error") or response.status_code))
    return value


def inspect_url(client: httpx.Client, base_url: str, url: str) -> dict[str, Any]:
    response = client.post(
        f"{base_url.rstrip('/')}/api/inspect",
        json={"url": url},
    )
    return _json(response)


def create_job(
    client: httpx.Client,
    base_url: str,
    url: str,
    quality: str,
) -> dict[str, Any]:
    response = client.post(
        f"{base_url.rstrip('/')}/api/jobs",
        json={"url": url, "format_id": quality},
    )
    return _json(response)


def wait_for_job(
    client: httpx.Client,
    base_url: str,
    job_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, timeout_seconds)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"{base_url.rstrip('/')}/api/jobs/{job_id}",
            headers={"cache-control": "no-cache"},
        )
        last = _json(response)
        if str(last.get("status")) in _TERMINAL:
            return last
        time.sleep(2)
    raise TimeoutError(f"job {job_id} did not finish: {last.get('status')}")


def download_file(client: httpx.Client, url: str, target: Path) -> int:
    with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        total = 0
        with target.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                handle.write(chunk)
                total += len(chunk)
    return total


def probe_media(path: Path) -> dict[str, Any]:
    binary = shutil.which("ffprobe")
    if not binary:
        raise RuntimeError("ffprobe is required for live acceptance")
    completed = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams") or []
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    heights = [int(item.get("height") or 0) for item in videos]
    duration = float((data.get("format") or {}).get("duration") or 0)
    return {
        "has_video": bool(videos),
        "has_audio": bool(audios),
        "height": max(heights, default=0),
        "duration": duration,
    }


def verify_media(quality: str, probe: dict[str, Any]) -> None:
    if quality == "audio":
        if not probe["has_audio"]:
            raise AssertionError("audio output has no audio stream")
        return
    if not probe["has_video"] or not probe["has_audio"]:
        raise AssertionError("video output must contain video and audio streams")
    if quality.isdigit() and int(probe["height"]) != int(quality):
        raise AssertionError(
            f"quality button {quality}p produced {probe['height']}p"
        )


def run_acceptance(
    *,
    base_url: str,
    urls: list[str],
    download: bool,
    include_audio: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "base_url": base_url.rstrip("/"),
        "started_at": int(time.time()),
        "download_mode": bool(download),
        "results": [],
    }
    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        for source_url in urls:
            entry: dict[str, Any] = {
                "url": source_url,
                "platform": platform_name(source_url),
                "inspect": None,
                "qualities": [],
            }
            report["results"].append(entry)
            try:
                inspected = inspect_url(client, base_url, source_url)
                formats = inspected.get("formats") or []
                entry["inspect"] = {
                    "ok": True,
                    "title": inspected.get("title"),
                    "duration": inspected.get("duration"),
                    "formats": formats,
                }
            except Exception as exc:  # noqa: BLE001
                message = str(exc)[:300]
                entry["inspect"] = {
                    "ok": False,
                    "error": message,
                    "classification": classify_api_failure(message),
                }
                continue

            if not download:
                continue

            for quality in planned_quality_keys(formats, include_audio=include_audio):
                started = time.monotonic()
                result: dict[str, Any] = {
                    "requested_quality": quality,
                    "ok": False,
                }
                entry["qualities"].append(result)
                try:
                    initial = create_job(client, base_url, source_url, quality)
                    job = initial
                    if str(initial.get("status")) not in _TERMINAL:
                        job = wait_for_job(
                            client,
                            base_url,
                            str(initial["id"]),
                            timeout_seconds=timeout_seconds,
                        )
                    if str(job.get("status")) != "ready":
                        message = str(job.get("error") or "media job failed")
                        raise RuntimeError(message)
                    download_url = str(job.get("download_url") or "")
                    if not download_url:
                        raise RuntimeError("ready job has no download URL")
                    with tempfile.TemporaryDirectory(prefix="yj-acceptance-") as temporary:
                        target = Path(temporary) / "media-output"
                        byte_count = download_file(client, download_url, target)
                        probe = probe_media(target)
                        verify_media(quality, probe)
                    result.update(
                        ok=True,
                        bytes=byte_count,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        **probe,
                    )
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)[:300]
                    result.update(
                        error=message,
                        classification=classify_api_failure(message),
                        elapsed_seconds=round(time.monotonic() - started, 3),
                    )
    report["finished_at"] = int(time.time())
    report["ok"] = all(
        item.get("inspect", {}).get("ok")
        and all(quality.get("ok") for quality in item.get("qualities", []))
        for item in report["results"]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Live YJDownloadVideo acceptance gate")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--without-audio", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("urls", nargs="*")
    args = parser.parse_args()

    report = run_acceptance(
        base_url=args.base_url,
        urls=args.urls or ACCEPTANCE_URLS,
        download=not args.inspect_only,
        include_audio=not args.without_audio,
        timeout_seconds=args.timeout,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

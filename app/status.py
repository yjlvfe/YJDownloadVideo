import json
from pathlib import Path
from .config import settings


def write_status(job_id: str, stage: str, pct: int = 0, size: int = 0,
                 message: str = "") -> None:
    """يكتب حالة التقدم للمهمة في data/status/<job_id>.json — يقرأها البوت ليرسل تحديثات حية."""
    try:
        p = settings.status_path / f"{job_id}.json"
        p.write_text(json.dumps({
            "stage": stage,        # downloading | preparing | uploading | done | failed
            "pct": int(pct),
            "size": int(size),
            "message": message,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def read_status(job_id: str) -> dict | None:
    try:
        p = settings.status_path / f"{job_id}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def clear_status(job_id: str) -> None:
    try:
        (settings.status_path / f"{job_id}.json").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def load_user_prefs() -> dict:
    """تفضيلات المستخدمين (وضع صوت/فيديو): data/user_prefs.json"""
    try:
        p = Path(settings.status_dir).parent / "user_prefs.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_user_prefs(prefs: dict) -> None:
    try:
        p = Path(settings.status_dir).parent / "user_prefs.json"
        p.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

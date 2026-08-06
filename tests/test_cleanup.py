"""اختبارات تنظيف المهام: الفاشلة/العالقة تُحذف بملفاتها — والجديدة الحية لا تُمَس."""
import os
from datetime import timedelta
from pathlib import Path

from app.config import settings
from app.cleanup import cleanup_once
from app.db import init_db, SessionLocal, Job, utcnow


def _add_job(jid: str, status: str, created_ago_h: float, expires_ago_h=None):
    with SessionLocal() as db:
        db.add(Job(
            id=jid, url="https://example.com/v", status=status, owner_key="t",
            created_at=utcnow() - timedelta(hours=created_ago_h),
            expires_at=(utcnow() - timedelta(hours=expires_ago_h)) if expires_ago_h else None,
        ))
        db.commit()


def _make_artifacts(jid: str) -> Path:
    folder = Path(settings.storage_path) / jid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "video.mp4").write_bytes(b"fake-video")
    (Path(settings.status_path) / f"{jid}.json").write_text('{"stage":"failed"}', encoding="utf-8")
    return folder


def test_cleanup_removes_old_failed_with_files():
    """الفاشلة (أقدم من ساعة): صف + مجلد + ملف حالة يُحذفون — كانوا يتسربون للأبد."""
    init_db()
    jid = "oldfail-1"
    _add_job(jid, "failed", created_ago_h=2)
    folder = _make_artifacts(jid)

    cleanup_once()

    with SessionLocal() as db:
        assert db.get(Job, jid) is None
    assert not folder.exists()
    assert not (Path(settings.status_dir) / f"{jid}.json").exists()


def test_cleanup_removes_stuck_downloading():
    """عالقة في downloading (أقدم من 6 ساعات — عامل مات): تُحذف."""
    init_db()
    jid = "stuckdl-1"
    _add_job(jid, "downloading", created_ago_h=8)
    folder = _make_artifacts(jid)

    cleanup_once()

    with SessionLocal() as db:
        assert db.get(Job, jid) is None
    assert not folder.exists()


def test_cleanup_keeps_fresh_ready():
    """مهمة جاهزة حديثة (غير منتهية): لا تُمَس."""
    init_db()
    jid = "fresh-ready-1"
    with SessionLocal() as db:
        db.add(Job(
            id=jid, url="https://example.com/v", status="ready", owner_key="t",
            created_at=utcnow(), expires_at=utcnow() + timedelta(hours=23),
        ))
        db.commit()

    cleanup_once()

    with SessionLocal() as db:
        j = db.get(Job, jid)
        assert j is not None and j.status == "ready"


def test_cleanup_keeps_recent_failed():
    """فاشلة حديثة (أقل من ساعة): تبقى حتى يراها البوت/المستخدم."""
    init_db()
    jid = "fresh-fail-1"
    _add_job(jid, "failed", created_ago_h=0.1)

    cleanup_once()

    with SessionLocal() as db:
        assert db.get(Job, jid) is not None

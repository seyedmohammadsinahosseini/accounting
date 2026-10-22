"""آزمون پشتیبان‌گیری و بازگرداندن — تمرین واقعی، نه روی کاغذ (لایه ۱۳)."""
from __future__ import annotations

import hashlib

import pytest
from cryptography.fernet import Fernet

from app import backup, dates as jd, services as svc
from app.db import session_scope
from app.models import JournalEntry, User
from sqlalchemy import select


def _count_entries() -> int:
    with session_scope() as sess:
        return len(list(sess.execute(select(JournalEntry)).scalars()))


def _make_entry(desc: str) -> int:
    with session_scope() as sess:
        acc = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
        rent = svc.account_by_code(sess, "5103")
        bank = svc.account_by_code(sess, "110201")
        e = svc.create_draft(sess, acc, jd.jtoday(), desc, [
            {"account_id": rent.id, "debit": 1_000_000, "credit": 0},
            {"account_id": bank.id, "debit": 0, "credit": 1_000_000},
        ])
        svc.post_entry(sess, acc, e.id)
        return e.id


def test_backup_then_restore_returns_to_same_state():
    before_count = _count_entries()
    path = backup.make_backup("test")
    assert path.exists() and path.stat().st_size > 0

    _make_entry("سندی که بعد از پشتیبان‌گیری ثبت شد")
    assert _count_entries() == before_count + 1

    backup.restore(path)
    assert _count_entries() == before_count, "بعد از بازگرداندن باید همان وضعیت پشتیبان برگردد"

    listing = backup.list_backups()
    assert any(b["file"] == path.name for b in listing)
    assert all("jalali" in b for b in listing)


def test_prune_keeps_limited_number_of_backups():
    from app import config

    original = config.BACKUP_KEEP
    try:
        config.BACKUP_KEEP = 3
        for _ in range(5):
            backup.make_backup("many")
        assert len(list(config.BACKUP_DIR.glob("accounting-*.db"))) <= 3
    finally:
        config.BACKUP_KEEP = original


def test_backup_checksum_and_database_integrity_are_verified():
    path = backup.make_backup("verify")
    details = backup.verify_backup(path)
    assert details["integrity"] == "ok"
    assert details["checksum_ok"] is True
    assert details["foreign_key_errors"] == 0

    with path.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError):
        backup.verify_backup(path)


def test_backup_rejects_a_wrong_encryption_key_even_with_updated_checksum():
    path = backup.make_backup("wrong-key")
    key_path = path.with_suffix(".key")
    checksum_path = path.with_suffix(".sha256")
    try:
        key_path.write_bytes(Fernet.generate_key())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_path.write_text(f"{digest}  {path.name}\\n", encoding="ascii")
        with pytest.raises(ValueError, match="کلید|رمز"):
            backup.verify_backup(path)
    finally:
        path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)


def test_storage_health_and_manual_backup_are_admin_only(client, admin, manager):
    health = client.get("/api/admin/storage-health", headers=admin)
    assert health.status_code == 200, health.text
    assert health.json()["database"]["integrity"] == "ok"
    assert client.get("/api/admin/storage-health", headers=manager).status_code == 403

    created = client.post("/api/admin/backups", headers=admin)
    assert created.status_code == 200, created.text
    assert created.json()["integrity"] == "ok"
    rows = client.get("/api/admin/backups", headers=admin).json()["rows"]
    assert any(row["file"] == created.json()["file"] for row in rows)

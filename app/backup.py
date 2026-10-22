"""پشتیبان‌گیری، اعتبارسنجی و بازگرداندن امن پایگاه داده.

هر پشتیبان شامل snapshot سازگار SQLite، checksum مستقل و در صورت وجود کلید رمزنگاری
اطلاعات مسافران نیز همان کلید است. فایل ناقص یا خراب پیش از جایگزینی پایگاه زنده رد می‌شود.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from . import config
from .dates import jalali_str

_REQUIRED_TABLES = {"users", "accounts", "journal_entries", "journal_lines"}
_REQUIRED_COLUMNS = {
    "users": {"id", "username", "password_hash"},
    "accounts": {"id", "code", "name", "kind", "nature"},
    "journal_entries": {"id", "entry_no", "entry_date", "status"},
    "journal_lines": {"id", "entry_id", "account_id", "debit_rial", "credit_rial"},
}


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)


def verify_encryption_pair(database: str | Path, key_file: str | Path) -> dict:
    """تمام فیلدهای رمزشده‌ی مسافر را با کلید همراه باز می‌کند تا کلید اشتباه/فایل خراب رد شود."""
    database, key_file = Path(database), Path(key_file)
    conn = _readonly_connection(database)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "passengers" not in tables:
            return {"encrypted_values": 0}
        values = []
        for national_id, passport_no in conn.execute(
                "SELECT national_id, passport_no FROM passengers"):
            for value in (national_id or "", passport_no or ""):
                if value.startswith("enc:v1:"):
                    values.append(value[len("enc:v1:"):])
        if not values:
            return {"encrypted_values": 0}
        if not key_file.is_file():
            raise ValueError("داده‌ی مسافر رمز‌شده است اما فایل کلید موجود نیست.")
        try:
            from cryptography.fernet import Fernet
            cipher = Fernet(key_file.read_bytes().strip())
            for token in values:
                cipher.decrypt(token.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 — invalid/wrong key or malformed Fernet value
            raise ValueError("کلید همراه با پایگاه داده سازگار نیست یا یکی از اطلاعات رمز‌شده خراب است.") from exc
        return {"encrypted_values": len(values)}
    finally:
        conn.close()


def verify_database(path: str | Path) -> dict:
    """سلامت SQLite، جدول‌های کلیدی و یکپارچگی کلیدهای خارجی را بررسی می‌کند."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("فایل پایگاه داده وجود ندارد یا خالی است.")
    conn = _readonly_connection(path)
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise ValueError("آزمون integrity_check پایگاه داده ناموفق بود: " + "; ".join(integrity[:5]))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise ValueError("جدول‌های اصلی در فایل نیستند: " + ", ".join(missing))
        for table, expected_columns in _REQUIRED_COLUMNS.items():
            actual_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing_columns = sorted(expected_columns - actual_columns)
            if missing_columns:
                raise ValueError(f"ستون‌های لازم در جدول {table} نیستند: " + ", ".join(missing_columns))
        foreign_key_errors = list(conn.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise ValueError(f"{len(foreign_key_errors)} پیوند ناسالم بین جدول‌ها پیدا شد.")
        unbalanced = conn.execute(
            "SELECT COUNT(*) FROM (SELECT e.id FROM journal_entries e "
            "LEFT JOIN journal_lines l ON l.entry_id=e.id WHERE e.status='posted' "
            "GROUP BY e.id HAVING COUNT(l.id)<2 OR COALESCE(SUM(l.debit_rial),0)<>COALESCE(SUM(l.credit_rial),0))"
        ).fetchone()[0]
        if unbalanced:
            raise ValueError(f"{unbalanced} سند ثبت‌نهایی‌شده دست‌کم دو سطرِ متوازن ندارد.")
        posted_count = conn.execute("SELECT COUNT(*) FROM journal_entries WHERE status='posted'").fetchone()[0]
        return {"ok": True, "size_bytes": path.stat().st_size,
                "tables": len(tables), "foreign_key_errors": 0,
                "posted_entries_checked": posted_count, "integrity": "ok"}
    finally:
        conn.close()


def _sidecar(path: Path, suffix: str) -> Path:
    return path.with_suffix(suffix)


def _write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksum(path: Path) -> None:
    digest = _checksum(path)
    _write_atomic(_sidecar(path, ".sha256"), f"{digest}  {path.name}\n".encode("ascii"))


def _copy_key(path: Path) -> bool:
    source = config.DATA_DIR / "secret.key"
    if not source.is_file():
        return False
    _write_atomic(_sidecar(path, ".key"), source.read_bytes())
    return True


def make_backup(tag: str = "auto") -> Path:
    """SQLite backup API را به کار می‌گیرد؛ فایل نهایی فقط پس از بررسی اتمی منتشر می‌شود."""
    config.ensure_dirs()
    if not config.DB_PATH.is_file():
        raise FileNotFoundError(f"پایگاه داده پیدا نشد: {config.DB_PATH}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_tag = "".join(ch for ch in str(tag) if ch.isalnum() or ch in "-_-")[:30] or "auto"
    target = config.BACKUP_DIR / f"accounting-{safe_tag}-{stamp}.db"
    fd, temp_name = tempfile.mkstemp(prefix=".accounting-backup-", suffix=".tmp", dir=config.BACKUP_DIR)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        src = sqlite3.connect(str(config.DB_PATH), timeout=30)
        dst = sqlite3.connect(str(temp_path), timeout=30)
        try:
            src.backup(dst, pages=256, sleep=0.01)
        finally:
            dst.close()
            src.close()
        if os.name == "posix":
            temp_path.chmod(0o600)
        os.replace(temp_path, target)
        _copy_key(target)
        _write_checksum(target)
        verify_backup(target)
        prune()
        return target
    except Exception:
        temp_path.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        _sidecar(target, ".key").unlink(missing_ok=True)
        _sidecar(target, ".sha256").unlink(missing_ok=True)
        raise


def prune() -> None:
    files = sorted(config.BACKUP_DIR.glob("accounting-*.db"))
    for old in files[: max(0, len(files) - config.BACKUP_KEEP)]:
        old.unlink(missing_ok=True)
        _sidecar(old, ".key").unlink(missing_ok=True)
        _sidecar(old, ".sha256").unlink(missing_ok=True)


def _checksum_ok(path: Path) -> bool | None:
    sidecar = _sidecar(path, ".sha256")
    if not sidecar.exists():
        return None
    try:
        expected = sidecar.read_text(encoding="ascii").split()[0]
    except (OSError, UnicodeError, IndexError):
        return False
    return _checksum(path) == expected


def verify_backup(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = config.BACKUP_DIR / path
    details = verify_database(path)
    checksum_ok = _checksum_ok(path)
    if checksum_ok is None:
        raise ValueError("فایل checksum پشتیبان وجود ندارد؛ کامل‌بودن نسخه تأیید نمی‌شود.")
    if checksum_ok is False:
        raise ValueError("checksum فایل پشتیبان با محتوای آن یکی نیست؛ فایل دست‌کاری یا ناقص شده است.")
    key_path = _sidecar(path, ".key")
    encryption = verify_encryption_pair(path, key_path)
    needs_key = encryption["encrypted_values"] > 0
    return {**details, "checksum_ok": checksum_ok, "contains_key": key_path.is_file(),
            "needs_key": needs_key, "encrypted_values_checked": encryption["encrypted_values"]}


def list_backups() -> list[dict]:
    config.ensure_dirs()
    out = []
    for f in sorted(config.BACKUP_DIR.glob("accounting-*.db"), reverse=True):
        stat = f.stat()
        when = datetime.fromtimestamp(stat.st_mtime)
        checksum_file = _sidecar(f, ".sha256")
        key_file = _sidecar(f, ".key")
        out.append({"file": f.name, "size_kb": round(stat.st_size / 1024, 1),
                    "jalali": jalali_str(when.date()), "at": when.strftime("%H:%M:%S"),
                    "checksum_ok": None, "checksum_present": checksum_file.is_file(),
                    "contains_key": key_file.is_file()})
    return out


def seconds_since_last_backup() -> float | None:
    files = sorted(config.BACKUP_DIR.glob("accounting-*.db"))
    if not files:
        return None
    return datetime.now().timestamp() - files[-1].stat().st_mtime


def restore(backup_file: str | Path) -> Path:
    """بررسی کامل، نسخه‌گرفتن از وضعیت فعلی، سپس جایگزینی اتمی فایل زنده و کلید."""
    src = Path(backup_file)
    if not src.is_absolute():
        src = config.BACKUP_DIR / src
    if not src.exists():
        raise FileNotFoundError(f"فایل پشتیبان پیدا نشد: {src}")
    verified = verify_backup(src)
    if config.DB_PATH.is_file():
        make_backup(tag="before-restore")

    from .db import engine
    engine.dispose()
    config.ensure_dirs()
    fd, temp_name = tempfile.mkstemp(prefix=".restore-", suffix=".db", dir=config.DB_PATH.parent)
    os.close(fd)
    temp_db = Path(temp_name)
    try:
        shutil.copy2(src, temp_db)
        verify_database(temp_db)
        if os.name == "posix":
            temp_db.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(temp_db, config.DB_PATH)
        key_src = _sidecar(src, ".key")
        if key_src.is_file():
            key_target = config.DATA_DIR / "secret.key"
            _write_atomic(key_target, key_src.read_bytes())
        elif verified["needs_key"]:
            raise ValueError("کلید لازم برای بازکردن اطلاعات این پشتیبان موجود نیست.")
        engine.dispose()
        return config.DB_PATH
    except Exception:
        temp_db.unlink(missing_ok=True)
        engine.dispose()
        raise

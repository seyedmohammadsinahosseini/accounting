"""اتصال به محل نگهداری اطلاعات + راه‌اندازی اولیه + لاگ ساختاریافته.

لاگ‌ها ساختاریافته‌اند (JSON) و هیچ رمز، توکن یا اطلاعات شخصی در آن‌ها نوشته نمی‌شود.
"""
from __future__ import annotations

import json
import logging
import os
import logging.handlers
import uuid
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

config.ensure_dirs()

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - زیرساختی
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=FULL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    config.ensure_dirs()
    Base.metadata.create_all(engine)
    if os.name == "posix" and config.DB_PATH.exists():
        try:
            config.DB_PATH.chmod(0o600)
        except OSError:
            pass


@contextmanager
def session_scope():
    s: OrmSession = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---------------------------------------------------------------- لاگ و هشدار
def _json_formatter(record: logging.LogRecord) -> str:
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key in ("event", "tracking_id", "path", "method", "status", "user", "ip", "extra"):
        value = getattr(record, key, None)
        if value is not None:
            payload[key] = value
    if record.exc_info:
        payload["error"] = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"
    return json.dumps(payload, ensure_ascii=False)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _json_formatter(record)


def _make_logger(name: str, filename: str, level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / filename, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(stream)
    logger.propagate = False
    return logger


app_log = _make_logger("acct.app", "app.log")
alert_log = _make_logger("acct.alerts", "alerts.log", logging.WARNING)


def new_tracking_id() -> str:
    return "ERR-" + uuid.uuid4().hex[:10].upper()

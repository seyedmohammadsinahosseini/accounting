"""نقطه‌ی شروع برنامه: صفحه‌ها را سرو می‌کند و پشتیبان‌گیری خودکار را اجرا می‌کند."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import backup, config, vault
from .api import app as api_app
from .db import alert_log, app_log, init_db
from .migrations import run_migrations
from .seed import bootstrap

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _backup_loop() -> None:
    while True:
        try:
            if config.BACKUP_ENABLED:
                age = backup.seconds_since_last_backup()
                if age is None or age > config.BACKUP_EVERY_HOURS * 3600:
                    path = backup.make_backup("auto")
                    app_log.info("پشتیبان‌گیری انجام شد",
                                 extra={"event": "backup", "extra": path.name})
        except Exception as exc:  # noqa: BLE001
            alert_log.error("پشتیبان‌گیری ناموفق بود", exc_info=exc, extra={"event": "backup_failed"})
        await asyncio.sleep(1800)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    if not vault.crypto_available() and not config.ALLOW_PLAINTEXT_PII:
        raise RuntimeError("کتابخانه‌ی رمزنگاری نصب نیست؛ برای حفاظت از اطلاعات مسافران، برنامه متوقف شد.")
    init_db()                       # جدول‌های تازه (اگر لازم باشد)
    run_migrations()                # ستون‌های تازه روی اطلاعات موجود — فقط رو به جلو
    creds = bootstrap(demo=config.DEMO_SEED)
    if creds:
        app_log.info("کاربران اولیه ساخته شدند", extra={"event": "bootstrap", "user": "system"})
    task = asyncio.create_task(_backup_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


api_app.router.lifespan_context = lifespan
app: FastAPI = api_app

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")

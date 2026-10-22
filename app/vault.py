"""صندوق رمز — رمزنگاری اطلاعات هویتی مسافران (فاز ۲ تکمیلی).

چه چیزی و چرا:
شماره‌ی گذرنامه، کد ملی مسافر و تاریخ انقضای گذرنامه، اطلاعات شخصی حساس‌اند. این‌ها
هیچ‌وقت ساده در فایل داده نوشته نمی‌شوند؛ با کلید رمزنگاری (AES از کتابخانه‌ی معروف
`cryptography`) رمز می‌شوند. در فهرست‌ها هم فقط بخش ماسک‌شده‌ی شماره دیده می‌شود و
دیدن کامل، دسترسی مخصوص دارد و در «ردّ کار» ثبت می‌شود.

صادقانه درباره‌ی حد این محافظت:
- اگر کسی فقط فایل `data/accounting.db` را بردارد (پشتیبان، لپ‌تاپ گم‌شده)، شماره‌ها خوانا نیست.
- ولی کلید در همان پوشه‌ی `data` است. اگر کسی *کل پوشه* را بردارد، می‌تواند رمزگشایی کند.
  برای گذاشتن برنامه روی اینترنت، همان لایه‌های دیگر (رمز ورود، HTTPS، دسترسی نقش‌محور) لازم است.
- در استقرار معمول، نبودن کتابخانه‌ی رمزنگاری جلوی راه‌اندازی را می‌گیرد؛ نگهداری متن
  ساده فقط با تنظیم صریحِ ACCT_ALLOW_PLAINTEXT_PII=1 و برای آزمون/انتقال اضطراری ممکن است.
"""
from __future__ import annotations

import pathlib
import stat
from datetime import date

from . import config

PREFIX = "enc:v1:"
PLAIN_PREFIX = "plain:"

try:                                      # نصب معمولی: با ابزار رمزنگاری کامل
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore

    CRYPTO_OK = True
    _CRYPTO_ERROR = ""
except Exception as exc:                  # noqa: BLE001 — هر خطای نصب
    Fernet = None                          # type: ignore[assignment]
    InvalidToken = Exception               # type: ignore[assignment]
    CRYPTO_OK = False
    _CRYPTO_ERROR = str(exc)


def key_path() -> pathlib.Path:
    return pathlib.Path(config.DATA_DIR) / "secret.key"


def crypto_available() -> bool:
    return CRYPTO_OK


def crypto_problem() -> str:
    return _CRYPTO_ERROR


def _load_key(create: bool = True) -> bytes | None:
    path = key_path()
    if path.exists():
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return path.read_bytes().strip()
    if not create:
        return None
    key = Fernet.generate_key()            # type: ignore[union-attr]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:                                   # فقط خودِ کاربر سیستم بتواند بخواند
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


def encrypt(value: str | None) -> str:
    """متن را رمز می‌کند؛ در نبود وابستگی رمزنگاری، پیش‌فرض fail-closed است."""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if not CRYPTO_OK:
        if not config.ALLOW_PLAINTEXT_PII:
            raise RuntimeError("رمزنگاری اطلاعات هویتی در دسترس نیست؛ کتابخانه‌ی cryptography را نصب کنید.")
        return PLAIN_PREFIX + text
    token = Fernet(_load_key()).encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value: str | None) -> str:
    text = value or ""
    if not text:
        return ""
    if text.startswith(PLAIN_PREFIX):
        return text[len(PLAIN_PREFIX):]
    if not text.startswith(PREFIX):
        return text                       # داده‌ی قدیمی/ساده
    if not CRYPTO_OK:
        return ""                          # بدون کلید نمی‌شود خواند
    try:
        return Fernet(_load_key(create=False)).decrypt(text[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def stored_plain(value: str | None) -> bool:
    """آیا اطلاعات بدون رمز ذخیره شده است؟ (برای نشان دادن هشدار صادقانه)"""
    return bool(value) and str(value).startswith(PLAIN_PREFIX)


def mask(value: str | None, keep_start: int = 2, keep_end: int = 2) -> str:
    """شماره را ماسک می‌کند: چند رقم اول و آخر بماند، بقیه نقطه.

    مثال: گذرنامه‌ی «X1234567» → «X1•••67» و کد ملی «0012345678» → «00••••••78».
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if len(text) <= keep_start + keep_end:
        return "•" * len(text)
    middle = len(text) - keep_start - keep_end
    return f"{text[:keep_start]}{'•' * middle}{text[-keep_end:]}"


def expiry_state(expiry: date | None) -> str:
    """وضعیت گذرنامه: سالم، نزدیک به انقضا (کمتر از ۶ ماه)، یا منقضی."""
    if expiry is None:
        return "unknown"
    today = date.today()
    if expiry < today:
        return "expired"
    months = (expiry - today).days / 30.44
    return "soon" if months < 6 else "ok"


EXPIRY_LABELS = {"ok": "معتبر", "soon": "کمتر از ۶ ماه مانده", "expired": "منقضی شده",
                 "unknown": "ثبت نشده"}

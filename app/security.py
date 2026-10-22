"""ورود کاربران، رمزها و سطوح دسترسی.

رمزها هرگز به شکل خوانا ذخیره نمی‌شوند؛ با روش استاندارد PBKDF2 و «نمک» مخصوص هر کاربر
ذخیره می‌شوند. توکن ورود هم به شکل هش ذخیره می‌شود تا اگر روزی کسی فایل داده‌ها را دید،
نتواند با آن وارد حساب کسی شود.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

PBKDF2_ROUNDS = 240_000

ROLE_LABELS = {
    "admin": "مدیر سیستم",
    "manager": "مدیر آژانس",
    "accountant": "حسابدار",
    "employee": "کارمند",
    "auditor": "حسابرس (فقط خواندن)",
}

_READ_ONLY = {
    "dashboard.view", "journal.view", "cash.view", "accounts.view",
    "reports.view", "fiscal.view", "audit.view", "assets.view", "moadian.view",
}

# اطلاعات هویتی مسافران (گذرنامه/کد ملی) — جدا از بقیه‌ی دسترسی‌ها کنترل می‌شود:
#  • passengers.view      ← دیدن فهرست مسافران (ماسک‌شده)
#  • passengers.view_full ← دیدن شماره‌ی کامل (هر بار در «ردّ کار» ثبت می‌شود)
#  • passengers.manage    ← افزودن و ویرایش مسافر
#  • passengers.purge     ← پاک‌کردن اطلاعات هویتی بعد از پایان سفر
_CONTACT = {"passengers.view", "passengers.manage"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "manager": _READ_ONLY | _CONTACT | {"passengers.view_full", "passengers.purge",
                                        "journal.create", "journal.view_all", "payroll.view"},
    "accountant": _READ_ONLY | {
        "journal.create", "journal.view_all", "journal.edit", "journal.post",
        "journal.reverse", "cash.transfer", "accounts.manage", "fiscal.manage",
        "payroll.manage", "payroll.view", "assets.manage", "moadian.manage",
        "passengers.view", "passengers.manage", "passengers.view_full", "passengers.purge",
    },
    # کارمند فروش مسافرها را ثبت می‌کند ولی شماره‌ی کامل را دوباره نمی‌بیند
    # (آن چیزی را که خودش نوشته، ماسک‌شده می‌بیند؛ دیدن کامل کار حسابدار و مدیر است)
    "employee": {"dashboard.view", "journal.create", "journal.view_own", "passengers.view",
                 "passengers.manage"},
    "auditor": _READ_ONLY | {"passengers.view", "passengers.view_full"},
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(hours: int) -> datetime:
    return datetime.now() + timedelta(hours=hours)


def password_problem(password: str) -> str | None:
    """پیام فارسی برای رمز ضعیف، یا None اگر رمز قابل قبول بود."""
    if password is None or len(password) < 10:
        return "رمز باید حداقل ۱۰ نویسه باشد."
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "رمز باید دست‌کم یک حرف و یک رقم داشته باشد."
    common = {"1234567890", "password", "qwerty12345", "1122334455"}
    if password.lower() in common:
        return "این رمز خیلی حدس‌زدنی است؛ رمز دیگری انتخاب کنید."
    return None


def role_has(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms

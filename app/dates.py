"""تاریخ شمسی.

قاعده‌ی داخلی: تاریخ‌ها در جای نگه‌داری به شکل میلادی استاندارد (YYYY-MM-DD) ذخیره
می‌شوند تا مقایسه و بازه‌ها همیشه درست کار کند، ولی همه‌جا به کاربر شمسی نشان داده
می‌شوند. سال و ماه شمسی هم جداگانه ذخیره می‌شود تا بستن دوره و گزارش‌های ماهانه ساده
و سریع باشد.
"""
from __future__ import annotations

from datetime import date, datetime

MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

_G_DAYS_IN_MONTH = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400) + gd + _G_DAYS_IN_MONTH[gm - 1]
    )
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    mdays = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gd > mdays[gm]:
        gd -= mdays[gm]
        gm += 1
    return gy, gm, gd


def is_jalali_leap(jy: int) -> bool:
    return ((jy + 12) % 33) % 4 == 1 or ((jy + 12) % 33) == 1


def jtoday() -> date:
    return date.today()


def to_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def from_iso(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def jalali_parts(d: date) -> tuple[int, int, int]:
    return gregorian_to_jalali(d.year, d.month, d.day)


def jalali_str(d: date) -> str:
    jy, jm, jd = jalali_parts(d)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def jalali_long(d: date) -> str:
    jy, jm, jd = jalali_parts(d)
    return f"{jd} {MONTHS[jm - 1]} {jy}"


def parse_jalali(text: str) -> date:
    """«1404/07/23» یا «۱۴۰۴/۰۷/۲۳» را به تاریخ داخلی تبدیل می‌کند."""
    from .money import fa_to_en_digits

    t = fa_to_en_digits(text.strip()).replace("-", "/").replace(".", "/")
    parts = [p for p in t.split("/") if p != ""]
    if len(parts) != 3:
        raise ValueError("تاریخ را به شکل 1404/07/23 بنویسید.")
    jy, jm, jd = (int(p) for p in parts)
    gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
    return date(gy, gm, gd)


def parse_date_any(text: str | None, default: date | None = None) -> date:
    """هم تاریخ شمسی و هم میلادی را می‌پذیرد. خالی یعنی امروز."""
    if text is None or str(text).strip() == "":
        return default or jtoday()
    t = str(text).strip()
    if len(t.split("-")) == 3 and t.split("-")[0].isdigit() and int(t.split("-")[0]) > 1700:
        return from_iso(t)                       # میلادی مثل 2025-08-01
    return parse_jalali(t)


def fiscal_year_of(d: date) -> tuple[str, date, date]:
    """سال مالی: از ۱ فروردین تا آخر اسفند همان سال شمسی."""
    jy, _, _ = jalali_parts(d)
    start = date(*jalali_to_gregorian(jy, 1, 1))
    end = date(*jalali_to_gregorian(jy + 1, 1, 1)).toordinal() - 1
    return f"{jy}", start, date.fromordinal(end)


def period_of(d: date) -> tuple[int, int]:
    jy, jm, _ = jalali_parts(d)
    return jy, jm


def daterange_month(jy: int, jm: int) -> tuple[date, date]:
    start = date(*jalali_to_gregorian(jy, jm, 1))
    if jm == 12:
        nxt = date(*jalali_to_gregorian(jy + 1, 1, 1))
    else:
        nxt = date(*jalali_to_gregorian(jy, jm + 1, 1))
    return start, date.fromordinal(nxt.toordinal() - 1)

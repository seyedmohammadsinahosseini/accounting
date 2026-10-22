"""پول.

قاعده‌ی داخلی پروژه: همه‌ی مبالغ ریال‌اند و به شکل عدد صحیح ذخیره می‌شوند (بدون اعشار).
با عدد صحیح، جمع‌ها هیچ‌وقت خطای گرد کردن پیدا نمی‌کنند — چیزی که در نرم‌افزار حسابداری
حیاتی است.
"""
from __future__ import annotations

from decimal import Decimal

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_THOUSANDS = {"هزار": 1_000, "میلیون": 1_000_000, "میلیارد": 1_000_000_000, "m": 1_000_000}


class MoneyError(ValueError):
    pass


def fa_to_en_digits(text: str) -> str:
    out = []
    for ch in str(text):
        if ch in FA_DIGITS:
            out.append(str(FA_DIGITS.index(ch)))
        elif ch in AR_DIGITS:
            out.append(str(AR_DIGITS.index(ch)))
        else:
            out.append(ch)
    return "".join(out)


def en_to_fa_digits(text) -> str:
    out = []
    for ch in str(text):
        out.append(FA_DIGITS[int(ch)] if ch.isdigit() else ch)
    return "".join(out)


def parse_amount(value) -> int:
    """هر شکلی از ورودی را به ریال تبدیل می‌کند: 12500000 / «۱۲,۵۰۰,۰۰۰» / «12.5 میلیون»."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise MoneyError("مبلغ خالی است.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, Decimal):
        return int(value)

    text = fa_to_en_digits(str(value)).strip().replace(",", "").replace("٬", "")
    text = text.replace("تومان", "").replace("ریال", "").strip()
    multiplier = 1
    for word, m in _THOUSANDS.items():
        if word in text:
            multiplier = m
            text = text.replace(word, "").strip()
            break
    parts = text.split()
    if len(parts) > 1:
        raise MoneyError("مبلغ را یک‌بار و بدون فاصله بنویسید.")
    if parts[0] == "":
        raise MoneyError("مبلغ را بنویسید.")
    try:
        number = Decimal(parts[0])
    except Exception as exc:  # noqa: BLE001
        raise MoneyError("مبلغ را فقط با عدد بنویسید.") from exc
    if number < 0:
        raise MoneyError("مبلغ نمی‌تواند منفی باشد.")
    amount = int(number * multiplier)
    if amount != number * multiplier:
        raise MoneyError("مبلغ اعشار دارد؛ در این برنامه همه‌ی مبالغ به ریال و بدون اعشار است.")
    return amount


def format_amount(value: int) -> str:
    """12,500,000"""
    return f"{int(value):,}"


def format_amount_fa(value: int) -> str:
    return en_to_fa_digits(f"{int(value):,}")


def format_with_unit(value: int) -> str:
    return f"{format_amount_fa(value)} ریال"
